import asyncio
import json
import re
import sys
from collections import defaultdict, Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

# Comprehensive list of Turkish factory substrings in image URLs
TR_SUBSTRINGS = [
    "twins_life", "onno", "arnisa", "bohem_shezade", "muzaffer",
    "vrl_home", "pinus", "vnn_sholpan", "aracikan", "narista",
    "amazon_garden", "roll_furniture", "gk/klein", "luce_garden",
    "deer_furniture", "titi", "alber", "alite", "redof", "burosit",
    "favory", "radmila", "serhat", "zeyno", "edaga", "maduca", "siptar",
    "sitis_tr", "louhome", "kopru", "sanat", "idollife", "legno",
    "area_mobilya", "bussy", "soffi", "moreno", "konfull", "selimoglu",
    "dekorister", "caginofis", "ulgen", "ressa", "biofis", "assarasofa",
    "kuka_home", "gureler", "larex", "tonza", "dasell", "saloni"
]

async def main():
    print("=== SEARCHING ALL UNMARKED FOLDERS FOR TURKISH PRODUCTS VIA IMAGE PATHS ===")

    # 1. Load online DB set
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.ean, OttoXlsxImportRow.sku)
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        online_db_items = res.all()

    online_db_set = set()
    for ean, sku in online_db_items:
        if ean:
            online_db_set.add(str(ean).strip())
        if sku:
            online_db_set.add(str(sku).strip())

    # 2. Load already updated
    updated_skus = set()
    for path in [
        "/app/tr_products_xl.json",
        "/app/tr_products_xl_missed_batch.json",
        "/app/tr_products_xl_batch3.json",
        "/app/tr_products_xl_batch4.json",
    ]:
        try:
            with open(path) as f:
                d = json.load(f)
                for fact in d.get("factories", []):
                    for it in fact.get("items", []):
                        s = str(it.get("ean") or it.get("sku") or "").strip()
                        if s:
                            updated_skus.add(s)
        except Exception:
            pass

    with open("/app/unupdated_online_scan_all_factories.json") as f:
        all_unupdated_folders = json.load(f)

    # Filter out explicit Non-TR folders
    unmarked_folders = {
        k: v for k, v in all_unupdated_folders.items()
        if not v.get("is_explicit_non_tr") and not v.get("is_tr")
    }
    print(f"Total unmarked folders to inspect: {len(unmarked_folders)}")

    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    found_by_tr_factory = defaultdict(list)
    found_by_folder = defaultdict(list)

    for fname, info in unmarked_folders.items():
        fid = info["factory_id"]
        offset = 0
        limit = 250
        while True:
            resp = await afterbuy.send_request(
                "GET",
                "/api/products",
                params={
                    "account": "xl",
                    "dataset": "lister",
                    "factory_id": fid,
                    "limit": limit,
                    "offset": offset,
                    "include_row": 1,
                },
                cookies={"session": session},
            )
            batch = resp.json().get("items", [])
            if not batch:
                break
            for it in batch:
                row = it.get("row", {})
                sku = str(row.get("EAN") or row.get("sku") or "").strip()
                if not sku or sku not in online_db_set or sku in updated_skus:
                    continue
                pic = str(row.get("PictureURL") or row.get("pictureurls") or "").lower()
                title = str(row.get("Artikelbeschreibung") or "")

                matched_tr = None
                for sub in TR_SUBSTRINGS:
                    if sub in pic:
                        matched_tr = sub
                        break

                if matched_tr:
                    found_item = {
                        "sku": sku,
                        "ean": sku,
                        "title": title[:60],
                        "folder_name": fname,
                        "folder_id": fid,
                        "tr_brand": matched_tr,
                        "pic": pic[:90]
                    }
                    found_by_tr_factory[matched_tr].append(found_item)
                    found_by_folder[fname].append(found_item)

            if len(batch) < limit:
                break
            offset += limit

    total_found = sum(len(v) for v in found_by_tr_factory.values())
    print("\n================================================================")
    print(f"SEARCH RESULTS: Found {total_found} TURKISH ITEMS in {len(found_by_folder)} UNMARKED FOLDERS!")
    print("================================================================")

    print("\nBreakdown by Turkish Brand / Factory:")
    for tr_brand, items in sorted(found_by_tr_factory.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"  * {tr_brand}: {len(items)} items")

    print("\nBreakdown by Afterbuy Folder where they were found:")
    for fn, items in sorted(found_by_folder.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"  * Folder '{fn}': {len(items)} Turkish items")

    # Save to JSON
    with open("/app/hidden_tr_found_unmarked.json", "w", encoding="utf-8") as out:
        json.dump({
            "total_items": total_found,
            "by_tr_brand": {k: len(v) for k, v in found_by_tr_factory.items()},
            "by_folder": {k: len(v) for k, v in found_by_folder.items()},
            "items": [it for sublist in found_by_tr_factory.values() for it in sublist]
        }, out, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
