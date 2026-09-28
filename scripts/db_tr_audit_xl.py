"""Audit Turkish products for XL directly from local PostgreSQL DB and verify with OTTO API.
This is 100% independent of Aftercool.
"""

import asyncio
import json
import re
import sys
from collections import Counter, defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal, engine
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.core.otto_auth import OttoAuth
from app.clients.external_otto_client import ExternalOttoClient
from app.schemas.external_schemes.until_schemes import GetProductRequest
from app.core.configs import settings
from scripts.tr_factories_list import VALID_TR_FACTORIES
from scripts.tr_rules import ALL_TR_KEYWORDS, CLEAN_TR_FACTORIES, KNOWN_TR_BRANDS

log_out = open("/app/db_tr_audit_xl.log", "w", encoding="utf-8")

def log_p(*args, **kwargs):
    print(*args, **kwargs, flush=True)
    print(*args, **kwargs, file=log_out, flush=True)

async def main():
    log_p("=== DB-BASED TURKISH PRODUCTS AUDIT FOR XL ===")

    # 1. Load already verified SKUs from factories_xl.txt
    with open("/app/tr_products_xl.json", "r", encoding="utf-8") as f:
        existing_xl_data = json.load(f)

    existing_skus = set()
    for f_data in existing_xl_data.get("factories", []):
        for it in f_data.get("items", []):
            existing_skus.add(str(it.get("ean") or it.get("sku") or "").strip())

    log_p(f"Loaded {len(existing_skus)} already found unupdated SKUs in factories_xl.txt", flush=True)

    # 2. Query all Online rows in DB for account XL
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.ean, OttoXlsxImportRow.product_name, OttoXlsxImportRow.brand)
            .where(
                OttoXlsxImportRow.account == "xl",
                OttoXlsxImportRow.marketplace_status == "Online",
            )
        )
        online_rows = res.all()

    log_p(f"Total Online rows in DB for XL: {len(online_rows)}", flush=True)

    # 3. Detect Turkish products from DB rows
    new_candidates = {}
    
    for ean, pname, brand in online_rows:
        ean_str = str(ean).strip()
        if not ean_str or ean_str in existing_skus:
            continue

        pname_str = pname or ""
        pname_low = pname_str.lower()
        brand_str = brand or ""
        brand_low = brand_str.lower().strip()

        is_tr = False
        reason = ""

        # Title TR markers
        if re.search(r"(_TR\b|_TR_|\(TR\)|_\(TR\))", pname_str, re.I):
            is_tr = True
            reason = "TR tag in product title"
        
        # Brand match
        if not is_tr and brand_low:
            for kw in ALL_TR_KEYWORDS:
                if brand_low == kw or brand_low.startswith(kw + "_") or brand_low.endswith("_" + kw):
                    is_tr = True
                    reason = f"Brand matches TR '{kw}'"
                    break

        # Check if title contains Turkish factory names
        if not is_tr:
            for f_clean in CLEAN_TR_FACTORIES:
                if len(f_clean) >= 4 and f_clean in pname_low:
                    is_tr = True
                    reason = f"Title contains TR factory '{f_clean}'"
                    break

        if is_tr:
            new_candidates[ean_str] = {
                "ean": ean_str,
                "title": pname_str[:60],
                "brand": brand_str,
                "reason": reason,
            }

    log_p(f"Found {len(new_candidates)} new candidate Turkish items from DB Online rows not in factories_xl.txt!", flush=True)

    if not new_candidates:
        log_p("No new candidates found in DB. All Turkish items in DB are already in factories_xl.txt!", flush=True)
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

    sem = asyncio.Semaphore(10)
    confirmed_missed = []
    already_39 = 0
    not_found = 0

    async def verify(sku, it):
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
                        return
                    it["current_delivery_time"] = dt
                    confirmed_missed.append(it)
                    return
                except Exception:
                    await asyncio.sleep(1)

    log_p(f"Verifying {len(new_candidates)} candidates against live OTTO XL API...", flush=True)
    await asyncio.gather(*[verify(sku, it) for sku, it in new_candidates.items()])

    log_p(f"\nVerification Results:", flush=True)
    log_p(f"  * Total candidates: {len(new_candidates)}", flush=True)
    log_p(f"  * Already 39 days: {already_39}", flush=True)
    log_p(f"  * Not found on OTTO: {not_found}", flush=True)
    log_p(f"  * CONFIRMED LIVE UNUPDATED TR ITEMS: {len(confirmed_missed)}", flush=True)

    if confirmed_missed:
        with open("/app/confirmed_missed_db_xl.json", "w", encoding="utf-8") as f:
            json.dump(confirmed_missed, f, ensure_ascii=False, indent=2)
        log_p(f"Saved {len(confirmed_missed)} missed items to /app/confirmed_missed_db_xl.json", flush=True)

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
