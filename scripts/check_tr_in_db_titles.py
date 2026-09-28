import asyncio
import json
import re
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow

TR_WORDS = [
    r"\(tr\)", r"_tr\b", r"_tr_", r"\(tr\b",
    r"\bmobilya\b", r"\bkoltuk\b", r"\bsandalya\b", r"\bsehpa\b",
    r"\byatak\b", r"\byemek\s+odasi\b", r"\byatak\s+odasi\b",
    r"\bberjer\b", r"\bkaryola\b", r"\bbahce\b", r"\bofis\b",
    r"\binegöl\b", r"\binegol\b", r"\bkayseri\b", r"\bbursa\b"
]

async def check():
    # Load already updated SKUs
    updated_skus = set()
    for path in ["/app/tr_products_xl.json", "/app/tr_products_xl_missed_batch.json", "/app/tr_products_xl_batch3.json"]:
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
            select(OttoXlsxImportRow.ean, OttoXlsxImportRow.sku, OttoXlsxImportRow.product_name, OttoXlsxImportRow.brand)
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        rows = res.all()

    found_tr_in_title = []
    for ean, sku, pname, brand in rows:
        key = str(ean or sku or "").strip()
        if not key or key in updated_skus:
            continue
        pname_str = str(pname or "")
        for pat in TR_WORDS:
            if re.search(pat, pname_str, re.I):
                found_tr_in_title.append((key, pname_str, brand, pat))
                break

    print(f"Total non-updated Online items with Turkish markers in title: {len(found_tr_in_title)}")
    for k, p, b, pat in found_tr_in_title[:25]:
        print(f"  [{k}] (matched {pat}, brand: {b}): {p[:70]}")

if __name__ == "__main__":
    asyncio.run(check())
