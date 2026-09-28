"""Deep audit of potential missed Turkish goods for XL.
1. Re-check all factories that failed during Aftercool transient errors.
2. Check all Timur folders for XL in Afterbuy & DB.
3. Cross-check against otto_xlsx_import_rows for XL.
"""

import asyncio
import json
import re
import sys
from collections import defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import func, select
from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.database import SessionLocal, engine
from app.models.factories import Factories
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.schemas.external_schemes.until_schemes import GetProductRequest
from scripts.tr_factories_list import VALID_TR_FACTORIES
from scripts.tr_rules import ALL_TR_KEYWORDS, is_turkish_product


log_file = open("/app/deep_audit_xl.log", "w", encoding="utf-8")

def log_print(*args, **kwargs):
    print(*args, **kwargs, flush=True)
    print(*args, **kwargs, file=log_file, flush=True)

async def audit():
    log_print("=== DEEP AUDIT FOR MISSED TURKISH GOODS (XL) ===\n")

    # 1. Load currently found SKUs to skip
    with open("/app/tr_products_xl.json", "r", encoding="utf-8") as f:
        existing_xl_data = json.load(f)

    existing_skus = set()
    for f_data in existing_xl_data.get("factories", []):
        for it in f_data.get("items", []):
            existing_skus.add(str(it.get("ean") or it.get("sku") or "").strip())

    log_print(f"Loaded {len(existing_skus)} already verified unupdated SKUs in factories_xl.txt.\n")

    # 2. Extract failed factories from the log
    failed_factory_ids = set()
    try:
        with open("/app/logs/otto_bot.log", "r") as f:
            for line in f:
                if "Failed to fetch factory" in line:
                    m = re.search(r"\(ID\s+(\d+)\)", line)
                    if m:
                        failed_factory_ids.add(int(m.group(1)))
    except Exception as e:
        log_print("Log read error:", e)

    log_print(f"Found {len(failed_factory_ids)} factories that failed due to Aftercool network hiccup: {failed_factory_ids}")

    # 3. Connect to Afterbuy
    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=45.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    # Re-fetch the failed factories
    recheck_candidates = []
    async with SessionLocal() as db:
        res = await db.execute(
            select(Factories).where(Factories.factory_id.in_(failed_factory_ids), func.lower(Factories.account) == "xl")
        )
        failed_factories = res.scalars().all()

    log_print(f"Re-fetching {len(failed_factories)} failed factories from Afterbuy...")
    for f in failed_factories:
        for attempt in range(4):
            try:
                resp = await afterbuy.send_request(
                    "GET",
                    "/api/products",
                    params={"account": "xl", "dataset": "lister", "factory_id": f.factory_id, "limit": 0, "include_row": 1},
                    cookies={"session": session},
                )
                items = resp.json().get("items", [])
                for it in items:
                    row = it.get("row", {})
                    ean = str(row.get("EAN") or row.get("sku") or "").strip()
                    if not ean or ean in existing_skus:
                        continue
                    is_tr, reason = is_turkish_product(row)
                    if is_tr:
                        recheck_candidates.append({
                            "ean": ean,
                            "factory_name": f.name,
                            "factory_id": f.factory_id,
                            "reason": reason,
                            "title": str(row.get("Artikelbeschreibung") or "")[:60],
                        })
                break
            except Exception as e:
                await asyncio.sleep(2)

    log_print(f"Found from failed factories: {len(recheck_candidates)} new candidate Turkish items!\n")

    # 4. Check all Timur folders on XL
    async with SessionLocal() as db:
        res = await db.execute(
            select(Factories).where(
                func.lower(Factories.account) == "xl",
                Factories.name.ilike("%timur%"),
                Factories.items_count > 0,
            )
        )
        timur_factories = res.scalars().all()

    log_print(f"Checking {len(timur_factories)} Timur folders on XL:")
    timur_candidates = []
    for f in timur_factories:
        try:
            resp = await afterbuy.send_request(
                "GET",
                "/api/products",
                params={"account": "xl", "dataset": "lister", "factory_id": f.factory_id, "limit": 0, "include_row": 1},
                cookies={"session": session},
            )
            items = resp.json().get("items", [])
            tr_in_folder = 0
            for it in items:
                row = it.get("row", {})
                ean = str(row.get("EAN") or row.get("sku") or "").strip()
                if not ean or ean in existing_skus:
                    continue
                is_tr, reason = is_turkish_product(row)
                if is_tr:
                    tr_in_folder += 1
                    timur_candidates.append({
                        "ean": ean,
                        "factory_name": f.name,
                        "factory_id": f.factory_id,
                        "reason": reason,
                        "title": str(row.get("Artikelbeschreibung") or "")[:60],
                    })
            log_print(f"  * {f.name} (ID: {f.factory_id}): {len(items)} total items, {tr_in_folder} new unupdated TR items")
        except Exception as e:
            log_print(f"  * {f.name} error: {e}")

    # 5. Check DB online items for Turkish brands
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.ean, OttoXlsxImportRow.brand, OttoXlsxImportRow.product_name)
            .where(
                OttoXlsxImportRow.account == "xl",
                OttoXlsxImportRow.marketplace_status == "Online",
            )
        )
        all_online_xl_rows = res.all()

    log_print(f"\nScanning {len(all_online_xl_rows)} Online rows from DB for Turkish brands...")
    db_candidates = []
    for ean, brand, pname in all_online_xl_rows:
        ean_str = str(ean).strip()
        if not ean_str or ean_str in existing_skus:
            continue
        brand_low = (brand or "").lower().strip()
        pname_str = pname or ""
        
        # Check brand match
        is_tr_brand = False
        match_kw = None
        for kw in ALL_TR_KEYWORDS:
            if kw in brand_low or kw in pname_str.lower():
                is_tr_brand = True
                match_kw = kw
                break
                
        if is_tr_brand or re.search(r"(_TR\b|_TR_|\(TR\)|_\(TR\))", pname_str, re.I):
            db_candidates.append({
                "ean": ean_str,
                "factory_name": f"DB_Brand_{match_kw or 'TR'}",
                "factory_id": 0,
                "reason": f"Brand/Title match '{match_kw}' in DB",
                "title": pname_str[:60],
            })

    log_print(f"Found from DB Online rows: {len(db_candidates)} candidate Turkish items!\n")

    # Combine all newly found candidates
    combined_new = {}
    for c in recheck_candidates + timur_candidates + db_candidates:
        if c["ean"] not in existing_skus and c["ean"] not in combined_new:
            combined_new[c["ean"]] = c

    log_print(f"=== TOTAL NEW UNIQUE CANDIDATES TO VERIFY ON OTTO XL: {len(combined_new)} ===")

    # 6. Verify with OTTO XL API
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
    confirmed_missed_items = []
    already_39 = 0
    not_found = 0

    async def verify(it):
        nonlocal already_39, not_found
        sku = it["ean"]
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
                    confirmed_missed_items.append(it)
                    return
                except Exception as e:
                    await asyncio.sleep(1)

    await asyncio.gather(*[verify(it) for it in combined_new.values()])

    log_print("\n=== VERIFICATION RESULTS ===")
    log_print(f"Total checked: {len(combined_new)}")
    log_print(f"Already 39 days: {already_39}")
    log_print(f"Not found on OTTO XL: {not_found}")
    log_print(f"CONFIRMED MISSED LIVE ITEMS: {len(confirmed_missed_items)}\n")

    by_factory = defaultdict(list)
    for it in confirmed_missed_items:
        by_factory[it["factory_name"]].append(it)

    for fn, items in sorted(by_factory.items(), key=lambda x: len(x[1]), reverse=True):
        log_print(f"  * {fn}: {len(items)} items")

    with open("/app/missed_xl_items.json", "w", encoding="utf-8") as f:
        json.dump(confirmed_missed_items, f, ensure_ascii=False, indent=2)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(audit())
