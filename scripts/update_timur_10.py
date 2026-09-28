"""End-to-end scanner and updater for Turkish products in Timur_for_Otto_Standard_10.
Supports OTTO XL and JV accounts.

Usage:
  # 1. Test run (Dry-Run) on XL:
  python3 scripts/update_timur_10.py --account xl --dry-run

  # 2. Live run on XL:
  python3 scripts/update_timur_10.py --account xl --live

  # 3. Test run on JV:
  python3 scripts/update_timur_10.py --account jv --dry-run

  # 4. Live run on JV:
  python3 scripts/update_timur_10.py --account jv --live
"""

import argparse
import asyncio
import copy
import json
import logging
import os
import sys
import time
import random
from collections import Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

import httpx
from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.schemas.external_schemes.until_schemes import (
    DeliveryInformation,
    DeliveryInformationRequest,
    GetProductRequest,
)
from scripts.tr_rules import is_turkish_product

# Configuration
TR_DELIVERY_TIME = 39  # 39 days -> 4-8 Wochen

TIMUR_10_FACTORIES = {
    "xl": {"id": 483410, "name": "Timur_for_Otto_Standard_10"},
    "jv": {"id": 504201, "name": "Timur_for_Otto_Standard_10"},
}

SHIPPING_PROFILES = {
    "jv": "786c6468-3baf-52e0-88b5-13757eb7f873",  # (4-8 WOCHEN)
    "xl": "d045a64d-59e6-57fb-9f69-6370dc7c9d40",  # LIEFERZEIT (4-8 WOCHEN)
}

