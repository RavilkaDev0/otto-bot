import asyncio
import json
import logging
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("check_status")

async def main():
    with open("/app/timur_factories_audit.json") as f:
        data = json.load(f)

    cn_items = data.get("cn_items", [])
    log.info("Total CN items from Timur audit: %d", len(cn_items))

    # De-duplicate by SKU
    unique_skus = {}
    for it in cn_items:
        s = str(it.get("sku") or it.get("ean") or "").strip()
        if s and s not in unique_skus:
            unique_skus[s] = it

    log.info("Unique CN SKUs to check: %d", len(unique_skus))

    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
    )
    otto = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)

    sem = asyncio.Semaphore(15)

    status_counts = {
        "pt_35": [],  # Currently 35 days (4-8 weeks) -> NEED UPDATE TO 12 WEEKS!
        "pt_57": [],  # Already 57 days (12 weeks)
        "other_pt": [],
        "not_found": [],
        "error": []
    }

    async def check_one(sku, item_info):
        async with sem:
            for attempt in range(3):
                try:
                    res = await otto.get_shipping_profile_for_product(sku, "xl")
                    pt = str(res.get("processingTime") or "")
                    prof = str(res.get("shippingProfileId") or "")
                    item_info["live_pt"] = pt
                    item_info["live_prof"] = prof

                    if pt == "35":
                        status_counts["pt_35"].append(item_info)
                    elif pt == "57":
                        status_counts["pt_57"].append(item_info)
                    else:
                        status_counts["other_pt"].append(item_info)
                    return
                except Exception as e:
                    err_str = str(e)
                    if "404" in err_str:
                        status_counts["not_found"].append(item_info)
                        return
                    if attempt == 2:
                        status_counts["error"].append(item_info)
                    await asyncio.sleep(1)

    log.info("Checking live OTTO XL shipping status for all %d items...", len(unique_skus))
    tasks = [check_one(sku, it) for sku, it in unique_skus.items()]
    await asyncio.gather(*tasks)

    log.info("==================================================")
    log.info("LIVE OTTO XL STATUS RESULTS FOR TIMUR CN ITEMS:")
    log.info("  * processingTime = 35 (4-8 weeks, NEED 12w UPDATE): %d", len(status_counts["pt_35"]))
    log.info("  * processingTime = 57 (Already 12 weeks): %d", len(status_counts["pt_57"]))
    log.info("  * other processingTime: %d", len(status_counts["other_pt"]))
    log.info("  * not found on OTTO XL (drafts/inactive): %d", len(status_counts["not_found"]))
    log.info("  * API errors: %d", len(status_counts["error"]))
    log.info("==================================================")

    # Save details
    with open("/app/timur_cn_live_status.json", "w", encoding="utf-8") as out:
        json.dump(status_counts, out, indent=2, ensure_ascii=False)
    log.info("Saved to /app/timur_cn_live_status.json")

if __name__ == "__main__":
    asyncio.run(main())
