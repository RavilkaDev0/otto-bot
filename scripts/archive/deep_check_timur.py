"""Deep check of Timur factories (II, 3, 4, 5, 6, 7, 8, 9) and other potential uncounted TR products."""

import asyncio
import json
import sys
import os
import re

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.core.configs import settings
from app.database import SessionLocal, engine
from sqlalchemy import select, func
from app.models.factories import Factories
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.schemas.external_schemes.until_schemes import GetProductRequest

async def main():
    print("=== DEEP CHECK: Timur Factories & Potential Missed TR Products ===\n", flush=True)

    # 1. Check all Timur factories in DB
    async with SessionLocal() as db:
        res = await db.execute(select(Factories).where(Factories.name.ilike("%Timur%")))
        all_timur = res.scalars().all()
        
    print(f"Total Timur factories in DB: {len(all_timur)}")
    for f in all_timur:
        print(f"  * ID: {f.factory_id} | Name: '{f.name}' | Account: {f.account} | Items: {f.items_count}")
        
    # Connect to Aftercool
    client = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await client.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    # Check if there is any Standard 9 in Aftercool
    print("\nChecking if 'Timur_for_Otto_Standard_9' or similar exists in Aftercool...", flush=True)
    resp_search = await client.send_request(
        "GET",
        "/api/factories",
        params={"account": "jv"},
        cookies={"session": session}
    )
    all_af_factories = resp_search.json()
    if isinstance(all_af_factories, dict):
        all_af_factories = all_af_factories.get("items", []) or all_af_factories.get("factories", [])
        
    timur_af = [f for f in all_af_factories if "timur" in str(f.get("name", "")).lower()]
    print(f"Total Timur factories in Aftercool (JV): {len(timur_af)}")
    for f in timur_af:
        print(f"  Aftercool ID: {f.get('id')} | Name: '{f.get('name')}' | Items: {f.get('items_count')}")

    # 2. Inspect Timur_for_Otto_Standard_7 and Standard_8
    target_fids = [
        ("504104", "Timur_for_Otto_Standard_7"),
        ("504135", "Timur_for_Otto_Standard_8"),
    ]
    
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
    )
    otto_client = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)

    for fid, fname in target_fids:
        print(f"\n=======================================================", flush=True)
        print(f"Deep analyzing {fname} (ID: {fid})", flush=True)
        resp = await client.send_request(
            "GET",
            "/api/products",
            params={"account": "jv", "dataset": "lister", "factory_id": fid, "include_row": 1, "limit": 0},
            cookies={"session": session}
        )
        items = resp.json().get("items", [])
        print(f"Total items in Aftercool: {len(items)}")
        
        tr_eans = []
        for it in items:
            row = it.get("row", {})
            ean = str(row.get("EAN") or row.get("ean") or row.get("sku") or "").strip()
            all_imgs = f"{row.get('GalleryURL', '')} {row.get('PictureURL', '')} {row.get('pictureurls', '')}"
            if re.search(r"(_TR\b|/TR/|_TR_)", all_imgs, re.I) or any(b in all_imgs.lower() for b in ["evart", "merli", "enza", "nott"]):
                if ean:
                    tr_eans.append(ean)
                    
        print(f"Turkish items detected: {len(tr_eans)}")
        
        # Check in local DB
        async with SessionLocal() as db:
            res_db = await db.execute(
                select(OttoXlsxImportRow.marketplace_status, func.count(OttoXlsxImportRow.id))
                .where(OttoXlsxImportRow.account == "jv", OttoXlsxImportRow.ean.in_(tr_eans))
                .group_by(OttoXlsxImportRow.marketplace_status)
            )
            db_statuses = dict(res_db.all())
            
            res_rows = await db.execute(
                select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean, OttoXlsxImportRow.marketplace_status)
                .where(OttoXlsxImportRow.account == "jv", OttoXlsxImportRow.ean.in_(tr_eans))
            )
            sample_rows = res_rows.all()
            
        print(f"Local DB statuses for {fname} TR items: {db_statuses}")
        
        # Test 2 sample items on OTTO API directly
        if sample_rows:
            print(f"Checking sample items directly on OTTO API:")
            for sku, ean, st in sample_rows[:2]:
                try:
                    otto_res = await otto_client.get_products(GetProductRequest(sku=sku), "jv")
                    variations = otto_res.get("productVariations", [])
                    if variations:
                        v = variations[0]
                        deliv = v.get("delivery", {})
                        moin = v.get("moin")
                        print(f"  OTTO API SKU: {sku} | MOIN: {moin} | deliveryTime on OTTO: {deliv.get('deliveryTime')} | DB status: {st}")
                    else:
                        print(f"  OTTO API SKU: {sku} | NOT FOUND on OTTO | DB status: {st}")
                except Exception as e:
                    print(f"  OTTO API error for {sku}: {e}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
