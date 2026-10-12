"""Update delivery time for 554 newly discovered Turkish goods on OTTO (JV)."""

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

TR_DELIVERY_TIME = 39  # days -> "4-8 Wochen" on OTTO
JV_SHIPPING_PROFILE_ID = "786c6468-3baf-52e0-88b5-13757eb7f873"  # (4-8 WOCHEN)
LOG_FILE = "/app/update_batch_554.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("update_batch_554")


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
                    wait_time = 30
                elif status in (400, 401, 403, 404):
                    raise exc
            log.warning("API Error: %s. Retrying in %ds (attempt %d/3)...", exc, wait_time, attempt + 1)
            await asyncio.sleep(wait_time)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=0.1)
    args = parser.parse_args()

    log.info("================================================================")
    log.info("  Updating Delivery Time for 554 Turkish Goods on OTTO (JV)     ")
    log.info("  Target Delivery Time: 39 days (4-8 WOCHEN)                    ")
    log.info("  Mode: %s                                                      ", "DRY-RUN" if args.dry_run else "LIVE")
    log.info("================================================================")

    data_file = "/app/live_unupdated_tr_otto.json"
    with open(data_file, "r", encoding="utf-8") as f:
        folders_data = json.load(f)

    # Collect all items
    items_to_update = []
    seen_skus = set()
    for folder in folders_data:
        fname = folder["factory_name"]
        fid = folder["factory_id"]
        for sku in folder.get("all_skus", []):
            sku_str = str(sku).strip()
            if sku_str and sku_str not in seen_skus:
                seen_skus.add(sku_str)
                items_to_update.append((sku_str, fname, fid))

    log.info("Total unique Turkish items to update: %d across %d folders", len(items_to_update), len(folders_data))

    client = _build_otto_client()
    controller = "jv"

    stats_by_folder = defaultdict(int)
    stats_total = {
        "total": len(items_to_update),
        "updated": 0,
        "already_39": 0,
        "not_found": 0,
        "errors": 0,
    }

    start_time = time.time()

    for idx, (sku, fname, fid) in enumerate(items_to_update, 1):
        pct = (idx / len(items_to_update)) * 100
        prefix = f"[{idx}/{len(items_to_update)}] ({pct:.1f}%) SKU: {sku} ({fname})"

        try:
            get_req = GetProductRequest(sku=sku)
            response = await _retry_api_call(client.get_products, get_req, controller, delay_sec=args.delay)
        except Exception as exc:
            log.error("%s GET failed: %s", prefix, exc)
            stats_total["errors"] += 1
            continue

        variations = response.get("productVariations") or []
        if not variations:
            log.warning("%s: Not found on OTTO", prefix)
            stats_total["not_found"] += 1
            continue

        for var in variations:
            var_sku = str(var.get("sku") or "").strip()
            if not var_sku:
                continue

            current_delivery = var.get("delivery") or {}
            current_time = current_delivery.get("deliveryTime")

            patched = None
            if current_time == TR_DELIVERY_TIME:
                stats_total["already_39"] += 1
            else:
                patched = _patch_delivery_time(var, TR_DELIVERY_TIME)

            if args.dry_run:
                log.info("%s [DRY-RUN] deliveryTime %s -> 39 | Profile -> (4-8 WOCHEN)", prefix, current_time)
                stats_total["updated"] += 1
                stats_by_folder[fname] += 1
                continue

            try:
                # 1. Update Catalog
                if patched:
                    await _retry_api_call(
                        client.create_or_update_product,
                        [patched],
                        controller,
                        delay_sec=args.delay,
                        is_post=True,
                    )

                # 2. Update Availability
                del_req = DeliveryInformationRequest(
                    root=[
                        DeliveryInformation(
                            shipping_profile_id=JV_SHIPPING_PROFILE_ID,
                            processing_time=str(TR_DELIVERY_TIME),
                            sku=var_sku,
                        )
                    ]
                )
                await _retry_api_call(
                    client.update_product_delivery,
                    del_req,
                    controller,
                    delay_sec=args.delay,
                    is_post=True,
                )

                log.info("%s OK: deliveryTime %s -> 39, ShippingProfile -> (4-8 WOCHEN)", prefix, current_time)
                stats_total["updated"] += 1
                stats_by_folder[fname] += 1

            except Exception as exc:
                log.error("%s POST failed: %s", prefix, exc)
                stats_total["errors"] += 1

    elapsed = time.time() - start_time
    log.info("================================================================")
    log.info("  Update Finished in %.1f seconds (%.1f minutes)", elapsed, elapsed / 60)
    log.info("  Total Processed: %d | Updated: %d | Already 39: %d | Not Found: %d | Errors: %d",
             stats_total["total"], stats_total["updated"], stats_total["already_39"],
             stats_total["not_found"], stats_total["errors"])
    log.info("================================================================")
    log.info("Breakdown by Folder:")
    for fn, cnt in stats_by_folder.items():
        log.info("  * %s: %d items updated", fn, cnt)

    # Save summary report
    summary_report = {
        "total": stats_total["total"],
        "updated": stats_total["updated"],
        "already_39": stats_total["already_39"],
        "not_found": stats_total["not_found"],
        "errors": stats_total["errors"],
        "by_folder": dict(stats_by_folder),
    }
    with open("/app/batch_554_summary.json", "w", encoding="utf-8") as out:
        json.dump(summary_report, out, ensure_ascii=False, indent=2)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
