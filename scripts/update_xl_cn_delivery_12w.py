import argparse
import asyncio
import json
import logging
import os
import sys
import time

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.schemas.external_schemes.until_schemes import DeliveryInformationRequest, DeliveryInformation
import httpx

SHIPPING_PROFILE_CN_12W = "027a1a46-cf60-56f4-917b-81661569f016"  # LIEFERZEIT (8-12 WOCHEN)
DEFAULT_PT = 57  # 57 + 3 (transport) = 60 working days = 12 weeks

os.makedirs("/app/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/logs/update_xl_cn_12w.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("update_xl_cn_12w")


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


async def _retry_api_call(coro_func, *args, delay_sec: float = 0.15):
    delays = [2, 5, 15]
    for attempt in range(4):
        try:
            res = await coro_func(*args)
            if delay_sec > 0:
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
    parser = argparse.ArgumentParser(description="Update XL Chinese products to 12 weeks (PT=57)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    parser.add_argument("--batch-size", type=int, default=200, help="SKUs per batch (max 500)")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between batch POSTs in seconds")
    parser.add_argument("--processing-time", type=int, default=DEFAULT_PT, help="Processing time in days (default: 57)")
    parser.add_argument("--input", type=str, default="/app/cn_products_xl.json", help="Input JSON file")
    args = parser.parse_args()

    pt_str = str(args.processing_time)
    log.info("================================================================")
    log.info("  STARTING XL CHINESE PRODUCTS UPDATE -> %s DAYS (12 WEEKS)", pt_str)
    log.info("  Profile: %s (LIEFERZEIT (8-12 WOCHEN))", SHIPPING_PROFILE_CN_12W)
    log.info("  Batch size: %d | Delay: %.2fs | Dry run: %s", args.batch_size, args.delay, args.dry_run)
    log.info("================================================================")

    if not os.path.exists(args.input):
        log.error("Input file %s does not exist! Run scan_cn_products_xl.py first.", args.input)
        return

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_skus = []
    seen = set()
    
    if "items" in data:
        # New format (from cn_in_tr_batches_found.json)
        for it in data["items"]:
            s = str(it.get("sku") or it.get("ean") or "").strip()
            if s and s not in seen:
                seen.add(s)
                all_skus.append(s)
    else:
        for fact in data.get("factories", []):
            for it in fact.get("items", []):
                s = str(it.get("ean") or it.get("sku") or "").strip()
                if s and s not in seen:
                    seen.add(s)
                    all_skus.append(s)

    total_skus = len(all_skus)
    log.info("Loaded %d unique Chinese SKUs from %d factories.", total_skus, len(data.get("factories", [])))

    if total_skus == 0:
        log.error("No SKUs found to process!")
        return

    client = _build_otto_client()

    batch_size = max(1, min(args.batch_size, 500))
    total_batches = (total_skus + batch_size - 1) // batch_size
    log.info("Total batches to send: %d (batch size: %d)", total_batches, batch_size)

    start_time = time.time()
    success_count = 0
    error_count = 0

    for b_idx in range(total_batches):
        batch_skus = all_skus[b_idx * batch_size : (b_idx + 1) * batch_size]
        items_req = [
            DeliveryInformation(
                shipping_profile_id=SHIPPING_PROFILE_CN_12W,
                processing_time=pt_str,
                sku=s,
            )
            for s in batch_skus
        ]
        del_req = DeliveryInformationRequest(root=items_req)

        if args.dry_run:
            log.info("[DRY-RUN] Batch [%d/%d] (%d items): OK", b_idx + 1, total_batches, len(batch_skus))
            success_count += len(batch_skus)
            continue

        try:
            await _retry_api_call(
                client.update_product_delivery,
                del_req,
                "xl",
                delay_sec=args.delay,
            )
            success_count += len(batch_skus)
            pct = (success_count / total_skus) * 100
            elapsed = time.time() - start_time
            rate = success_count / elapsed if elapsed > 0 else 0
            eta = (total_skus - success_count) / rate if rate > 0 else 0

            log.info(
                "[%d/%d] %d/%d (%.1f%%) | Rate: %.0f items/s | ETA: %.0fs | OK",
                b_idx + 1,
                total_batches,
                success_count,
                total_skus,
                pct,
                rate,
                eta,
            )
        except Exception as exc:
            log.error("Batch [%d/%d] FAILED: %s", b_idx + 1, total_batches, exc)
            error_count += len(batch_skus)

    elapsed_total = time.time() - start_time
    log.info("================================================================")
    log.info("  UPDATE COMPLETE in %.1fs", elapsed_total)
    log.info("  Total: %d | Success: %d | Errors: %d", total_skus, success_count, error_count)
    log.info("================================================================")

    # Save summary
    summary = {
        "timestamp": time.time(),
        "country": "CN",
        "account": "xl",
        "total_skus": total_skus,
        "processing_time": args.processing_time,
        "shipping_profile": SHIPPING_PROFILE_CN_12W,
        "success_count": success_count,
        "error_count": error_count,
        "elapsed_seconds": round(elapsed_total, 1),
    }
    with open("/app/update_xl_cn_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
