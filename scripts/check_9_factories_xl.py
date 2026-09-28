"""Fetch products from the 9 factories on XL and verify live on OTTO XL."""

import asyncio
import json
import re
import sys
from collections import defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.otto_auth import OttoAuth
from app.clients.external_otto_client import ExternalOttoClient
from app.schemas.external_schemes.until_schemes import GetProductRequest
from app.core.configs import settings
from scripts.tr_rules import is_turkish_product

FACTORIES_TO_CHECK = [
    ("Montro_(TR)_(MTR)", 482588),
    ("Twins_(TR)", 481988),
    ("NARISTA_(TR)", 483306),
    ("Bürosit_(TR)", 482511),
    ("New_Home_Design_(TR)", 483339),
    ("T&H_(TR)", 481975),
    ("Others_Löschen", 482403),
    ("MillHouse_(TR) (Delete don't touch)", 482552),
    ("Kromev_(TR) (Delete_Don't_Touch)", 482427),
]

async def check():
    print("=== CHECKING 9 XL FACTORIES ON OTTO XL API ===\n", flush=True)

    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    candidates = []
    for fname, fid in FACTORIES_TO_CHECK:
        try:
            resp = await afterbuy.send_request(
                "GET",
                "/api/products",
                params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
                cookies={"session": session},
            )
            items = resp.json().get("items", [])
            tr_items = 0
            for it in items:
                row = it.get("row", {})
                sku = str(row.get("EAN") or row.get("sku") or "").strip()
                if not sku:
                    continue
                is_tr, reason = is_turkish_product(row)
                if is_tr:
                    tr_items += 1
                    candidates.append({
                        "sku": sku,
                        "ean": sku,
                        "factory_name": fname,
                        "factory_id": fid,
                        "title": str(row.get("Artikelbeschreibung") or "")[:60],
                        "reason": reason,
                    })
            print(f"  * {fname} (ID {fid}): {len(items)} items in Afterbuy, {tr_items} Turkish candidates", flush=True)
        except Exception as e:
            print(f"  * {fname} error: {e}", flush=True)

    print(f"\nTotal candidate Turkish goods across these 9 factories: {len(candidates)}")

    if not candidates:
        return

    # Verify on live OTTO XL API
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
    confirmed_live = []
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
                    confirmed_live.append(it)
                    return
                except Exception:
                    await asyncio.sleep(1)

    print(f"Verifying {len(candidates)} candidates on live OTTO XL API...", flush=True)
    await asyncio.gather(*[verify(it) for it in candidates])

    print("\n=== VERIFICATION RESULTS ===")
    print(f"Total checked: {len(candidates)}")
    print(f"Already 39 days: {already_39}")
    print(f"Not found on OTTO XL (drafts): {not_found}")
    print(f"REAL LIVE UNUPDATED TR GOODS ON OTTO XL: {len(confirmed_live)}")

    by_f = defaultdict(list)
    for it in confirmed_live:
        by_f[it["factory_name"]].append(it)

    print("\nBreakdown of newly found live goods:")
    for fn, it_list in sorted(by_f.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"  * {fn}: {len(it_list)} items")

    with open("/app/extra_9_factories_xl.json", "w", encoding="utf-8") as f:
        json.dump(confirmed_live, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(check())
