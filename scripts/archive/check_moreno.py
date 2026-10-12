import asyncio
import httpx
from app.core.configs import settings
from app.database import SessionLocal, engine
from sqlalchemy import select
from app.models.otto_xlsx_import import OttoXlsxImportRow

async def run():
    # 1. Login to Aftercool
    async with httpx.AsyncClient(timeout=30.0) as client:
        login_res = await client.post(
            f"{settings.afterbuy_base_url}/auth/login",
            json={"username": settings.afterbuy_username, "password": settings.afterbuy_password}
        )
        session = login_res.cookies.get("session")
        print(f"Logged in to Aftercool, session={session[:10]}...", flush=True)
        
        # 2. Fetch products for Moreno (501022)
        p_res = await client.get(
            f"{settings.afterbuy_base_url}/api/products",
            params={"account": "jv", "dataset": "lister", "factory_id": 501022, "include_row": 1, "limit": 10},
            cookies={"session": session}
        )
        data = p_res.json()
        items = data.get("items", [])
        total = data.get("total")
        print(f"Moreno items: total in Aftercool = {total}, returned = {len(items)}", flush=True)
        
        moreno_eans = []
        for it in items:
            row = it.get("row", {})
            ean = row.get("EAN") or row.get("ean") or row.get("sku")
            title = row.get("Artikelbeschreibung", "")
            if ean:
                moreno_eans.append(str(ean).strip())
            print(f"  * EAN: {ean} | Title: {title[:50]}", flush=True)
            
    # 3. Check DB
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean, OttoXlsxImportRow.marketplace_status)
            .where(OttoXlsxImportRow.account == "jv", OttoXlsxImportRow.ean.in_(moreno_eans))
        )
        rows = res.all()
        print(f"\nFound in OttoXlsxImportRow (JV): {len(rows)} / {len(moreno_eans)}", flush=True)
        for r in rows:
            print(f"  DB: EAN={r.ean}, Status={r.marketplace_status}", flush=True)
            
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(run())
