import asyncio
import json
import re
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow

async def main():
    # 1. Load all 351 factory names
    factories = []
    with open("/app/factories_xl.txt") as f:
        for line in f:
            m = re.match(r"^\d+\.\s+(.+?)\s+—\s+(\d+)", line)
            if m:
                fn = m.group(1).lower()
                clean = re.sub(r"\s*\(tr\).*", "", fn).strip()
                clean = re.sub(r"\s*20\.12\.24.*", "", clean).strip()
                if len(clean) >= 4 and clean not in ["home", "plus", "gold", "room", "style"]:
                    factories.append(clean)

    print(f"Loaded {len(factories)} distinct Turkish factory name roots to cross-check")

    # 2. Load all updated SKUs
    updated_skus = set()
    for path in [
        "/app/tr_products_xl.json",
        "/app/tr_products_xl_missed_batch.json",
        "/app/tr_products_xl_batch3.json",
        "/app/tr_products_xl_batch4.json",
        "/app/tr_products_xl_batch5.json",
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

    # 3. Query all unupdated items in DB
    async with SessionLocal() as db:
        res = await db.execute(
            select(
                OttoXlsxImportRow.sku,
                OttoXlsxImportRow.ean,
                OttoXlsxImportRow.product_name,
                OttoXlsxImportRow.product_reference,
            )
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        rows = res.all()

    unupdated = [r for r in rows if str(r[0] or "").strip() not in updated_skus and str(r[1] or "").strip() not in updated_skus]
    print(f"Total unupdated items in DB: {len(unupdated)}")

    matches = []
    for sku, ean, name, ref in unupdated:
        text = f"{name or ''} {ref or ''}".lower()
        for fn in factories:
            if re.search(r"\b" + re.escape(fn) + r"\b", text):
                matches.append((sku or ean, fn, name[:60]))
                break

    print(f"\nMatches found for any of the 351 Turkish factory names among unupdated items: {len(matches)}")
    for s, fn, nm in matches[:20]:
        print(f"  * Matched '{fn}': SKU {s} | {nm}")

if __name__ == "__main__":
    asyncio.run(main())
