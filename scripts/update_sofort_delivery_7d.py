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

SHIPPING_PROFILES_SOFORT = {
    "xl": "9d2ac737-22dc-5bfa-8a5f-a2cef07b7efd",  # SOFORT LIEFERBAR (transportTime: 5, defaultProcessingTime: 5)
    "jv": "b4139e65-603f-52f7-9b99-393cf6b2461f",  # SOFORT LIEFERBAR (transportTime: 5, defaultProcessingTime: 5)
}

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/update_sofort_delivery_7d.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("update_sofort_delivery_7d")


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
    parser = argparse.ArgumentParser(description="Update SOFORT factories to 7 days delivery")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without live OTTO API calls")
    parser.add_argument("--processing-time", type=int, default=2, help="Processing time in working days (default 2 -> 2 + 5 transport = 7 days)")
    parser.add_argument("--batch-size", type=int, default=100, help="SKUs per batch")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between batch requests in seconds")
    parser.add_argument("--audit-file", type=str, default="scratch/factory_delivery_audit.json", help="Path to audit JSON")
    args = parser.parse_args()

    pt_str = str(args.processing_time)

    log.info("================================================================")
    log.info("  STARTING SOFORT FACTORIES DELIVERY UPDATE")
    log.info("  Target Processing Time: %s days (Transport: 5 days -> Total: %d days)", pt_str, args.processing_time + 5)
    log.info("  Profiles: XL=%s | JV=%s", SHIPPING_PROFILES_SOFORT["xl"], SHIPPING_PROFILES_SOFORT["jv"])
    log.info("  Dry Run: %s | Batch Size: %d | Delay: %.2fs", args.dry_run, args.batch_size, args.delay)
    log.info("================================================================")

    if not os.path.exists(args.audit_file):
        log.error("Audit file %s not found! Please run audit first.", args.audit_file)
        return

    with open(args.audit_file, "r", encoding="utf-8") as f:
        audit_data = json.load(f)

    client = _build_otto_client()

    overall_results = {}

    for fid, f_info in audit_data.items():
        log.info("\n--- Processing Factory %s (Afterbuy total: %d) ---", fid, f_info.get("total_afterbuy", 0))

        # Check XL items
        xl_skus = [it["sku"] for it in f_info.get("xl_items", []) if it.get("sku")]
        jv_skus = [it["sku"] for it in f_info.get("jv_items", []) if it.get("sku")]

        factory_res = {"xl": {"total": len(xl_skus), "success": 0, "errors": 0}, "jv": {"total": len(jv_skus), "success": 0, "errors": 0}}

        for ctrl, skus in [("xl", xl_skus), ("jv", jv_skus)]:
            if not skus:
                continue

            prof_id = SHIPPING_PROFILES_SOFORT[ctrl]
            total_skus = len(skus)
            batch_size = max(1, min(args.batch_size, 500))
            total_batches = (total_skus + batch_size - 1) // batch_size

            log.info("Account %s: %d SKUs to update in %d batches (Profile: %s, PT: %s)", ctrl.upper(), total_skus, total_batches, prof_id, pt_str)

            for b_idx in range(total_batches):
                batch_skus = skus[b_idx * batch_size : (b_idx + 1) * batch_size]
                items_req = [
                    DeliveryInformation(
                        shipping_profile_id=prof_id,
                        processing_time=pt_str,
                        sku=s,
                    )
                    for s in batch_skus
                ]
                del_req = DeliveryInformationRequest(root=items_req)

                if args.dry_run:
                    log.info("[DRY-RUN] [%s] Batch %d/%d (%d SKUs): OK", ctrl.upper(), b_idx + 1, total_batches, len(batch_skus))
                    factory_res[ctrl]["success"] += len(batch_skus)
                    continue

                try:
                    await _retry_api_call(
                        client.update_product_delivery,
                        del_req,
                        ctrl,
                        delay_sec=args.delay,
                    )
                    factory_res[ctrl]["success"] += len(batch_skus)
                    log.info("[%s] Batch %d/%d (%d SKUs) -> SUCCESS", ctrl.upper(), b_idx + 1, total_batches, len(batch_skus))
                except Exception as exc:
                    log.error("[%s] Batch %d/%d FAILED: %s", ctrl.upper(), b_idx + 1, total_batches, exc)
                    factory_res[ctrl]["errors"] += len(batch_skus)

        overall_results[fid] = factory_res

    log.info("\n================================================================")
    log.info("  FINAL SUMMARY")
    for fid, res in overall_results.items():
        log.info("Factory %s: XL [Total: %d, Ok: %d, Err: %d] | JV [Total: %d, Ok: %d, Err: %d]",
                 fid, res["xl"]["total"], res["xl"]["success"], res["xl"]["errors"],
                 res["jv"]["total"], res["jv"]["success"], res["jv"]["errors"])
    log.info("================================================================")

    with open("scratch/update_sofort_delivery_summary.json", "w", encoding="utf-8") as f:
        json.dump(overall_results, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
