import asyncio
import json
import sys
from collections import Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow

async def main():
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

    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean, OttoXlsxImportRow.brand, OttoXlsxImportRow.product_name)
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        all_online = res.all()

    not_updated = []
    brand_counts = Counter()
    for sku, ean, brand, name in all_online:
        sku_clean = str(sku or "").strip()
        ean_clean = str(ean or "").strip()
        if (sku_clean and sku_clean in updated_skus) or (ean_clean and ean_clean in updated_skus):
            continue
        not_updated.append({"sku": sku_clean, "ean": ean_clean, "brand": brand, "name": name})
        brand_counts[brand or "UNKNOWN"] += 1

    print(f"Total Online XL items in DB: {len(all_online)}")
    print(f"Total Online XL items ALREADY updated in Batches 1-4: {len(all_online) - len(not_updated)}")
    print(f"Total Online XL items NOT in our updated list: {len(not_updated)}")
    print("\nTop 40 brands among NOT updated items:")
    for b, c in brand_counts.most_common(40):
        print(f"  {b}: {c}")

if __name__ == "__main__":
    asyncio.run(main())
