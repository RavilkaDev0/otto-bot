"""Universal updater for Turkish products on OTTO (Catalog + Availability).
Supports both JV and XL accounts.
"""

import argparse
import asyncio
import copy
import json
import logging
import os
import sys
import time
from collections import defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

import httpx
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.database import engine
from app.schemas.external_schemes.until_schemes import (
    DeliveryInformation,
    DeliveryInformationRequest,
    GetProductRequest,
)

TR_DELIVERY_TIME = 39  # 39 days -> 4-8 Wochen

SHIPPING_PROFILES = {
    "jv": "786c6468-3baf-52e0-88b5-13757eb7f873",  # (4-8 WOCHEN)
    "xl": "d045a64d-59e6-57fb-9f69-6370dc7c9d40",  # LIEFERZEIT (4-8 WOCHEN)
}

os.makedirs("/app/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/update_batch2_xl.log", encoding="utf-8"),
        logging.FileHandler("/app/update_xl_tr.log", encoding="utf-8"),
        logging.FileHandler("/app/logs/otto_bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("update_tr_products")


def _patch_delivery_time(product_variation: dict, delivery_time: int) -> dict:
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
                    wait_time = 25
                elif status in (400, 401, 403, 404):
                    raise exc
            log.warning("API Error: %s. Retrying in %ds...", exc, wait_time)
            await asyncio.sleep(wait_time)


async def main():
    parser = argparse.ArgumentParser(description="Update Turkish products delivery time on OTTO")
    parser.add_argument("--account", choices=["jv", "xl"], default="xl", help="Account: jv or xl")
    parser.add_argument("--input", type=str, default=None, help="Input JSON file from scanner")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without making API changes")
    parser.add_argument("--delay", type=float, default=0.05, help="Delay between POST calls in seconds")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent workers (default: 5)")
    parser.add_argument("--summary", type=str, default=None, help="Summary output JSON path")
    args = parser.parse_args()

    account = args.account.lower()
    shipping_profile_id = SHIPPING_PROFILES.get(account)
    if not shipping_profile_id:
        log.error("Unknown shipping profile for account '%s'", account)
        return

    input_file = args.input or f"/app/tr_products_{account}.json"
    if not os.path.exists(input_file):
        log.error("Input file %s does not exist. Run scan_tr_products.py first!", input_file)
        return

    with open(input_file, "r", encoding="utf-8") as f:
        scan_data = json.load(f)

    items_to_process = []
    seen_skus = set()
    for f_data in scan_data.get("factories", []):
        fn = f_data["factory_name"]
        for it in f_data.get("items", []):
            sku = str(it.get("ean") or it.get("sku") or "").strip()
            if sku and sku not in seen_skus:
                seen_skus.add(sku)
                items_to_process.append({"sku": sku, "factory_name": fn})

    log.info("================================================================")
    log.info("  Updating %d Turkish Products on OTTO %s", len(items_to_process), account.upper())
    log.info("  Target Delivery Time: %d days", TR_DELIVERY_TIME)
    log.info("  Shipping Profile ID: %s", shipping_profile_id)
    log.info("  Mode: %s", "DRY-RUN" if args.dry_run else "LIVE")
    log.info("================================================================")

    client = _build_otto_client()
    stats_by_folder = defaultdict(int)
    stats_total = {
        "total": len(items_to_process),
        "updated": 0,
        "already_39": 0,
        "not_found": 0,
        "errors": 0,
    }

    start_time = time.time()
    sem = asyncio.Semaphore(args.concurrency)
    counter_lock = asyncio.Lock()
    processed_count = 0
    total_items = len(items_to_process)

    async def process_item(it):
        nonlocal processed_count
        sku = it["sku"]
        fname = it["factory_name"]

        async with sem:
            async with counter_lock:
                processed_count += 1
                curr_idx = processed_count
            pct = (curr_idx / total_items) * 100
            prefix = f"[{curr_idx}/{total_items}] ({pct:.1f}%) SKU: {sku} ({fname})"

            try:
                get_req = GetProductRequest(sku=sku)
                response = await _retry_api_call(client.get_products, get_req, account, delay_sec=args.delay)
            except Exception as exc:
                log.error("%s GET failed: %s", prefix, exc)
                async with counter_lock:
                    stats_total["errors"] += 1
                return

            variations = response.get("productVariations") or []
            if not variations:
                log.warning("%s: Not found on OTTO", prefix)
                async with counter_lock:
                    stats_total["not_found"] += 1
                return

            for var in variations:
                var_sku = str(var.get("sku") or "").strip()
                if not var_sku:
                    continue

                current_delivery = var.get("delivery") or {}
                current_time = current_delivery.get("deliveryTime")

                patched = None
                if current_time == TR_DELIVERY_TIME:
                    async with counter_lock:
                        stats_total["already_39"] += 1
                else:
                    patched = _patch_delivery_time(var, TR_DELIVERY_TIME)

                if args.dry_run:
                    log.info("%s [DRY-RUN] deliveryTime %s -> 39 | Profile -> %s", prefix, current_time, shipping_profile_id)
                    async with counter_lock:
                        stats_total["updated"] += 1
                        stats_by_folder[fname] += 1
                    continue

                try:
                    # 1. Update Catalog
                    if patched:
                        await _retry_api_call(
                            client.create_or_update_product,
                            [patched],
                            account,
                            delay_sec=args.delay,
                            is_post=True,
                        )

                    # 2. Update Availability
                    del_req = DeliveryInformationRequest(
                        root=[
                            DeliveryInformation(
                                shipping_profile_id=shipping_profile_id,
                                processing_time=str(TR_DELIVERY_TIME),
                                sku=var_sku,
                            )
                        ]
                    )
                    await _retry_api_call(
                        client.update_product_delivery,
                        del_req,
                        account,
                        delay_sec=args.delay,
                        is_post=True,
                    )

                    log.info("%s OK: deliveryTime %s -> 39, ShippingProfile -> %s", prefix, current_time, shipping_profile_id)
                    async with counter_lock:
                        stats_total["updated"] += 1
                        stats_by_folder[fname] += 1

                except Exception as exc:
                    log.error("%s POST failed: %s", prefix, exc)
                    async with counter_lock:
                        stats_total["errors"] += 1

    # Run in chunks of 500 to avoid excessive in-flight tasks
    chunk_size = 500
    for i in range(0, len(items_to_process), chunk_size):
        chunk = items_to_process[i:i + chunk_size]
        await asyncio.gather(*[process_item(it) for it in chunk])

    elapsed = time.time() - start_time
    log.info("================================================================")
    log.info("  Update Finished in %.1f seconds (%.1f minutes)", elapsed, elapsed / 60)
    log.info("  Total Processed: %d | Updated: %d | Already 39: %d | Not Found: %d | Errors: %d",
             stats_total["total"], stats_total["updated"], stats_total["already_39"],
             stats_total["not_found"], stats_total["errors"])
    log.info("================================================================")
    log.info("Breakdown by Folder:")
    for fn, cnt in sorted(stats_by_folder.items(), key=lambda x: x[1], reverse=True):
        log.info("  * %s: %d items updated", fn, cnt)

    summary_file = args.summary or f"/app/update_summary_{account}.json"
    summary_report = {
        "account": account,
        "total": stats_total["total"],
        "updated": stats_total["updated"],
        "already_39": stats_total["already_39"],
        "not_found": stats_total["not_found"],
        "errors": stats_total["errors"],
        "elapsed_sec": round(elapsed, 1),
        "by_folder": dict(stats_by_folder),
    }
    with open(summary_file, "w", encoding="utf-8") as out:
        json.dump(summary_report, out, ensure_ascii=False, indent=2)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
