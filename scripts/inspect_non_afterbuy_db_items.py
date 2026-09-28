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
    print("=== INSPECTING ONLINE ITEMS IN DB NOT IN ANY AFTERBUY FOLDER ===")

    # 1. Load all EANs found in all Afterbuy factories
    # We can check by loading all updated SKUs + all items from unupdated_online_scan_all_factories.json
    all_ab_online_skus = set()
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
                            all_ab_online_skus.add(s)
        except Exception:
            pass

    with open("/app/unupdated_online_scan_all_factories.json") as f:
        unup_data = json.load(f)
        for fn, info in unup_data.items():
            for it in info.get("sample_items", []):
                all_ab_online_skus.add(it["sku"])

    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean, OttoXlsxImportRow.product_name, OttoXlsxImportRow.product_category, OttoXlsxImportRow.source_file)
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        all_online = res.all()

    not_in_ab = []
    source_files = Counter()
    categories = Counter()

    for sku, ean, name, cat, s_file in all_online:
        sku_clean = str(sku or "").strip()
        ean_clean = str(ean or "").strip()
        if sku_clean in all_ab_online_skus or ean_clean in all_ab_online_skus:
            continue
        not_in_ab.append({"sku": sku_clean, "ean": ean_clean, "name": name, "cat": cat})
        source_files[s_file or "UNKNOWN"] += 1
        categories[cat or "UNKNOWN"] += 1

    print(f"Total online items in DB: {len(all_online)}")
    print(f"Sample source files in DB:")
    for sf, c in source_files.most_common(10):
        print(f"  * {sf}: {c}")

    print(f"\nTop 15 categories for these items:")
    for cat, c in categories.most_common(15):
        print(f"  * {cat}: {c}")

    print(f"\nSample product names:")
    for it in not_in_ab[:10]:
        print(f"  - SKU {it['sku']}: {it['name']}")

if __name__ == "__main__":
    asyncio.run(main())
