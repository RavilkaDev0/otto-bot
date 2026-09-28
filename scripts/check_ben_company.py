import asyncio
import json
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings
from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow

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

    resp = await afterbuy.send_request(
        "GET",
        "/api/products",
        params={
            "account": "xl",
            "dataset": "lister",
            "factory_id": "482506", # UK-Ben-Company
            "limit": 10,
            "include_row": 1,
        },
        cookies={"session": session},
    )
    items = resp.json().get("items", [])
    print(f"UK-Ben-Company total sample items: {len(items)}")
    for it in items[:5]:
        row = it.get("row", {})
        sku = str(row.get("EAN") or row.get("sku") or "").strip()
        print(f"  SKU: {sku} | in online_db: {sku in online_db_set} | in updated_skus: {sku in updated_skus}")
        print(f"  Pic: {row.get('PictureURL')}")

if __name__ == "__main__":
    asyncio.run(main())
