"""
Bulk-update delivery_time = 39 days for all Online TR-factory products on JV + XL.

Strategy:
  1. Collect Online SKUs for TR factories from local DB (otto_xlsx_import_rows).
  2. Paginate through OTTO GET /v5/products (no SKU filter) and update any SKU
     that is in the TR set and has deliveryTime != 39.

Usage (inside the backend container):
    docker exec -i otto-bot-backend python scripts/update_tr_delivery_time.py

Dry-run (no OTTO calls):
    docker exec -i otto-bot-backend python scripts/update_tr_delivery_time.py --dry-run

Options:
    --dry-run            Log without sending to OTTO.
    --controller jv|xl   Limit to one account.
    --page-limit N        Products per OTTO page (default 100, max 2000).
    --delay S             Seconds between POST requests (default 0.6).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import logging
import sys
import os
import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import func, or_, select

from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.clients.external_otto_client import ExternalOttoClient
from app.database import SessionLocal
from app.models.factories import Factories
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.schemas.external_schemes.until_schemes import GetProductRequest, DeliveryInformationRequest, DeliveryInformation
from app.schemas.enums import Controller
from app.services.afterbuy_service import AfterbuyService
from app.dependencies import get_afterbuy_auth, get_afterbuy_client
from app.normalize_product_to_schema import build_normalized_product
from scripts.tr_factories_list import VALID_TR_FACTORIES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("update_tr_delivery_time")

TR_DELIVERY_TIME = 39

# 4-8 Wochen
SHIPPING_PROFILES = {
    "jv": "786c6468-3baf-52e0-88b5-13757eb7f873",  # (4-8 WOCHEN)
    "xl": "d045a64d-59e6-57fb-9f69-6370dc7c9d40"   # LIEFERZEIT (4-8 WOCHEN)
}  # days -> "4-8 Wochen" on OTTO
CONTROLLERS = ["jv", "xl"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_delivery_time(product_variation: dict, delivery_time: int) -> dict:
    """Return a deep copy of the variation with deliveryTime updated."""
    var = copy.deepcopy(product_variation)
    delivery = var.get("delivery")
    if isinstance(delivery, dict):
        delivery["deliveryTime"] = delivery_time
    else:
        # No delivery block yet — add with just deliveryTime (no type override)
        var["delivery"] = {"deliveryTime": delivery_time}
    return var


def _build_otto_client() -> ExternalOttoClient:
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
    )
    return ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)


# ---------------------------------------------------------------------------
# Step 1: build TR SKU set from local DB + Afterbuy
# ---------------------------------------------------------------------------

async def _collect_tr_skus(controller: str, factory_name_filter: str | None = None) -> set[str]:
    """
    Return the set of SKUs that belong to TR factories for this controller.
    Uses: factories (to find TR factory IDs) + Afterbuy (to get their products) + otto_xlsx_import_rows (Online only).
    """
    otto_account = controller.lower()

    async with SessionLocal() as db:
        # TR factory names -> factory_ids
        query = select(Factories.factory_id, Factories.name).where(
            func.upper(Factories.account) == controller.upper(),
            Factories.name.in_(VALID_TR_FACTORIES),
        )
        
        if factory_name_filter:
            if factory_name_filter.upper() != "ALL":
                query = query.where(Factories.name.ilike(f"%{factory_name_filter}%"))
            
        tr_factory_rows = (await db.execute(query)).all()

    if not tr_factory_rows:
        log.info("controller=%s: 0 TR factories matched", controller.upper())
        return set(), 0

    log.info(
        "controller=%s: %d TR factories matched. Fetching from Afterbuy...",
        controller.upper(), len(tr_factory_rows),
    )

    afterbuy = AfterbuyService(auth=get_afterbuy_auth(), client=get_afterbuy_client())
    
    tr_skus_from_afterbuy: set[str] = set()
    
    # Fetch products for each matching TR factory
    afterbuy_errors = 0
    for row in tr_factory_rows:
        try:
            resp = await afterbuy.get_products_by_factory_id(
                controller=Controller(otto_account),
                factory_id=int(row.factory_id)
            )
            for item in resp.products:
                item_dict = item.model_dump(mode="json", exclude_none=True)
                norm = build_normalized_product(item_dict, None, Controller(otto_account))
                sku = norm.get("sku")
                if sku:
                    tr_skus_from_afterbuy.add(str(sku).strip())
        except Exception as exc:
            afterbuy_errors += 1
            log.warning("Failed to fetch Afterbuy products for factory %s (%s): %s", row.name, row.factory_id, exc)

    log.info("controller=%s: Got %d unique SKUs from Afterbuy for TR factories", controller.upper(), len(tr_skus_from_afterbuy))

    # Intersect with Online SKUs for this account
    async with SessionLocal() as db:
        sku_rows = (
            await db.execute(
                select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean).where(
                    OttoXlsxImportRow.account == otto_account,
                    OttoXlsxImportRow.marketplace_status == "Online",
                )
            )
        ).all()

    all_online: set[str] = set()
    for sku, ean in sku_rows:
        if sku:
            all_online.add(str(sku).strip())
            
    valid_skus = tr_skus_from_afterbuy.intersection(all_online)

    log.info(
        "controller=%s: Intersected with local DB -> %d TR SKUs are Online",
        controller.upper(), len(valid_skus),
    )
    return valid_skus, afterbuy_errors


# ---------------------------------------------------------------------------
# Step 2: paginate OTTO, patch delivery_time for TR SKUs
# ---------------------------------------------------------------------------

async def _retry_api_call(coro_func, *args, delay_sec: float = 0.0, is_post: bool = False):
    """Run an API call with exponential backoff and rate limit handling."""
    delays = [2, 5, 15]
    for attempt in range(4):
        try:
            res = await coro_func(*args)
            if is_post and delay_sec > 0:
                await asyncio.sleep(delay_sec)
            return res
        except Exception as exc:
            if attempt == 3:
                raise exc
            
            wait_time = delays[attempt]
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                if status == 429:
                    wait_time = 30
                elif status in (400, 401, 403, 404):
                    # Do not retry definitive client errors
                    raise exc
                
            log.warning("API Error: %s. Retrying in %ds (attempt %d/3)...", exc, wait_time, attempt + 1)
            await asyncio.sleep(wait_time)


async def _update_controller(
    client: ExternalOttoClient,
    controller: str,
    tr_skus: set[str],
    *,
    delay_sec: float,
    dry_run: bool,
) -> dict[str, int]:
    stats: dict[str, int] = {
        "online_tr_skus": len(tr_skus),
        "otto_found": 0,
        "updated": 0,
        "already_39": 0,
        "not_found": 0,
        "get_error": 0,
        "post_error": 0,
    }

    for sku in tr_skus:
        log.info("controller=%s sku=%s — GET /v5/products", controller.upper(), sku)
        try:
            get_req = GetProductRequest(sku=sku)
            response = await _retry_api_call(client.get_products, get_req, controller, delay_sec=delay_sec)
        except Exception as exc:
            log.error("controller=%s sku=%s GET failed definitively: %s", controller.upper(), sku, exc)
            stats["get_error"] += 1
            continue

        variations = response.get("productVariations") or []
        if not variations:
            log.info("controller=%s sku=%s: not found on OTTO", controller.upper(), sku)
            stats["not_found"] += 1
            continue

        stats["otto_found"] += len(variations)

        for var in variations:
            var_sku = str(var.get("sku") or "").strip()
            if not var_sku:
                continue

            current_delivery = var.get("delivery") or {}
            current_time = current_delivery.get("deliveryTime")

            patched = None
            if current_time == TR_DELIVERY_TIME:
                stats["already_39"] += 1
                # Catalog already ok, but we STILL need to update availability
            else:
                patched = _patch_delivery_time(var, TR_DELIVERY_TIME)

            profile_id = SHIPPING_PROFILES.get(controller.lower())
            if not profile_id:
                log.error("controller=%s has no mapped shipping profile ID!", controller.upper())
                stats["error"] += 1
                continue

            if dry_run:
                log.info(
                    "[DRY-RUN] controller=%s sku=%s deliveryTime %s -> %d, ShippingProfile -> %s",
                    controller.upper(), var_sku, current_time, TR_DELIVERY_TIME, profile_id
                )
                stats["updated"] += 1
                continue

            try:
                if patched:
                    await _retry_api_call(client.create_or_update_product, [patched], controller, delay_sec=delay_sec, is_post=True)
                
                # Update Availability (Shipping Profile and Processing Time)
                del_req = DeliveryInformationRequest(
                    root=[
                        DeliveryInformation(
                            shipping_profile_id=profile_id,
                            processing_time=str(TR_DELIVERY_TIME),
                            sku=var_sku
                        )
                    ]
                )
                await _retry_api_call(client.update_product_delivery, del_req, controller, delay_sec=delay_sec, is_post=True)

                log.info(
                    "controller=%s sku=%s deliveryTime %s -> %d OK (Catalog & Availability updated)",
                    controller.upper(), var_sku, current_time, TR_DELIVERY_TIME,
                )
                stats["updated"] += 1
            except Exception as exc:
                log.error("controller=%s sku=%s POST failed definitively: %s", controller.upper(), var_sku, exc)
                stats["post_error"] += 1

    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    controllers = [args.controller] if args.controller else CONTROLLERS
    client = _build_otto_client()
    total: dict[str, int] = {
        "online_tr_skus": 0,
        "otto_found": 0,
        "updated": 0,
        "already_39": 0,
        "not_found": 0,
        "get_error": 0,
        "post_error": 0,
        "afterbuy_error": 0,
    }

    for controller in controllers:
        log.info("=== Controller: %s ===", controller.upper())

        tr_skus, afterbuy_errs = await _collect_tr_skus(controller, args.factory_name)
        total["afterbuy_error"] += afterbuy_errs
        
        if not tr_skus:
            log.info("controller=%s: no TR SKUs found — skip", controller.upper())
            continue

        log.info("controller=%s: will match against %d TR SKUs", controller.upper(), len(tr_skus))

        stats = await _update_controller(
            client=client,
            controller=controller,
            tr_skus=tr_skus,
            delay_sec=args.delay,
            dry_run=args.dry_run,
        )
        for k in total:
            total[k] += stats.get(k, 0)

        log.info(
            "controller=%s DONE | online_tr_skus=%d otto_found=%d updated=%d already_39=%d not_found=%d get_error=%d post_error=%d afterbuy_error=%d",
            controller.upper(),
            stats["online_tr_skus"], stats["otto_found"], stats["updated"], 
            stats["already_39"], stats["not_found"], stats["get_error"], stats["post_error"], afterbuy_errs,
        )

    log.info(
        "=== TOTAL | online_tr_skus=%d otto_found=%d updated=%d already_39=%d not_found=%d get_error=%d post_error=%d afterbuy_error=%d ===",
        total["online_tr_skus"], total["otto_found"], total["updated"], 
        total["already_39"], total["not_found"], total["get_error"], total["post_error"], total["afterbuy_error"],
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="No OTTO writes")
    parser.add_argument("--controller", choices=["jv", "xl"], default=None)
    parser.add_argument("--factory-name", type=str, default=None, help="Filter by specific factory name")
    parser.add_argument("--delay", type=float, default=0.6, help="Sec between POST calls")
    parsed = parser.parse_args()

    if parsed.dry_run:
        log.info("=== DRY RUN — no OTTO writes ===")

    asyncio.run(main(parsed))
