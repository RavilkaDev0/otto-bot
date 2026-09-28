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

async def main():
    # 1. Load factories_xl.txt
    existing_f = set()
    with open("/app/factories_xl.txt") as f:
        for line in f:
            line = line.strip()
            if " — " in line and line[0].isdigit():
                parts = line.split(". ", 1)[1].split(" — ")
                existing_f.add(parts[0].strip().lower())

    with open("/app/all_afterbuy_xl_factories.json") as f:
        all_f = json.load(f)

    # Find candidate TR factories that have items > 0 and NOT in factories_xl.txt
    tr_not_in_xl = []
    for f in all_f:
        name = f.get("name", "").strip()
        cnt = f.get("items_count", 0)
        if cnt == 0:
            continue
        low = name.lower()
        if "others_löschen" in low or "others_loschen" in low:
            continue
        if "_(pl)" in low or "_(it)" in low or "_(cn)" in low or "_(de)" in low:
            continue
        # check (tr)
        if "(tr)" in low or "_tr" in low or "timur" in low or "ropez" in low:
            if name.lower() not in existing_f:
                tr_not_in_xl.append(f)

    print(f"Total TR factories with items > 0 not in factories_xl.txt: {len(tr_not_in_xl)}")
    for f in tr_not_in_xl:
        print(f" - {f['name']} (ID: {f['id']}, items: {f['items_count']})")

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

    # Let's inspect each of these factories!
    print("\n--- DETAILED AUDIT OF EACH NOT-INCLUDED TR FACTORY ---")
    for fact in tr_not_in_xl:
        fid = fact["id"]
        fname = fact["name"]
        resp = await afterbuy.send_request(
            "GET",
            "/api/products",
            params={
                "account": "xl",
                "dataset": "lister",
                "factory_id": fid,
                "limit": 50,
                "offset": 0,
                "include_row": 1,
            },
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        not_found = 0
        found_dt_39 = 0
        found_other_dt = 0
        other_dt_list = []

        for it in items:
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if not sku:
                continue
            try:
                res = await otto.get_products(GetProductRequest(sku=sku), "xl")
                vars_ = res.get("productVariations") or []
                if not vars_:
                    not_found += 1
                else:
                    dt = (vars_[0].get("delivery") or {}).get("deliveryTime")
                    if dt == 39:
                        found_dt_39 += 1
                    else:
                        found_other_dt += 1
                        other_dt_list.append((sku, dt))
            except Exception:
                pass

        print(f"[{fname}] (Total Afterbuy: {fact['items_count']}, Sample: {len(items)}):")
        print(f"    Not on OTTO XL: {not_found}")
        print(f"    Already deliveryTime 39: {found_dt_39}")
        print(f"    DeliveryTime != 39 (NEEDS UPDATE): {found_other_dt}")
        if other_dt_list:
            print(f"    Sample unupdated: {other_dt_list[:3]}")

if __name__ == "__main__":
    asyncio.run(main())
