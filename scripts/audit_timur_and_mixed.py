import asyncio
import json
import logging
import os
import re
import sys
from collections import Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from sqlalchemy import select, func

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("audit_mixed")

async def main():
    log.info("Starting deep audit of Timur folders and other mixed folders on XL...")

    # 1. Load online SKUs for XL from DB
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean, OttoXlsxImportRow.product_name)
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        rows = res.all()

    online_skus = set()
    sku_to_title = {}
    for s, e, title in rows:
        if s:
            s_str = str(s).strip()
            online_skus.add(s_str)
            if title: sku_to_title[s_str] = title
        if e:
            e_str = str(e).strip()
            online_skus.add(e_str)
            if title and e_str not in sku_to_title: sku_to_title[e_str] = title

    log.info("Total online SKUs in DB for XL: %d", len(online_skus))

    # 2. Connect to Afterbuy
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=60.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    with open("/app/all_afterbuy_xl_factories.json", "r", encoding="utf-8") as f:
        all_facts = json.load(f)

    # Find target folders: all Timur folders, Kuka, NEW_ROPEZ_SPECIAL, Others_MixFabriks
    target_folders = []
    for f in all_facts:
        name = f.get("name", "").strip()
        low = name.lower()
        if (
            "timur" in low
            or "kuka" in low
            or "new_ropez" in low
            or "mixfabrik" in low
        ):
            target_folders.append(f)

    log.info("Found %d target folders to analyze in detail:", len(target_folders))
    for f in target_folders:
        log.info("  * ID %s: %s", f.get("id"), f.get("name"))

    folder_analyses = []

    for f in target_folders:
        fid = f.get("id")
        fname = f.get("name")
        log.info("Fetching items for %s (ID %s)...", fname, fid)
        try:
            resp = await ab.send_request(
                "GET",
                "/api/products",
                params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
                cookies={"session": session},
            )
            items = resp.json().get("items", [])
            log.info("  -> Got %d total items in AB for %s", len(items), fname)

            supplier_counts = Counter()
            supplier_items = {}

            for it in items:
                row = it.get("row", {})
                sku = str(row.get("EAN") or row.get("sku") or "").strip()
                if not sku or sku not in online_skus:
                    continue

                gurl = str(row.get("GalleryURL") or row.get("PictureURL") or "")
                
                # Extract supplier from URL
                supplier = "UNKNOWN"
                m = re.search(r"AfterbuyUploadService/([^/]+)/", gurl)
                if m:
                    supplier = m.group(1).strip()
                else:
                    m2 = re.search(r"https?://[^/]+/([^/]+)/", gurl)
                    if m2:
                        supplier = m2.group(1).strip()

                supplier_counts[supplier] += 1
                if supplier not in supplier_items:
                    supplier_items[supplier] = []
                supplier_items[supplier].append({
                    "sku": sku,
                    "title": sku_to_title.get(sku, row.get("Artikelbeschreibung", "")),
                    "gurl": gurl[:100]
                })

            log.info("  -> Found %d online items on XL across %d suppliers in %s", sum(supplier_counts.values()), len(supplier_counts), fname)
            for supp, cnt in supplier_counts.most_common(15):
                log.info("     %s: %d items", supp, cnt)

            folder_analyses.append({
                "factory_id": fid,
                "factory_name": fname,
                "total_ab_items": len(items),
                "total_online_items": sum(supplier_counts.values()),
                "suppliers": {
                    supp: {
                        "count": cnt,
                        "sample_url": supplier_items[supp][0]["gurl"] if supplier_items[supp] else "",
                        "sample_sku": supplier_items[supp][0]["sku"] if supplier_items[supp] else "",
                        "sample_title": supplier_items[supp][0]["title"][:60] if supplier_items[supp] else "",
                        "skus": [x["sku"] for x in supplier_items[supp]]
                    }
                    for supp, cnt in supplier_counts.items()
                }
            })

        except Exception as ex:
            log.error("Error fetching %s: %s", fname, ex)

    # Save complete analysis
    with open("/app/timur_and_mixed_analysis.json", "w", encoding="utf-8") as out:
        json.dump(folder_analyses, out, indent=2, ensure_ascii=False)

    log.info("Saved complete analysis to /app/timur_and_mixed_analysis.json")

if __name__ == "__main__":
    asyncio.run(main())
