import asyncio
import json
import re
import sys
from collections import defaultdict, Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func, text
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow

async def main():
    print("================================================================")
    print("  DEEP AUDIT FOR ANY REMAINING TURKISH PRODUCTS ON OTTO XL")
    print("================================================================")

    # 1. Collect all updated SKUs across Batch 1, 2, 3, 4
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
        except Exception as e:
            print(f"Notice: {path}: {e}")

    print(f"Total verified updated SKUs on XL: {len(updated_skus)}")

    # 2. Check all Afterbuy folders in all_afterbuy_xl_factories.json
    with open("/app/all_afterbuy_xl_factories.json") as f:
        all_ab_factories = json.load(f)

    print(f"Total Afterbuy folders in file: {len(all_ab_factories)}")

    # 3. Check DB for XL rows
    async with SessionLocal() as db:
        # Sample raw_payload
        sample_row = await db.execute(
            select(OttoXlsxImportRow).where(func.lower(OttoXlsxImportRow.account) == "xl").limit(1)
        )
        s_obj = sample_row.scalar_one_or_none()
        if s_obj:
            print("Sample raw_payload keys:", list(s_obj.raw_payload.keys()))

        # Total online rows for XL
        total_online = await db.scalar(
            select(func.count(OttoXlsxImportRow.id)).where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        print(f"Total Online items in DB for XL: {total_online}")

        # Non-updated online rows in DB
        res = await db.execute(
            select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean, OttoXlsxImportRow.brand, OttoXlsxImportRow.product_name)
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        all_online = res.all()

    not_updated = []
    brand_counts_not_updated = Counter()
    for sku, ean, brand, name in all_online:
        sku_clean = str(sku or "").strip()
        ean_clean = str(ean or "").strip()
        if (sku_clean and sku_clean in updated_skus) or (ean_clean and ean_clean in updated_skus):
            continue
        not_updated.append({"sku": sku_clean, "ean": ean_clean, "brand": brand, "name": name})
        brand_counts_not_updated[brand or "UNKNOWN"] += 1

    print(f"Total Online XL items NOT in our updated list: {len(not_updated)}")
    print(f"Top 30 brands among NOT updated items:")
    for b, c in brand_counts_not_updated.most_common(30):
        print(f"  - {b}: {c}")

    # 4. Check Afterbuy folders: find folders that were NOT touched and see if they look Turkish
    updated_folder_names = set()
    with open("/app/factories_xl.txt", "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\d+\.\s+(.+?)\s+—\s+(\d+)", line)
            if m:
                updated_folder_names.add(m.group(1).strip().lower())

    print(f"Total factory names in factories_xl.txt: {len(updated_folder_names)}")

    untouched_folders = []
    for f in all_ab_factories:
        name = f.get("name", "").strip()
        cnt = f.get("items_count", 0)
        if cnt == 0:
            continue
        if name.lower() not in updated_folder_names:
            untouched_folders.append(f)

    print(f"Total Afterbuy folders with items > 0 NOT in factories_xl.txt: {len(untouched_folders)}")
    print("Listing untouched Afterbuy folders:")
    for f in sorted(untouched_folders, key=lambda x: x.get("items_count", 0), reverse=True):
        print(f"  * ID {f['id']}: '{f['name']}' ({f.get('items_count', 0)} items in Afterbuy)")

if __name__ == "__main__":
    asyncio.run(main())
