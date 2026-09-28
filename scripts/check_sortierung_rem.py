import asyncio
import json
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

VERIFIED_TR_DIRS = [
    "twins_life", "onno", "arnisa", "bohem_shezade", "muzaffer",
    "vrl_home", "pinus", "vnn_sholpan", "aracikan", "narista",
    "amazon_garden", "roll_furniture", "gk/klein", "luce_garden",
    "deer_furniture", "titi", "alber", "alite", "redof", "burosit",
    "favory", "radmila", "serhat", "zeyno", "edaga", "maduca", "siptar"
]

async def main():
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

    offset = 0
    limit = 250
    rem = []
    while True:
        resp = await afterbuy.send_request(
            "GET",
            "/api/products",
            params={
                "account": "xl",
                "dataset": "lister",
                "factory_id": "482963",
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
            if not any(m in pic for m in VERIFIED_TR_DIRS):
                rem.append({"sku": sku, "title": title, "pic": pic})
        if len(batch) < limit:
            break
        offset += limit

    print(f"Remaining in SORTIERUNG: {len(rem)}")
    for it in rem:
        print(f"  SKU: {it['sku']} | {it['title'][:50]} | {it['pic'][:80]}")

if __name__ == "__main__":
    asyncio.run(main())
