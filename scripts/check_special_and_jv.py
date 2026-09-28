import asyncio
import json
import logging
import os
import re
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.schemas.external_schemes.until_schemes import GetProductRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("check_special")

SPECIAL_FOLDERS_FROM_JV = [
    "NEW_NOTT_PRICE_CHANGE",
    "NEW_Person_4_Xenia",
    "NEW_SERHATHOME_SPECIAL_AINUR",
    "NEW_FAVORY_SPECIAL",
    "NEW_Radmila",
    "NEW_DEPOTUM",
    "NARISTA_(TR)",
    "MillHouse_(TR) (Delete don't touch)",
]

async def main():
    with open("/app/all_afterbuy_xl_factories.json") as f:
        all_f = json.load(f)

    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

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

    sem = asyncio.Semaphore(15)

    print("=== CHECKING SPECIAL JV FOLDERS ON OTTO XL ===")
    for folder_name in SPECIAL_FOLDERS_FROM_JV:
        matched = [f for f in all_f if folder_name.lower() in f["name"].lower()]
        if not matched:
            print(f"Folder '{folder_name}' not found in Afterbuy")
            continue
        fact = matched[0]
        fid = fact["id"]
        fname = fact["name"]
        resp = await afterbuy.send_request(
            "GET",
            "/api/products",
            params={
                "account": "xl",
                "dataset": "lister",
                "factory_id": fid,
                "limit": 150,
                "offset": 0,
                "include_row": 1,
            },
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        if not items:
            print(f"[{fname}] (ID {fid}): 0 items in Afterbuy")
            continue

        xl_online_unupdated = []
        xl_already_39 = []
        xl_not_found = 0

        async def check_item(it):
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if not sku:
                return
            async with sem:
                try:
                    res = await otto.get_products(GetProductRequest(sku=sku), "xl")
                    vars_ = res.get("productVariations") or []
                    if vars_:
                        dt = (vars_[0].get("delivery") or {}).get("deliveryTime")
                        if dt == 39:
                            xl_already_39.append(sku)
                        else:
                            xl_online_unupdated.append((sku, dt))
                    else:
                        nonlocal xl_not_found
                        xl_not_found += 1
                except Exception:
                    pass

        await asyncio.gather(*[check_item(it) for it in items])

        print(f"[{fname}] (Total Afterbuy: {len(items)}):")
        print(f"   -> XL Online unupdated (dt != 39): {len(xl_online_unupdated)}")
        print(f"   -> XL Already 39: {len(xl_already_39)}")
        print(f"   -> XL Not found: {xl_not_found}")
        if xl_online_unupdated:
            print(f"   -> Sample unupdated on XL: {xl_online_unupdated[:3]}")

if __name__ == "__main__":
    asyncio.run(main())
