"""Scan OttoXlsxImportRow table for Turkish products on account XL."""

import asyncio
import json
import re
import sys
from collections import Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func, text
from app.database import SessionLocal, engine
from app.models.otto_xlsx_import import OttoXlsxImportRow
from scripts.tr_rules import ALL_TR_KEYWORDS

# Major Turkish furniture brands and markers
TR_BRANDS = {
    "merli", "alpino", "burobig", "vettero", "rixxe", "triodoor",
    "trio", "duga", "tuare", "verengarden", "safa", "modelin",
    "cisa", "kuka", "panda", "patroon", "sandalie", "brezza",
    "montro", "favory", "serhathome", "kaju", "eymense", "nott", "seyran",
    "gizetta", "vanitti", "luce_garden", "newsett", "alonex", "mse_bahce",
    "enter_sofa", "natura_outdoor", "zekioglu", "voguess", "vivanza",
    "woodface", "ulgen", "depotum", "bermonti", "wossew", "atolyekoltuk",
    "burosit", "bürosit", "acel", "saltanat", "saloni", "newberonni", "essence",
    "epika", "inci", "uyku", "mre", "soffi", "selimoglu", "boyneroglu",
    "arteluex", "sitis", "elif", "caca", "elegance", "hollywood", "vionse",
    "arwen", "tomi", "hafsam", "myway", "nevizada", "saka", "rotta", "somm"
}

async def scan_otto_rows():
    print("=== SCANNING OTTO_XLSX_IMPORT_ROWS FOR XL ACCOUNT ===", flush=True)

    # 1. Load already updated SKUs
    updated_skus = set()
    for path in ["/app/tr_products_xl.json", "/app/tr_products_xl_missed_batch.json"]:
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

    print(f"Total already updated SKUs on XL: {len(updated_skus)}", flush=True)

    # 2. Query distinct brands for XL where marketplace_status is online
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.brand, func.count(OttoXlsxImportRow.id))
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
            .group_by(OttoXlsxImportRow.brand)
            .order_by(func.count(OttoXlsxImportRow.id).desc())
        )
        brands = res.all()

    print(f"\nTotal distinct brands on OTTO XL Online: {len(brands)}", flush=True)

    matched_brands = []
    for b, cnt in brands:
        b_clean = (b or "").strip().lower()
        if any(tb in b_clean for tb in TR_BRANDS):
            matched_brands.append((b, cnt))

    print("\nTurkish brands found in OTTO XL Online catalog:")
    for b, cnt in matched_brands:
        print(f"  * {b}: {cnt} items")

    # 3. Find any SKUs of these Turkish brands that were NOT updated
    brand_names = [b for b, _ in matched_brands]
    unupdated_by_brand = []
    if brand_names:
        async with SessionLocal() as db:
            res = await db.execute(
                select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean, OttoXlsxImportRow.brand, OttoXlsxImportRow.product_name)
                .where(
                    func.lower(OttoXlsxImportRow.account) == "xl",
                    func.lower(OttoXlsxImportRow.marketplace_status) == "online",
                    OttoXlsxImportRow.brand.in_(brand_names),
                )
            )
            rows = res.all()
            for r in rows:
                s = str(r[0] or r[1] or "").strip()
                if s and s not in updated_skus:
                    unupdated_by_brand.append({
                        "sku": s,
                        "brand": r[2],
                        "product_name": r[3],
                    })

    print(f"\nUnupdated items among verified Turkish brands: {len(unupdated_by_brand)}")

    # 4. Search in raw_payload or product_name for Turkey markers among unupdated Online rows
    async with SessionLocal() as db:
        res = await db.execute(text("""
            SELECT sku, ean, brand, product_name
            FROM otto_xlsx_import_rows
            WHERE lower(account) = 'xl'
              AND lower(marketplace_status) = 'online'
              AND (
                  product_name ~* '(made in turkey|türkei|inegöl|bursa|kayseri)'
                  OR raw_payload::text ~* '\\mcountry\\s+TR\\M'
                  OR raw_payload::text ~* '\\mcity\\s+(INE|BUR|KAY|IST|ANK|IZM|ADA|GAZ)\\M'
              )
        """))
        tag_rows = res.all()

    unupdated_by_tags = []
    for r in tag_rows:
        s = str(r[0] or r[1] or "").strip()
        if s and s not in updated_skus:
            unupdated_by_tags.append({
                "sku": s,
                "brand": r[2],
                "product_name": r[3],
            })

    print(f"Unupdated items with explicit Turkey tags in raw_payload / name: {len(unupdated_by_tags)}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(scan_otto_rows())
