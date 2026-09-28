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

# Explicit verified Turkish factory directory markers in URLs
VERIFIED_TR_DIRS = [
    "twins_life", "onno", "arnisa", "bohem_shezade", "muzaffer",
    "vrl_home", "pinus", "vnn_sholpan", "aracikan", "narista",
    "amazon_garden", "roll_furniture", "gk/klein", "luce_garden",
    "deer_furniture", "titi", "alber", "alite", "redof", "burosit",
    "favory", "radmila", "serhat", "zeyno", "edaga", "maduca", "siptar"
]

async def main():
    print("=== EXACT VERIFICATION OF SORTIERUNG & UK FOLDERS FOR VERIFIED TR DIRECTORIES ===")

    # 1. Load online DB set
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

    # 2. Load already updated
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

    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    # Check SORTIERUNG (482963), UK-Ben-Company (482506), UK-SCORZANYSWIAT (482442), UK-Comad (482458)
    check_folders = [
        ("SORTIERUNG", "482963"),
        ("UK-Ben-Company", "482506"),
        ("UK-SCORZANYSWIAT", "482442"),
        ("UK-Comad", "482458"),
    ]

    tr_found = defaultdict(list)

    for fname, fid in check_folders:
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
                if not sku or sku not in online_db_set or sku in updated_skus:
                    continue
                pic = str(row.get("PictureURL") or row.get("pictureurls") or "").lower()
                title = str(row.get("Artikelbeschreibung") or "")

                for m in VERIFIED_TR_DIRS:
                    if m in pic:
                        tr_found[f"{fname} -> {m}"].append({
                            "sku": sku,
                            "title": title[:60],
                            "pic": pic[:90]
                        })
                        break

            if len(batch) < limit:
                break
            offset += limit

    total_verified = sum(len(v) for v in tr_found.values())
    print(f"\nTOTAL VERIFIED TURKISH ITEMS IN THESE UNMARKED FOLDERS: {total_verified}")
    for k, items in sorted(tr_found.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"  * {k}: {len(items)} items")
        for it in items[:2]:
            print(f"      SKU: {it['sku']} | {it['title']} | {it['pic']}")

if __name__ == "__main__":
    asyncio.run(main())
