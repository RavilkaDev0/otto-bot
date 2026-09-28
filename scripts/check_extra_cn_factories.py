import asyncio
import json
import logging
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("check_extra_cn")

TARGET_FACTORIES = [
    ("NEW_GANASI_SPECIAL", 483389),
    ("NEW_GANASI_SPECIAL_2", 483390),
    ("NEW_GANASI_SPECIAL_3", 483393),
    ("UK-Evergo", 482556),
    ("UK-JXR-CHINA", 482465),
    ("UK-Mefan", 482547),
    ("UK-PengPai Furniture", 482406),
    ("UK-Romero", 482582),
    ("UK-WEEDING-ROYAL-CHINA", 482568),
    ("UK-You-ME-Furniture", 482586),
]

async def main():
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=60.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

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

    all_items = []
    for fname, fid in TARGET_FACTORIES:
        resp = await ab.send_request(
            "GET",
            "/api/products",
            params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        log.info("Factory %s (ID %d): %d items in Afterbuy", fname, fid, len(items))
        for it in items:
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if sku:
                all_items.append({
                    "sku": sku,
                    "title": str(row.get("Artikelbeschreibung") or "")[:60],
                    "factory_name": fname,
                    "factory_id": fid
                })

    log.info("Total items across these 10 factories: %d", len(all_items))

    # De-duplicate
    unique_items = {}
    for it in all_items:
        unique_items[it["sku"]] = it

    log.info("Total unique SKUs: %d", len(unique_items))

    # Check live OTTO status
    pt_counts = {}
    live_results = []

    async def check_one(it):
        async with sem:
            try:
                res = await otto.get_shipping_profile_for_product(it["sku"], "xl")
                pt = str(res.get("processingTime") or "")
                prof = str(res.get("shippingProfileId") or "")
                it["live_pt"] = pt
                it["live_prof"] = prof
                pt_counts[pt] = pt_counts.get(pt, 0) + 1
                live_results.append(it)
            except Exception as e:
                it["live_pt"] = "NOT_FOUND"
                pt_counts["NOT_FOUND"] = pt_counts.get("NOT_FOUND", 0) + 1

    await asyncio.gather(*[check_one(it) for it in unique_items.values()])

    log.info("==================================================")
    log.info("LIVE OTTO XL STATUS FOR 10 EXTRA CHINESE FACTORIES:")
    for pt, cnt in sorted(pt_counts.items()):
        log.info("  processingTime = %s: %d items", pt, cnt)
    log.info("==================================================")

    # Filter items that are NOT at 57
    need_update = [it for it in live_results if it.get("live_pt") in ["35", "DEFAULT"] or (it.get("live_pt") != "57" and it.get("live_pt") != "NOT_FOUND")]
    log.info("Items that need update to 12 weeks: %d", len(need_update))

    with open("/app/extra_cn_factories_status.json", "w", encoding="utf-8") as out:
        json.dump({
            "pt_counts": pt_counts,
            "need_update": need_update,
            "all_live": live_results
        }, out, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    asyncio.run(main())
