import asyncio
import json
import re
import sys
from collections import defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

# Load known TR patterns
KNOWN_TR_PATTERNS = [
    r"\(tr\)", r"_tr\b", r"_tr_", r"\(tr\b",
    r"timur", r"favory", r"radmila", r"serhat", r"alpino",
    r"modelin", r"kaju", r"eymense", r"tuare", r"montro",
    r"voguess", r"ropez", r"katre", r"epika", r"inci",
    r"sandalie", r"brezza", r"soffi", r"selimoglu", r"boyneroglu",
    r"burosit", r"bürosit", r"bussy", r"redof", r"alber",
    r"alite", r"nills", r"titi", r"uyku", r"vionse", r"duga",
    r"arwen", r"tomi", r"hafsam", r"myway", r"nevizada",
    r"saka", r"rotta", r"somm", r"zeta", r"varisse", r"cchm",
    r"bogao", r"alist"
]

def is_candidate_tr_factory(name: str) -> bool:
    low = name.lower()
    if "others_löschen" in low or "others_loschen" in low:
        return False
    if "_(pl)" in low or "_(it)" in low or "_(cn)" in low or "_(de)" in low or "_(uk)" in low:
        return False
    for pat in KNOWN_TR_PATTERNS:
        if re.search(pat, low):
            return True
    return False

async def main():
    print("=== CROSS-REFERENCING ALL AFTERBUY TR FACTORIES WITH OTTO XL ONLINE IN DB ===")

    # 1. Load all online EANs for XL from DB
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.ean, OttoXlsxImportRow.sku)
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        online_db_items = res.all()

    online_db_set = set()
    for ean, sku in online_db_items:
        if ean:
            online_db_set.add(str(ean).strip())
        if sku:
            online_db_set.add(str(sku).strip())

    print(f"Total distinct Online SKUs/EANs in DB for XL: {len(online_db_set)}")

    # 2. Load already updated SKUs in factories_xl.txt across Batch 1, 2, 3
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

    print(f"Total already updated SKUs in Batches 1, 2, 3: {len(updated_skus)}")

    # 3. Load Afterbuy factories
    with open("/app/all_afterbuy_xl_factories.json") as f:
        all_f = json.load(f)

    cands = [f for f in all_f if f.get("items_count", 0) > 0 and is_candidate_tr_factory(f.get("name", ""))]
    print(f"Total candidate TR factories in Afterbuy: {len(cands)}")

    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    missed_online_by_factory = defaultdict(list)
    total_missed_online = 0

    for idx, fact in enumerate(cands, 1):
        fid = fact["id"]
        fname = fact["name"]
        cnt = fact.get("items_count", 0)

        # Paginated fetch from Afterbuy
        offset = 0
        limit = 250
        while True:
            resp = await afterbuy.send_request(
                "GET",
                "/api/products",
                params={
                    "account": "xl",
                    "dataset": "lister",
                    "factory_id": fid,
                    "limit": limit,
                    "offset": offset,
                    "include_row": 1,
                },
                cookies={"session": session},
            )
            batch = resp.json().get("items", [])
            if not batch:
                break
            for it in batch:
                row = it.get("row", {})
                sku = str(row.get("EAN") or row.get("sku") or "").strip()
                if not sku:
                    continue
                # Check if it is Online in OTTO DB, but NOT in updated_skus!
                if sku in online_db_set and sku not in updated_skus:
                    missed_online_by_factory[fname].append({
                        "sku": sku,
                        "ean": sku,
                        "title": str(row.get("Artikelbeschreibung") or "")[:60],
                        "factory_name": fname,
                        "factory_id": fid,
                    })
                    total_missed_online += 1

            if len(batch) < limit:
                break
            offset += limit

    print("\n=======================================================")
    print(f"SCAN COMPLETED!")
    print(f"Total MISSED Online Turkish items found in DB: {total_missed_online}")
    print(f"Across {len(missed_online_by_factory)} factories")
    print("=======================================================")

    for fn, items in sorted(missed_online_by_factory.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"  * {fn}: {len(items)} items")

    with open("/app/missed_online_tr_xl.json", "w", encoding="utf-8") as out:
        json.dump({
            "total_missed": total_missed_online,
            "factories_count": len(missed_online_by_factory),
            "by_factory": {fn: len(items) for fn, items in missed_online_by_factory.items()},
            "items": [it for items in missed_online_by_factory.values() for it in items],
        }, out, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
