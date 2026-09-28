"""Compare factories.txt (JV) with XL account.
1. Check each factory from JV factories.txt on account XL.
2. Fetch items from Afterbuy for XL.
3. Check them on OTTO XL API.
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
from scripts.tr_rules import is_turkish_product

# Extract pure factory names from JV factories.txt
JV_FACTORIES = [
    "Timur_for_Ottor_Standard_3",
    "Timur_for_Otto_Standard",
    "Timur_for_Ottor_Standard_4",
    "Timur_for_Otto_Standard_II",
    "Timur_for_Otto_Standard_6",
    "Timur_for_Otto_Standard_9",
    "Timur_for_Otto_Standard_7",
    "Timur_for_Otto_Standard_8",
    "Timur_for_Otto_Standard_5",
    "NEW_NOTT_PRICE_CHANGE",
    "Duga_Home_Original(TR)",
    "NEW_SEYRAN_GIZETTA_2",
    "Sandalie_(TR)",
    "AhmKag_(TR)",
    "NEW_Person_4_Xenia",
    "NEW_SERHATHOME_SPECIAL_AINUR",
    "NEW_FAVORY_SPECIAL",
    "Brezza_(TR)",
    "NEW_SEYRAN_GIZETTA",
    "NEW_Radmila",
    "YRL_(TR)",
    "Montro_(TR)",
    "Twins_(TR)",
    "NEW_SEYRAN_GIZETTA_3",
    "Narista_(TR)",
    "NEW_DEPOTUM",
    "DELETE_DONT_TOUCH Bellavistacollection_(IT)",
    "EVEREST_(TR)",
    "Sitis-Office_(TR)",
    "Bürosit_(TR)",
    "New_Home_Design_(TR)",
    "T&H_(TR)",
    "SOFFI_(TR)",
    "Somm_Mobilya_(TR)",
    "Others_Löschen",
    "MillHouse_(TR)",
    "Hollywood_(TR)",
    "Roll_Furniture_(TR)",
    "Saka_(TR)",
    "Acel_(TR)",
    "Elegance_Furniture_(TR)",
    "SETT_(TR)",
    "Kromev_(TR)",
]

async def main():
    print("=== CROSS-ANALYSIS: JV FACTORIES vs XL ACCOUNT ===\n", flush=True)

    # 1. Load already updated SKUs from factories_xl.txt / tr_products_xl.json
    with open("/app/tr_products_xl.json", "r", encoding="utf-8") as f:
        xl_data = json.load(f)

    updated_skus = set()
    for f in xl_data.get("factories", []):
        for it in f.get("items", []):
            sku = str(it.get("ean") or it.get("sku") or "").strip()
            if sku:
                updated_skus.add(sku)

    print(f"Already updated on XL: {len(updated_skus)} SKUs in {len(xl_data.get('factories', []))} factories.", flush=True)

    # 2. Check each JV factory in DB for XL
    async with SessionLocal() as db:
        res = await db.execute(select(Factories).where(func.lower(Factories.account) == "xl"))
        all_xl_db = res.scalars().all()

    xl_db_by_name = {f.name.lower().strip(): f for f in all_xl_db}

    matched_factories = []
    missing_in_xl_db = []

    for jv_f in JV_FACTORIES:
        clean = jv_f.lower().strip()
        matched = None
        for k, v in xl_db_by_name.items():
            if k == clean or k.replace(" ", "_") == clean.replace(" ", "_"):
                matched = v
                break
        if matched:
            matched_factories.append((jv_f, matched))
        else:
            # Try fuzzy match
            clean_tag = re.sub(r"_\(tr\)", "", clean).strip()
            fuzzy = None
            for k, v in xl_db_by_name.items():
                if clean_tag in k:
                    fuzzy = v
                    break
            if fuzzy:
                matched_factories.append((jv_f, fuzzy))
            else:
                missing_in_xl_db.append(jv_f)

    print(f"Matched {len(matched_factories)} JV factories in XL DB.")
    print(f"Not found in XL DB ({len(missing_in_xl_db)}): {missing_in_xl_db}\n")

    # 3. Connect to Afterbuy and fetch items from these factories that were not updated
    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    candidates = []
    print("Checking products in matched factories for XL...")
    for jv_name, xl_f in matched_factories:
        try:
            resp = await afterbuy.send_request(
                "GET",
                "/api/products",
                params={"account": "xl", "dataset": "lister", "factory_id": xl_f.factory_id, "limit": 0, "include_row": 1},
                cookies={"session": session},
            )
            items = resp.json().get("items", [])
            unupdated_in_f = 0
            for it in items:
                row = it.get("row", {})
                sku = str(row.get("EAN") or row.get("sku") or "").strip()
                if not sku or sku in updated_skus:
                    continue
                is_tr, reason = is_turkish_product(row)
                if is_tr:
                    unupdated_in_f += 1
                    candidates.append({
                        "sku": sku,
                        "ean": sku,
                        "factory_name": xl_f.name,
                        "factory_id": xl_f.factory_id,
                        "title": str(row.get("Artikelbeschreibung") or "")[:60],
                        "reason": reason,
                    })
            if unupdated_in_f > 0:
                print(f"  * {xl_f.name} (ID {xl_f.factory_id}): {len(items)} total items, {unupdated_in_f} UNUPDATED TR candidates", flush=True)
        except Exception as e:
            print(f"  * {xl_f.name} error: {e}", flush=True)

    print(f"\nTotal new candidate Turkish items from matched factories: {len(candidates)}", flush=True)

    # 4. Also check: are there other Timur folders on XL?
    async with SessionLocal() as db:
        res = await db.execute(
            select(Factories).where(
                func.lower(Factories.account) == "xl",
                Factories.name.ilike("%timur%"),
                Factories.items_count > 0,
            )
        )
        all_timur_xl = res.scalars().all()

    print(f"\nAll Timur folders on XL ({len(all_timur_xl)}):")
    for f in all_timur_xl:
        print(f"  - {f.name}: {f.items_count} items (ID: {f.factory_id})")

    # 5. Verify candidates on live OTTO XL API
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

    sem = asyncio.Semaphore(10)
    confirmed_live_unupdated = []
    already_39 = 0
    not_found = 0

    async def verify(it):
        nonlocal already_39, not_found
        sku = it["sku"]
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
                        return
                    it["current_delivery_time"] = dt
                    confirmed_live_unupdated.append(it)
                    return
                except Exception:
                    await asyncio.sleep(1)

    if candidates:
        print(f"\nVerifying {len(candidates)} candidates on live OTTO XL API...", flush=True)
        await asyncio.gather(*[verify(it) for it in candidates])

    print("\n=== LIVE OTTO XL VERIFICATION SUMMARY ===", flush=True)
    print(f"Total checked: {len(candidates)}", flush=True)
    print(f"Already 39 days: {already_39}", flush=True)
    print(f"Not found on OTTO XL (drafts): {not_found}", flush=True)
    print(f"CONFIRMED UNUPDATED LIVE ITEMS: {len(confirmed_live_unupdated)}", flush=True)

    by_f = defaultdict(list)
    for it in confirmed_live_unupdated:
        by_f[it["factory_name"]].append(it)

    print("\nBreakdown of MISSED unupdated items by factory:", flush=True)
    for fn, items in sorted(by_f.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"  * {fn}: {len(items)} items", flush=True)

    with open("/app/tr_missed_vs_jv_xl.json", "w", encoding="utf-8") as f:
        json.dump(confirmed_live_unupdated, f, ensure_ascii=False, indent=2)

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
