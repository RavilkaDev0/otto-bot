import asyncio
import os
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.database import SessionLocal, engine
from sqlalchemy import select
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.clients.external_otto_client import ExternalOttoClient
from app.schemas.external_schemes.until_schemes import GetProductRequest

async def test():
    client = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await client.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
    )
    otto = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)

    for fid, name in [("504104", "Standard_7"), ("504135", "Standard_8"), ("504190", "Standard_9")]:
        resp = await client.send_request("GET", "/api/products", params={"account": "jv", "dataset": "lister", "factory_id": fid, "limit": 10, "include_row": 1}, cookies={"session": session})
        items = resp.json().get("items", [])
        print(f"\n--- {name} (ID: {fid}) sample items: ---", flush=True)
        for it in items[:3]:
            row = it.get("row", {})
            ean = str(row.get("EAN") or row.get("sku") or "").strip()
            title = str(row.get("Artikelbeschreibung") or "")[:40]
            print(f"Item EAN: {ean} | Title: {title}", flush=True)
            
            # Check in local DB across ANY account
            async with SessionLocal() as db:
                res = await db.execute(select(OttoXlsxImportRow.account, OttoXlsxImportRow.marketplace_status).where(OttoXlsxImportRow.ean == ean))
                rows = res.all()
                print(f"  Local DB: {rows}", flush=True)
                
            # Check OTTO JV
            try:
                res_jv = await otto.get_products(GetProductRequest(sku=ean), "jv")
                vars_jv = res_jv.get("productVariations", [])
                if vars_jv:
                    print(f"  OTTO JV: FOUND! deliveryTime={vars_jv[0].get('delivery', {}).get('deliveryTime')}", flush=True)
                else:
                    print(f"  OTTO JV: NOT FOUND", flush=True)
            except Exception as e:
                print(f"  OTTO JV error: {e}", flush=True)

            # Check OTTO XL
            try:
                res_xl = await otto.get_products(GetProductRequest(sku=ean), "xl")
                vars_xl = res_xl.get("productVariations", [])
                if vars_xl:
                    print(f"  OTTO XL: FOUND! deliveryTime={vars_xl[0].get('delivery', {}).get('deliveryTime')}", flush=True)
                else:
                    print(f"  OTTO XL: NOT FOUND", flush=True)
            except Exception as e:
                print(f"  OTTO XL error: {e}", flush=True)

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(test())
