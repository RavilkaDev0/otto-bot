import asyncio
import json
import re
import sys
from collections import Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow

async def main():
    # Load updated SKUs
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
            select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean, OttoXlsxImportRow.product_reference)
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        rows = res.all()

    prefixes = Counter()
    total_unupdated = 0
    for sku, ean, ref in rows:
        sku_clean = str(sku or "").strip()
        ean_clean = str(ean or "").strip()
        if (sku_clean and sku_clean in updated_skus) or (ean_clean and ean_clean in updated_skus):
            continue
        total_unupdated += 1
        ref_str = str(ref or "").strip()
        # Find prefix before '_' or '-'
        parts = re.split(r"[-_]", ref_str)
        if parts:
            prefixes[parts[0].upper()] += 1

    print(f"Total unupdated online rows: {total_unupdated}")
    print("Top 50 product_reference prefixes among unupdated:")
    for p, c in prefixes.most_common(50):
        print(f"  {p}: {c}")

if __name__ == "__main__":
    asyncio.run(main())
