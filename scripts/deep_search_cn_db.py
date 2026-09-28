import asyncio
import json
import re
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from sqlalchemy import select, func

# Load all known Chinese SKUs (11,291 + 386 = 11,677)
all_known_cn = set()
for path in [
    "/app/cn_products_xl.json",
    "/app/cn_in_tr_batches_found.json",
    "/app/timur_cn_to_update_12w.json",
    "/app/newly_discovered_cn_items.json"
]:
    try:
        with open(path) as f:
            d = json.load(f)
            for it in d.get("items", []):
                s = str(it.get("ean") or it.get("sku") or "").strip()
                if s: all_known_cn.add(s)
            for fact in d.get("factories", []):
                for it in fact.get("items", []):
                    s = str(it.get("ean") or it.get("sku") or "").strip()
                    if s: all_known_cn.add(s)
    except Exception:
        pass

print(f"Total known Chinese SKUs: {len(all_known_cn)}")

CN_PATTERNS = [
    r"\bchina\b", r"\bchinesisch\b", r"\bmade in china\b", r"\baus china\b",
    r"\bfoshan\b", r"\bdongguan\b", r"\bshenzhen\b", r"\bguangzhou\b",
    r"\bganasi\b", r"\blenatees\b", r"\bkuka\b", r"\bpengpai\b", r"\bbiaodi\b",
    r"\byoubond\b", r"\byqc\b", r"\bevergo\b", r"\bweitao\b", r"\byoumi\b",
    r"country\s+cn\b"
]
cn_re = re.compile("|".join(CN_PATTERNS), re.IGNORECASE)

async def main():
    async with SessionLocal() as db:
        res = await db.execute(
            select(
                OttoXlsxImportRow.sku,
                OttoXlsxImportRow.ean,
                OttoXlsxImportRow.product_name,
                OttoXlsxImportRow.product_reference,
                OttoXlsxImportRow.raw_payload,
            )
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        rows = res.all()

    print(f"Total online XL rows in DB: {len(rows)}")
    matched_unknown = []

    for s, e, name, ref, raw in rows:
        sku = str(s or "").strip()
        ean = str(e or "").strip()
        if sku in all_known_cn or ean in all_known_cn:
            continue

        raw_str = json.dumps(raw or {}, ensure_ascii=False)
        full_text = f"{name} {ref} {raw_str}"
        m = cn_re.search(full_text)
        if m:
            matched_unknown.append({
                "sku": sku or ean,
                "name": (name or "")[:70],
                "ref": ref,
                "matched": m.group(0),
                "brand": (raw or {}).get("Marke", "")
            })

    print(f"\nDiscovered unknown potential Chinese matches in DB: {len(matched_unknown)}")
    for it in matched_unknown[:20]:
        print(f"  * [{it['matched']}] SKU: {it['sku']} | Name: {it['name']} | Brand: {it['brand']}")

if __name__ == "__main__":
    asyncio.run(main())