os.makedirs("/app/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/logs/update_timur_10.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("timur_10")


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


async def _retry_api_call(coro_func, *args, delay_sec: float = 0.05, is_post: bool = False):
    delays = [2, 5, 10]
    for attempt in range(4):
        try:
            res = await coro_func(*args)
            if delay_sec > 0:
                await asyncio.sleep(delay_sec)
            return res
        except Exception as exc:
            if attempt == 3:
                raise exc
            wait_time = delays[attempt] + random.uniform(0.2, 1.0)
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                if status == 429:
                    wait_time = 5 + (attempt * 3) + random.uniform(1.0, 4.0)
                elif status in (400, 401, 403, 404):
                    raise exc
            log.warning("API Error: %s. Retrying in %.1fs...", exc, wait_time)
            await asyncio.sleep(wait_time)


async def fetch_afterbuy_items(account: str, factory_id: int) -> list[dict]:
    log.info("Fetching items from Afterbuy for account=%s, factory_id=%d...", account.upper(), factory_id)
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=60.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    all_items = []
    offset = 0
    limit = 250

    while True:
        resp = await ab.send_request(
            "GET",
            "/api/products",
            params={
                "account": account,
                "dataset": "lister",
                "factory_id": factory_id,
                "offset": offset,
                "limit": limit,
                "include_row": 1,
            },
            cookies={"session": session},
        )
        if resp.status_code != 200:
            log.error("Afterbuy error %d at offset %d: %s", resp.status_code, offset, resp.text[:200])
            break

        data = resp.json()
        items = data.get("items", [])
        all_items.extend(items)
        total = data.get("total", len(all_items))
        log.info("  Fetched %d / %d items...", len(all_items), total)
        if len(items) < limit or len(all_items) >= total:
            break
        offset += limit

    return all_items


def _patch_delivery_time(product_variation: dict, delivery_time: int) -> dict:
    var = copy.deepcopy(product_variation)
    delivery = var.get("delivery")
    if isinstance(delivery, dict):
        delivery["deliveryTime"] = delivery_time
    else:
        var["delivery"] = {"deliveryTime": delivery_time}
    return var


async def process_account(account: str, is_dry_run: bool, concurrency: int = 5, delay: float = 0.05):
    fact_info = TIMUR_10_FACTORIES.get(account)
    shipping_profile_id = SHIPPING_PROFILES.get(account)
    if not fact_info or not shipping_profile_id:
        log.error("Invalid configuration for account '%s'", account)
        return

    fid = fact_info["id"]
    fname = fact_info["name"]

    log.info("=" * 70)
    log.info("  STARTING TASK: %s (ID %d) on OTTO %s", fname, fid, account.upper())
    log.info("  Target: Delivery Time = %d days (4-8 Wochen)", TR_DELIVERY_TIME)
    log.info("  Target Shipping Profile: %s", shipping_profile_id)
    log.info("  Mode: %s", "DRY-RUN (Simulate only, no changes)" if is_dry_run else "LIVE (Applying changes!)")
    log.info("=" * 70)

    # 1. Fetch from Afterbuy
    raw_items = await fetch_afterbuy_items(account, fid)
    log.info("Total items received from Afterbuy: %d", len(raw_items))

    # 2. Filter Turkish products
    tr_items = []
    non_tr_items = []
    reasons = Counter()

    seen_skus = set()
    for it in raw_items:
        row = it.get("row", {})
        sku = str(row.get("EAN") or it.get("ean") or row.get("Artikelnummer") or "").strip()
        if not sku or sku in seen_skus:
            continue
        seen_skus.add(sku)

        is_tr, reason = is_turkish_product(row)
        if is_tr:
            tr_items.append({"sku": sku, "title": str(row.get("Artikelname") or row.get("Artikelbeschreibung") or "")[:50], "reason": reason})
            reasons[reason] += 1
        else:
            non_tr_items.append({"sku": sku, "title": str(row.get("Artikelname") or "")[:50]})

    log.info("\n--- Turkish Origin Classification ---")
    log.info("  Total Unique SKUs: %d", len(seen_skus))
    log.info("  Confirmed Turkish: %d (%.1f%%)", len(tr_items), (len(tr_items) / max(1, len(seen_skus))) * 100)
    log.info("  Non-Turkish:       %d", len(non_tr_items))
    for r, count in reasons.most_common():
        log.info("    - %s: %d", r, count)

    if not tr_items:
        log.warning("No Turkish items found in factory %s. Exiting.", fname)
        return

    # 3. Check and Update on OTTO
    client = _build_otto_client()
    semaphore = asyncio.Semaphore(concurrency)
    counter_lock = asyncio.Lock()
    rate_limiter = asyncio.Lock()
    last_req_time = [0.0]
    MIN_REQUEST_GAP = 0.2  # Pacing: ~5 requests per sec max to prevent APISIX 429

    async def _paced_call(coro_func, *args, delay_sec: float = 0.05, is_post: bool = False):
        async with rate_limiter:
            now = time.time()
            gap = now - last_req_time[0]
            if gap < MIN_REQUEST_GAP:
                await asyncio.sleep(MIN_REQUEST_GAP - gap)
            last_req_time[0] = time.time()
        return await _retry_api_call(coro_func, *args, delay_sec=delay_sec, is_post=is_post)

    stats = {
        "total_tr": len(tr_items),
        "not_in_otto": 0,
        "already_updated": 0,
        "needed_update": 0,
        "updated_ok": 0,
        "errors": 0,
    }

    async def _worker(idx: int, item: dict):
        sku = item["sku"]
        prefix = f"[{idx + 1}/{len(tr_items)}] SKU {sku}"

        async with semaphore:
            # 1. Get from OTTO
            try:
                otto_res = await _paced_call(
                    client.get_products,
                    GetProductRequest(sku=sku),
                    account,
                    delay_sec=0.0,
                    is_post=False,
                )
            except Exception as e:
                log.error("%s OTTO lookup error: %s", prefix, e)
                async with counter_lock:
                    stats["errors"] += 1
                return

            resources = otto_res.get("productVariations", []) if isinstance(otto_res, dict) else []
            if not resources:
                async with counter_lock:
                    stats["not_in_otto"] += 1
                if (idx + 1) % 25 == 0 or idx == 0:
                    log.info("%s Not found in OTTO (progress: %d/%d)", prefix, idx + 1, len(tr_items))
                return

            var = resources[0]
            var_sku = var.get("sku", sku)
            current_time = var.get("delivery", {}).get("deliveryTime")

            # Check delivery info
            try:
                del_info = await _paced_call(
                    client.get_shipping_profile_for_product,
                    var_sku,
                    account,
                    delay_sec=0.0,
                    is_post=False,
                )
            except Exception:
                del_info = {}

            cur_prof = del_info.get("shippingProfileId") if isinstance(del_info, dict) else None
            cur_pt = del_info.get("processingTime") if isinstance(del_info, dict) else None

            # Check if update is needed
            needs_catalog = (current_time != TR_DELIVERY_TIME)
            needs_delivery = (cur_prof != shipping_profile_id or cur_pt != str(TR_DELIVERY_TIME))

            if not needs_catalog and not needs_delivery:
                async with counter_lock:
                    stats["already_updated"] += 1
                if (idx + 1) % 25 == 0:
                    log.info("%s Already at 4-8 Wochen (progress: %d/%d)", prefix, idx + 1, len(tr_items))
                return

            async with counter_lock:
                stats["needed_update"] += 1

            if is_dry_run:
                log.info(
                    "%s [DRY-RUN] Would update: deliveryTime %s -> %d | Profile %s -> %s | PT %s -> %d",
                    prefix, current_time, TR_DELIVERY_TIME, cur_prof, shipping_profile_id, cur_pt, TR_DELIVERY_TIME
                )
                return

            # Live Update
            try:
                # 1. Update Catalog variation delivery time (non-blocking if validation fails)
                if needs_catalog:
                    try:
                        patched = _patch_delivery_time(var, TR_DELIVERY_TIME)
                        await _paced_call(
                            client.create_or_update_product,
                            [patched],
                            account,
                            delay_sec=delay,
                            is_post=True,
                        )
                    except Exception as cat_err:
                        log.warning("%s Catalog variation update warning (will still apply ShippingProfile): %s", prefix, cat_err)

                # 2. Update Availability & Shipping Profile (sets 4-8 weeks on Otto)
                del_req = DeliveryInformationRequest(
                    root=[
                        DeliveryInformation(
                            shipping_profile_id=shipping_profile_id,
                            processing_time=str(TR_DELIVERY_TIME),
                            sku=var_sku,
                        )
                    ]
                )
                await _paced_call(
                    client.update_product_delivery,
                    del_req,
                    account,
                    delay_sec=delay,
                    is_post=True,
                )

                log.info("%s OK: Updated to 39 days (4-8 Wochen) & ShippingProfile", prefix)
                async with counter_lock:
                    stats["updated_ok"] += 1

            except Exception as e:
                log.error("%s FAILED to update delivery: %s", prefix, e)
                async with counter_lock:
                    stats["errors"] += 1

    tasks = [_worker(i, it) for i, it in enumerate(tr_items)]
    await asyncio.gather(*tasks)

    log.info("\n" + "=" * 70)
    log.info("  FINAL REPORT FOR %s on OTTO %s (%s)", fname, account.upper(), "DRY-RUN" if is_dry_run else "LIVE")
    log.info("=" * 70)
    log.info("  Confirmed Turkish SKUs:  %d", stats["total_tr"])
    log.info("  Not Found in OTTO:       %d", stats["not_in_otto"])
    log.info("  Already at 4-8 Wochen:   %d", stats["already_updated"])
    log.info("  Required Update:         %d", stats["needed_update"])
    if not is_dry_run:
        log.info("  Successfully Updated:    %d", stats["updated_ok"])
        log.info("  Errors:                  %d", stats["errors"])
    log.info("=" * 70 + "\n")


async def main():
    parser = argparse.ArgumentParser(description="Update Turkish products in Timur 10 to 8 weeks on OTTO")
    parser.add_argument("--account", choices=["xl", "jv", "all"], default="xl", help="Target account: xl, jv, or all")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without making changes to OTTO")
    parser.add_argument("--live", action="store_true", help="Execute live update on OTTO")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent requests (default: 5)")
    args = parser.parse_args()

    # Default to dry-run if --live is not explicitly provided
    is_dry_run = not args.live

    accounts = ["xl", "jv"] if args.account == "all" else [args.account]
    for acc in accounts:
        await process_account(acc, is_dry_run=is_dry_run, concurrency=args.concurrency)


if __name__ == "__main__":
    asyncio.run(main())
