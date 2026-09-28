"""Re-check all potential Turkish factories for account XL.
Find any Turkish factories in Afterbuy that are NOT in factories_xl.txt,
fetch their products, and verify live on OTTO XL API.
"""

import asyncio
import json
import re
import sys
from collections import defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal, engine
from app.models.factories import Factories
from app.clients.afterbuy_client import AfterbuyClient
from app.core.otto_auth import OttoAuth
from app.clients.external_otto_client import ExternalOttoClient
from app.schemas.external_schemes.until_schemes import GetProductRequest
from app.core.configs import settings
from scripts.tr_factories_list import VALID_TR_FACTORIES
from scripts.tr_rules import ALL_TR_KEYWORDS, CLEAN_TR_FACTORIES, is_turkish_product

async def main():
    print("=== RE-CHECKING ALL TURKISH FACTORIES ON XL ===", flush=True)

    # 1. Load currently verified 47 factories and SKUs from factories_xl.txt
    with open("/app/tr_products_xl.json", "r", encoding="utf-8") as f:
        existing_xl_data = json.load(f)

    existing_factories = set()
    existing_skus = set()
    for f_data in existing_xl_data.get("factories", []):
        existing_factories.add(f_data.get("factory_name"))
        for it in f_data.get("items", []):
            existing_skus.add(str(it.get("ean") or it.get("sku") or "").strip())

    print(f"Already verified {len(existing_factories)} factories and {len(existing_skus)} live items.", flush=True)

    # 2. Find all factories in XL that might be Turkish
    async with SessionLocal() as db:
        res = await db.execute(
            select(Factories).where(
                func.lower(Factories.account) == "xl",
                Factories.items_count > 0,
            )
        )
        all_xl_factories = res.scalars().all()

    print(f"Total non-empty factories for XL in DB: {len(all_xl_factories)}", flush=True)

    factories_to_audit = []
    for f in all_xl_factories:
        fname = f.name.strip()
        fname_low = fname.lower()
        
        # Check if TR tag in factory name
        is_tr = bool(re.search(r"(_tr\b|_tr_|\(tr\)|\btr\b)", fname_low))
        
        # Check if matches known TR factory list
        if not is_tr and fname in VALID_TR_FACTORIES:
            is_tr = True

        # Check if Timur folder
        if not is_tr and "timur" in fname_low:
            is_tr = True

        # Check clean brand names
        if not is_tr:
            for kw in ALL_TR_KEYWORDS:
                if kw in fname_low:
                    is_tr = True
                    break

        if is_tr:
            factories_to_audit.append(f)

    print(f"Total potential Turkish factories for XL: {len(factories_to_audit)}", flush=True)
    not_in_report = [f for f in factories_to_audit if f.name not in existing_factories]
    print(f"Potential Turkish factories NOT in current factories_xl.txt: {len(not_in_report)}", flush=True)
    for f in not_in_report:
        print(f"  - {f.name} (items_count: {f.items_count}, ID: {f.factory_id})", flush=True)

    if not not_in_report:
        print("\nAll potential Turkish factories are ALREADY in factories_xl.txt!", flush=True)
        await engine.dispose()
        return

    # 3. Connect to Afterbuy and fetch items for these factories
    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    candidates = []
    sem_afterbuy = asyncio.Semaphore(5)

    async def fetch_factory(f):
        async with sem_afterbuy:
            for attempt in range(3):
                try:
                    resp = await afterbuy.send_request(
                        "GET",
                        "/api/products",
                        params={"account": "xl", "dataset": "lister", "factory_id": f.factory_id, "limit": 0, "include_row": 1},
                        cookies={"session": session},
                    )
                    items = resp.json().get("items", [])
                    found_in_f = 0
                    for it in items:
                        row = it.get("row", {})
                        ean = str(row.get("EAN") or row.get("sku") or "").strip()
                        if not ean or ean in existing_skus:
                            continue
                        is_tr, reason = is_turkish_product(row)
                        if is_tr:
                            found_in_f += 1
                            candidates.append({
                                "ean": ean,
                                "sku": ean,
                                "factory_name": f.name,
                                "factory_id": f.factory_id,
                                "title": str(row.get("Artikelbeschreibung") or "")[:60],
                                "reason": reason,
                            })
                    print(f"Fetched {f.name}: {len(items)} items, found {found_in_f} new TR candidates", flush=True)
                    return
                except Exception as e:
                    await asyncio.sleep(2)
            print(f"Failed {f.name} after 3 attempts", flush=True)

    await asyncio.gather(*[fetch_factory(f) for f in not_in_report])

    print(f"\nTotal new candidate Turkish items from these factories: {len(candidates)}", flush=True)

    if not candidates:
        print("No new Turkish products found in these factories.", flush=True)
        await engine.dispose()
        return

    # 4. Verify candidate items against live OTTO XL API
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

    sem_otto = asyncio.Semaphore(10)
    live_unupdated = []
    already_39 = 0
    not_found = 0

    async def verify_otto(it):
        nonlocal already_39, not_found
        sku = it["ean"]
        async with sem_otto:
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
                        return
                    it["current_delivery_time"] = dt
                    live_unupdated.append(it)
                    return
                except Exception:
                    await asyncio.sleep(1)

    print(f"Verifying {len(candidates)} candidates on live OTTO XL API...", flush=True)
    await asyncio.gather(*[verify_otto(it) for it in candidates])

    print(f"\n=== VERIFICATION RESULTS FOR ADDITIONAL FACTORIES ===", flush=True)
    print(f"  * Total candidates checked: {len(candidates)}", flush=True)
    print(f"  * Already 39 days: {already_39}", flush=True)
    print(f"  * Not found on OTTO XL (drafts): {not_found}", flush=True)
    print(f"  * NEW LIVE ITEMS NEEDING UPDATE: {len(live_unupdated)}", flush=True)

    by_fact = defaultdict(list)
    for it in live_unupdated:
        by_fact[it["factory_name"]].append(it)

    for fn, it_list in sorted(by_fact.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"    - {fn}: {len(it_list)} items", flush=True)

    # Save extra items
    with open("/app/extra_xl_tr_items.json", "w", encoding="utf-8") as f:
        json.dump(live_unupdated, f, ensure_ascii=False, indent=2)

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
