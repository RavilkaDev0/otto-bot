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

# Strong Turkish keywords
TURKISH_KEYWORDS = [
    r"\binegöl\b", r"\binegol\b", r"\bbursa\b", r"\bkayseri\b", r"\bistanbul\b",
    r"\bturkey\b", r"\btürkei\b", r"\bturkei\b", r"\btürkisch\b", r"\bturkisch\b",
    r"\bturkish\b", r"\bkoltuk\b", r"\byatak\b", r"\bsehpa\b", r"\bsandalye\b",
    r"\bmasa\b", r"\bceviz\b", r"\bmeşe\b", r"\bbaza\b", r"\bbesiktas\b",
    r"\bfenerbahce\b", r"\bgalatasaray\b", r"\btimur\b", r"\bistikbal\b",
    r"\bbellona\b", r"\byatas\b", r"\byataş\b", r"\bdogtas\b", r"\bdoğtaş\b",
    r"\bkelebek\b", r"\benza\b", r"\bmoda\b.*\blife\b", r"\balber\b",
    r"\balite\b", r"\bredof\b", r"\bsoffi\b", r"\bnills\b", r"\bbussy\b",
    r"\btiti\b", r"\barnisa\b", r"\bonno\b", r"\bmuzaffer\b", r"\bpinus\b"
]

tr_re = re.compile("|".join(TURKISH_KEYWORDS), re.IGNORECASE)

async def main():
    print("=== AUDIT: CHECKING IF ANY TURKISH PRODUCTS REMAIN IN DB ===")

    # 1. Load all already updated SKUs across all 5 batches
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

    print(f"Total already updated unique SKUs: {len(updated_skus)}")

    # 2. Inspect all unupdated online rows in DB
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
        all_online = res.all()

    print(f"Total online XL items in DB: {len(all_online)}")

    unupdated_items = []
    for sku, ean, name, ref, raw in all_online:
        sku_clean = str(sku or "").strip()
        ean_clean = str(ean or "").strip()
        if sku_clean in updated_skus or ean_clean in updated_skus:
            continue
        unupdated_items.append((sku_clean, ean_clean, name or "", ref or "", raw or {}))

    print(f"Total unupdated online XL items: {len(unupdated_items)}")

    # Search for Turkish keywords in name, ref, or raw_payload
    tr_matches = []
    for sku, ean, name, ref, raw in unupdated_items:
        raw_str = json.dumps(raw, ensure_ascii=False)
        combined_text = f"{name} {ref} {raw_str}"
        m = tr_re.search(combined_text)
        if m:
            tr_matches.append({
                "sku": sku or ean,
                "name": name[:70],
                "ref": ref,
                "matched_kw": m.group(0),
            })

    print(f"\nItems matching TURKISH keywords among unupdated: {len(tr_matches)}")
    kw_counts = Counter(it["matched_kw"].lower() for it in tr_matches)
    for kw, cnt in kw_counts.most_common(20):
        print(f"  * Keyword '{kw}': {cnt} items")

    print("\nSample matched items:")
    for it in tr_matches[:15]:
        print(f"  - [{it['matched_kw']}] SKU: {it['sku']} | {it['name']}")

if __name__ == "__main__":
    asyncio.run(main())
