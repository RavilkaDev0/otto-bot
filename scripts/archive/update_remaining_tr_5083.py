"""Script to update delivery time and shipping profile for remaining 5083 Turkish Online products on OTTO (JV).

Usage:
  # Dry-run test (no changes sent to OTTO):
  docker exec -it otto-bot-backend python /app/scripts/update_remaining_tr_5083.py --dry-run

  # Test on 5 products:
  docker exec -it otto-bot-backend python /app/scripts/update_remaining_tr_5083.py --limit 5

  # Live update for all 5083 products:
  docker exec -it otto-bot-backend python /app/scripts/update_remaining_tr_5083.py
"""

import argparse
import asyncio
import copy
import json
import logging
import sys
import os
import time
from typing import Any
import httpx
from sqlalchemy import select

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.core.otto_auth import OttoAuth
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.database import SessionLocal, engine
from app.schemas.external_schemes.until_schemes import (
    DeliveryInformation,
    DeliveryInformationRequest,
    GetProductRequest,
)
from app.models.otto_xlsx_import import OttoXlsxImportRow

TR_DELIVERY_TIME = 39  # days -> "4-8 Wochen" on OTTO
JV_SHIPPING_PROFILE_ID = "786c6468-3baf-52e0-88b5-13757eb7f873"  # (4-8 WOCHEN)
LOG_FILE = "/app/update_remaining_5083.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("update_remaining_tr")


def _patch_delivery_time(product_variation: dict, delivery_time: int) -> dict:
    """Return a deep copy of the variation with deliveryTime updated."""
    var = copy.deepcopy(product_variation)
    delivery = var.get("delivery")
    if isinstance(delivery, dict):
        delivery["deliveryTime"] = delivery_time
    else:
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
                    raise exc
            log.warning("API Error: %s. Retrying in %ds (attempt %d/3)...", exc, wait_time, attempt + 1)
            await asyncio.sleep(wait_time)


async def main():
    parser = argparse.ArgumentParser(description="Update delivery time to 39 days for remaining Turkish products.")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without modifying OTTO.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of items to update.")
    parser.add_argument("--delay", type=float, default=0.1, help="Delay between requests in seconds.")
    parser.add_argument("--file", type=str, default="/app/master_remaining_tr_factories.json", help="Path to input JSON file.")
    args = parser.parse_args()

    log.info("================================================================")
    log.info("  Starting OTTO Delivery Time Update (Remaining TR Products)   ")
    log.info("  Account: JV | Target Delivery Time: 39 days (4-8 WOCHEN)     ")
    log.info("  Mode: %s | Limit: %s", "DRY-RUN" if args.dry_run else "LIVE UPDATE", args.limit or "ALL")
    log.info("================================================================")

    # 1. Load EANs from master list
    if not os.path.exists(args.file):
        log.error("Input file not found: %s", args.file)
        sys.exit(1)

    with open(args.file, "r", encoding="utf-8") as f:
        factories_data = json.load(f)

    all_target_eans = set()
    for f_item in factories_data:
        all_target_eans.update(f_item.get("all_eans", []))

    log.info("Loaded %d unique Turkish EANs across %d factories from %s", len(all_target_eans), len(factories_data), args.file)

    # 2. Intersect with Online SKUs from local DB
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean).where(
                OttoXlsxImportRow.account == "jv",
                OttoXlsxImportRow.marketplace_status == "Online",
            )
        )
        online_rows = res.all()

    ean_to_sku = {}
    for r_sku, r_ean in online_rows:
        if r_ean and str(r_ean).strip() in all_target_eans and r_sku:
            ean_to_sku[str(r_ean).strip()] = str(r_sku).strip()

    unique_skus = list(set(ean_to_sku.values()))
    log.info("Found %d matching SKUs with status 'Online' in local DB.", len(unique_skus))

    if args.limit:
        unique_skus = unique_skus[:args.limit]
        log.info("Applied limit: processing first %d SKUs.", len(unique_skus))

    # 3. Update products on OTTO
    client = _build_otto_client()
    controller = "jv"
    stats = {
        "total": len(unique_skus),
        "updated": 0,
        "already_39": 0,
        "not_found": 0,
        "get_error": 0,
        "post_error": 0,
    }

    start_time = time.time()

    for idx, sku in enumerate(unique_skus, 1):
        pct = (idx / len(unique_skus)) * 100
        prefix = f"[{idx}/{len(unique_skus)}] ({pct:.1f}%) SKU: {sku}"

        try:
            get_req = GetProductRequest(sku=sku)
            response = await _retry_api_call(client.get_products, get_req, controller, delay_sec=args.delay)
        except Exception as exc:
            log.error("%s GET failed: %s", prefix, exc)
            stats["get_error"] += 1
            continue

        variations = response.get("productVariations") or []
        if not variations:
            log.warning("%s: not found on OTTO", prefix)
            stats["not_found"] += 1
            continue

        for var in variations:
            var_sku = str(var.get("sku") or "").strip()
            if not var_sku:
                continue

            current_delivery = var.get("delivery") or {}
            current_time = current_delivery.get("deliveryTime")

            patched = None
            if current_time == TR_DELIVERY_TIME:
                stats["already_39"] += 1
            else:
                patched = _patch_delivery_time(var, TR_DELIVERY_TIME)

            if args.dry_run:
                log.info(
                    "%s [DRY-RUN] deliveryTime %s -> %d | Profile -> (4-8 WOCHEN)",
                    prefix, current_time, TR_DELIVERY_TIME
                )
                stats["updated"] += 1
                continue

            try:
                # 1. Update Catalog deliveryTime if not 39
                if patched:
                    await _retry_api_call(
                        client.create_or_update_product,
                        [patched],
                        controller,
                        delay_sec=args.delay,
                        is_post=True
                    )

                # 2. Update Availability delivery information (Processing Time = 39, Shipping Profile = 4-8 Wochen)
                del_req = DeliveryInformationRequest(
                    root=[
                        DeliveryInformation(
                            shipping_profile_id=JV_SHIPPING_PROFILE_ID,
                            processing_time=str(TR_DELIVERY_TIME),
                            sku=var_sku
                        )
                    ]
                )
                await _retry_api_call(
                    client.update_product_delivery,
                    del_req,
                    controller,
                    delay_sec=args.delay,
                    is_post=True
                )

                log.info(
                    "%s OK: deliveryTime %s -> %d, ShippingProfile -> (4-8 WOCHEN)",
                    prefix, current_time, TR_DELIVERY_TIME
                )
                stats["updated"] += 1

            except Exception as exc:
                log.error("%s POST failed: %s", prefix, exc)
                stats["post_error"] += 1

    elapsed = time.time() - start_time
    log.info("================================================================")
    log.info("  Completed in %.1f seconds (%.1f minutes)", elapsed, elapsed / 60)
    log.info("  Total: %d | Updated: %d | Already 39: %d | Not Found: %d | Errors: %d",
             stats["total"], stats["updated"], stats["already_39"], stats["not_found"],
             stats["get_error"] + stats["post_error"])
    log.info("  Full log saved to: %s", LOG_FILE)
    log.info("================================================================")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
