import asyncio
import json
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.schemas.external_schemes.until_schemes import GetProductRequest
from scripts.tr_rules import is_turkish_product

TEST_FACTORIES = [
    ("Timur_for_Otto_Standard_8", 483403),
    ("Timur_for_Otto_Standard_9", 483406),
]

async def check_timur():
    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
    )
    otto = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)
    
    sem = asyncio.Semaphore(15)
    
    for fname, fid in TEST_FACTORIES:
        resp = await afterbuy.send_request(
            "GET",
            "/api/products",
            params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        print(f"\n--- Checking {fname} (ID {fid}): {len(items)} items in Afterbuy ---", flush=True)
        
        tr_items = []
        for it in items:
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if not sku:
                continue
            is_tr, reason = is_turkish_product(row)
            if is_tr:
                tr_items.append((sku, str(row.get("Artikelbeschreibung") or "")[:50], reason))
                
        print(f"  Turkish candidates by rule: {len(tr_items)} / {len(items)}", flush=True)
        
        live_online = []
        already_39 = 0
        not_found = 0
        
        async def check_otto(sku, title, reason):
            nonlocal already_39, not_found
            async with sem:
                for attempt in range(3):
                    try:
                        res = await otto.get_products(GetProductRequest(sku=sku), "xl")
                        vars_ = res.get("productVariations") or []
                        if not vars_:
                            not_found += 1
                            return
                        dt = (vars_[0].get("delivery") or {}).get("deliveryTime")
                        if dt == 39:
                            already_39 += 1
                        else:
                            live_online.append({"sku": sku, "title": title, "dt": dt, "reason": reason})
                        return
                    except Exception:
                        await asyncio.sleep(1)
                        
        await asyncio.gather(*[check_otto(sku, t, r) for sku, t, r in tr_items])
        
        print(f"  Live OTTO XL Results for {fname}:")
        print(f"    - Already 39 days: {already_39}")
        print(f"    - Not found on OTTO XL (drafts): {not_found}")
        print(f"    - LIVE UNUPDATED (dt != 39): {len(live_online)}")
        for x in live_online[:5]:
            print(f"       SKU: {x['sku']} | DT: {x['dt']} | Title: {x['title']}")

if __name__ == "__main__":
    asyncio.run(check_timur())
