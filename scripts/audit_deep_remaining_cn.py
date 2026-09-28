import asyncio
import json
import logging
import os
import re
import sys
from collections import Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from sqlalchemy import select, func

# Exhaustive list of Chinese furniture hubs, cities, provinces, and markers
CN_KEYWORDS = [
    # Top furniture manufacturing clusters & cities
    r"\bfoshan\b", r"\bshunde\b", r"\blecong\b", r"\blongjiang\b", r"\bnanhai\b",
    r"\bdongguan\b", r"\bdalingshan\b", r"\bhoujie\b", r"\bshenzhen\b", r"\bguangzhou\b",
    r"\bguangdong\b", r"\bcanton\b", r"\bzhongshan\b", r"\bhuizhou\b",
    r"\banji\b", r"\bhangzhou\b", r"\bningbo\b", r"\bhaining\b", r"\byiwu\b", r"\bzhejiang\b",
    r"\bsuzhou\b", r"\bwuxi\b", r"\bchangzhou\b", r"\bpizhou\b", r"\bshuyang\b", r"\bjiangsu\b", r"\bshanghai\b",
    r"\bbazhou\b", r"\bshengfang\b", r"\blangfang\b", r"\bhebei\b",
    r"\bnankang\b", r"\bganzhou\b", r"\bjiangxi\b",
    r"\bchengdu\b", r"\bsichuan\b",
    r"\bxiamen\b", r"\bquanzhou\b", r"\banxi\b", r"\bxianyou\b", r"\bfujian\b",
    r"\bqingdao\b", r"\bshandong\b",
    # Country / Origin markers
    r"\bchina\b", r"\bchinesisch\b", r"\bchine\b", r"\bprc\b", r"\bp\.r\.c\b",
    r"made in china", r"hergestellt in china", r"ursprungsland.*china",
    # Specific factory name roots
    r"\bganasi\b", r"\bbiaodi\b", r"\byqc\b", r"\bdefaico\b", r"\bevergo\b",
    r"\bkadi\b", r"\blenatees\b", r"\bpengpai\b", r"\byoumi\b", r"\bweitao\b",
]

cn_re = re.compile("|".join(CN_KEYWORDS), re.IGNORECASE)

async def main():
    print("=== DEEP AUDIT: SEARCHING FOR CHINESE PRODUCTS BY CITY & MARKERS ===")

    # 1. Load all already updated SKUs (Turkish 38362 + Chinese 10204)
    updated_skus = set()
    for path in [
        "/app/tr_products_xl.json",
        "/app/tr_products_xl_missed_batch.json",
        "/app/tr_products_xl_batch3.json",
        "/app/tr_products_xl_batch4.json",
        "/app/tr_products_xl_batch5.json",
        "/app/cn_products_xl.json",
    ]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
                for fact in d.get("factories", []):
                    for it in fact.get("items", []):
                        s = str(it.get("ean") or it.get("sku") or "").strip()
                        if s:
                            updated_skus.add(s)
        except Exception:
            pass

    print(f"Total already updated unique SKUs (TR + CN): {len(updated_skus)}")

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

    # Search for Chinese keywords in name, ref, or raw_payload
    cn_matches = []
    for sku, ean, name, ref, raw in unupdated_items:
        raw_str = json.dumps(raw, ensure_ascii=False)
        combined_text = f"{name} {ref} {raw_str}"
        m = cn_re.search(combined_text)
        if m:
            cn_matches.append({
                "sku": sku or ean,
                "name": name[:70],
                "ref": ref,
                "matched_kw": m.group(0),
                "brand": raw.get("Marke", ""),
            })

    print(f"\nItems matching CHINESE cities/keywords among unupdated: {len(cn_matches)}")
    kw_counts = Counter(it["matched_kw"].lower() for it in cn_matches)
    for kw, cnt in kw_counts.most_common(20):
        print(f"  * Keyword '{kw}': {cnt} items")

    print("\nAll matched items:")
    for it in cn_matches:
        print(f"  - [{it['matched_kw']}] SKU: {it['sku']} | {it['name']} (Brand: {it['brand']})")


if __name__ == "__main__":
    asyncio.run(main())
