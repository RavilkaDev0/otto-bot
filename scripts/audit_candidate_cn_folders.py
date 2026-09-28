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
log = logging.getLogger("audit_extra_cn")

# Candidate Chinese folders found in Afterbuy XL
CANDIDATE_FOLDERS = [
    ("NEW_GANASI_SPECIAL", 483389),
    ("NEW_GANASI_SPECIAL_2", 483390),
    ("NEW_GANASI_SPECIAL_3", 483393),
    ("UK-JXR-CHINA", 482465),
    ("UK-WEEDING-ROYAL-CHINA", 482568),
    ("UK-PengPai Furniture", 482406),
    ("UK-Mefan", 482547),
    ("UK-Milano-Furniture", 482562),
    ("UK-Romeigo", 482565),
    ("UK-Romero", 482582),
    ("UK-SUNRAY", 482542),
    ("UK-Wasserwand-Sunray", 482402),
    ("UK-You-ME-Furniture", 482586),
    ("UK-COCO", 482439),
    ("UK-DMB", 482514),
    ("UK-Epoxid-Furniture", 482461),
    ("UK-Evergo", 482556),
    ("WEJ_(CN)", 481993),
    ("WPS_(CN)", 482902),
    ("DELETE_DONT_TOUCH Weitao-NO_AKTIV_(CN)", 482609),
]

async def main():
    # Load all 11,291 already updated SKUs
    already_updated = set()
    for path in [
        "/app/cn_products_xl.json",
        "/app/cn_in_tr_batches_found.json",
        "/app/timur_cn_to_update_12w.json",
    ]:
        try:
            with open(path) as f:
                d = json.load(f)
                items = d.get("items", [])
                for it in items:
                    s = str(it.get("ean") or it.get("sku") or "").strip()
                    if s: already_updated.add(s)
                for fact in d.get("factories", []):
                    for it in fact.get("items", []):
                        s = str(it.get("ean") or it.get("sku") or "").strip()
                        if s: already_updated.add(s)
        except Exception:
            pass

    log.info("Total already updated unique Chinese SKUs: %d", len(already_updated))

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

    discovered_cn_items = []

    for fname, fid in CANDIDATE_FOLDERS:
        try:
            resp = await ab.send_request(
                "GET",
                "/api/products",
                params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
                cookies={"session": session},
            )
            items = resp.json().get("items", [])
        except Exception as e:
            log.error("Error fetching %s (ID %d): %s", fname, fid, e)
            continue

        log.info("Folder %s (ID %d): %d items in Afterbuy", fname, fid, len(items))
        if not items:
            continue

        folder_new_items = []
        for it in items:
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if not sku:
                continue
            if sku in already_updated:
                continue

            folder_new_items.append({
                "sku": sku,
                "ean": str(row.get("EAN") or "").strip(),
                "title": str(row.get("Artikelbeschreibung") or ""),
                "factory_name": fname,
                "factory_id": fid,
                "gurl": str(row.get("GalleryURL") or row.get("PictureURL") or "")[:100]
            })

        log.info("  -> %d items NOT in already_updated list", len(folder_new_items))

        # Check live OTTO status for new items
        async def check_item(it_dict):
            async with sem:
                try:
                    res = await otto.get_shipping_profile_for_product(it_dict["sku"], "xl")
                    pt = str(res.get("processingTime") or "")
                    prof = str(res.get("shippingProfileId") or "")
                    it_dict["live_pt"] = pt
                    it_dict["live_prof"] = prof
                    it_dict["is_online"] = True
                    discovered_cn_items.append(it_dict)
                except Exception as e:
                    it_dict["is_online"] = False

        await asyncio.gather(*[check_item(x) for x in folder_new_items])

    log.info("==================================================")
    log.info("EXTRA CHINESE AUDIT COMPLETE:")
    log.info("  * Total newly discovered online Chinese items on XL: %d", len(discovered_cn_items))
    from collections import Counter
    pt_counts = Counter(it.get("live_pt") for it in discovered_cn_items)
    print("Breakdown by current live processingTime:", pt_counts)
    by_fact = Counter(it["factory_name"] for it in discovered_cn_items)
    print("Breakdown by folder:", by_fact)
    log.info("==================================================")

    with open("/app/newly_discovered_cn_items.json", "w", encoding="utf-8") as out:
        json.dump(discovered_cn_items, out, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    asyncio.run(main())
