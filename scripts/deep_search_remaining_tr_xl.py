"""Deep search for any remaining Turkish products on account XL.
Method 1: Scan database table `otto_xlsx_import_rows` (all 90k+ online rows) for TR origin markers.
Method 2: Scan remaining Afterbuy factories that were not in the first batches.
Method 3: Cross-check live OTTO XL API to find items with deliveryTime != 39.
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, text
from app.database import SessionLocal, engine
from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.schemas.external_schemes.until_schemes import GetProductRequest
from scripts.tr_rules import is_turkish_product, ALL_TR_KEYWORDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/deep_search_xl.log", mode="w", encoding="utf-8"),
        logging.FileHandler("/app/logs/otto_bot.log", mode="a", encoding="utf-8"),
    ],
)
log = logging.getLogger("deep_search_xl")

def build_otto_client():
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
    )
    return ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)

async def main():
    log.info("================================================================")
    log.info("  STARTING DEEP SEARCH FOR ANY REMAINING TURKISH PRODUCTS (XL)")
    log.info("================================================================")

    # 1. Collect all already updated SKUs
    updated_skus = set()
    for path in ["/app/tr_products_xl.json", "/app/tr_products_xl_missed_batch.json"]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    for fact in d.get("factories", []):
                        for it in fact.get("items", []):
                            s = str(it.get("ean") or it.get("sku") or "").strip()
                            if s:
                                updated_skus.add(s)
            except Exception as e:
                log.warning("Could not load %s: %s", path, e)
    log.info("Total already updated/processed SKUs on XL: %d", len(updated_skus))

    candidates = {}

    # 2. METHOD 1: Scan DB table otto_xlsx_import_rows for XL
    log.info("\n--- METHOD 1: Scanning DB table otto_xlsx_import_rows ---")
    try:
        async with SessionLocal() as db:
            # Query online rows with TR keywords or city codes or country TR
            query = text("""
                SELECT sku, ean, brand, title, description, short_description
                FROM otto_xlsx_import_rows
                WHERE status = 'Online'
                  AND (
                      description ~* '\\mcountry\\s+TR\\M'
                      OR short_description ~* '\\mcountry\\s+TR\\M'
                      OR description ~* '\\mcity\\s+(INE|BUR|KAY|IST|ANK|IZM|ADA|GAZ)\\M'
                      OR short_description ~* '\\mcity\\s+(INE|BUR|KAY|IST|ANK|IZM|ADA|GAZ)\\M'
                      OR description ~* '(made in turkey|hergestellt in (der )?türkei)'
                      OR short_description ~* '(made in turkey|hergestellt in (der )?türkei)'
                      OR title ~* '(made in turkey|hergestellt in (der )?türkei)'
                  )
            """)
            res = await db.execute(query)
            rows = res.fetchall()
            log.info("DB query returned %d potential TR rows with origin tags.", len(rows))
            for r in rows:
                sku = str(r[0] or r[1] or "").strip()
                if not sku or sku in updated_skus:
                    continue
                candidates[sku] = {
                    "sku": sku,
                    "ean": sku,
                    "title": str(r[3] or "")[:60],
                    "brand": str(r[2] or ""),
                    "reason": "DB row has explicit TR origin marker",
                    "source": "DB_otto_xlsx_import_rows",
                }
    except Exception as exc:
        log.warning("DB query failed: %s", exc)

    log.info("Candidates from DB after excluding updated: %d", len(candidates))

    # 3. METHOD 2: Scan remaining Afterbuy factories that were not previously scanned
    log.info("\n--- METHOD 2: Scanning remaining Afterbuy factories ---")
    if os.path.exists("/app/all_afterbuy_xl_factories.json"):
        with open("/app/all_afterbuy_xl_factories.json", "r", encoding="utf-8") as f:
            all_factories = json.load(f)

        # Get set of already scanned factory IDs
        already_scanned_ids = set()
        for path in ["/app/tr_products_xl.json", "/app/tr_products_xl_missed_batch.json"]:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    for fact in d.get("factories", []):
                        fid = fact.get("factory_id")
                        if fid:
                            already_scanned_ids.add(str(fid))

        remaining = [f for f in all_factories if str(f.get("id")) not in already_scanned_ids and f.get("items_count", 0) > 0]
        # Skip huge archive folder
        remaining = [f for f in remaining if "others_löschen" not in f.get("name", "").lower()]

        # Filter factories that match any known TR keyword in their name
        tr_keyword_factories = []
        for f in remaining:
            low = f.get("name", "").lower()
            for kw in ALL_TR_KEYWORDS:
                if len(kw) >= 4 and kw in low:
                    tr_keyword_factories.append((f, kw))
                    break

        log.info("Found %d remaining factories matching Turkish brand/factory keywords.", len(tr_keyword_factories))

        if tr_keyword_factories:
            afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
            session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

            for fact, kw in tr_keyword_factories:
                fid = fact.get("id")
                fname = fact.get("name")
                try:
                    resp = await afterbuy.send_request(
                        "GET",
                        "/api/products",
                        params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
                        cookies={"session": session},
                    )
                    items = resp.json().get("items", [])
                    matched_in_f = 0
                    for it in items:
                        row = it.get("row", {})
                        sku = str(row.get("EAN") or row.get("sku") or "").strip()
                        if not sku or sku in updated_skus or sku in candidates:
                            continue
                        is_tr, reason = is_turkish_product(row)
                        if is_tr:
                            matched_in_f += 1
                            candidates[sku] = {
                                "sku": sku,
                                "ean": sku,
                                "title": str(row.get("Artikelbeschreibung") or "")[:60],
                                "reason": f"Factory '{fname}' (kw: {kw}) - {reason}",
                                "source": fname,
                            }
                    if matched_in_f > 0:
                        log.info("  * Factory '%s' (ID %s): found %d TR candidate items", fname, fid, matched_in_f)
                except Exception as exc:
                    log.warning("Failed to fetch %s: %s", fname, exc)

    log.info("\nTotal candidates gathered from all methods: %d", len(candidates))

    # 4. METHOD 3: Verify candidate items on live OTTO XL API
    if not candidates:
        log.info("No new Turkish candidate products found. Search complete.")
        return

    log.info("\n--- METHOD 3: Verifying %d candidates on live OTTO XL API ---", len(candidates))
    otto = build_otto_client()
    sem = asyncio.Semaphore(15)

    confirmed_live_unupdated = []
    already_39 = 0
    not_found = 0

    async def verify_candidate(cand):
        nonlocal already_39, not_found
        sku = cand["sku"]
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
                        cand["current_delivery_time"] = dt
                        confirmed_live_unupdated.append(cand)
                    return
                except Exception:
                    await asyncio.sleep(1)

    await asyncio.gather(*[verify_candidate(c) for c in candidates.values()])

    log.info("================================================================")
    log.info("  DEEP SEARCH RESULTS:")
    log.info("  Total Candidates Checked: %d", len(candidates))
    log.info("  Already 39 Days: %d", already_39)
    log.info("  Not Online / Drafts: %d", not_found)
    log.info("  CONFIRMED LIVE UNUPDATED TURKISH GOODS: %d", len(confirmed_live_unupdated))
    log.info("================================================================")

    by_src = defaultdict(list)
    for it in confirmed_live_unupdated:
        by_src[it.get("source", "Unknown")].append(it)

    for src, it_list in sorted(by_src.items(), key=lambda x: len(x[1]), reverse=True):
        log.info("  * %s: %d items", src, len(it_list))

    # Save to file
    out_file = "/app/remaining_tr_xl_found.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(confirmed_live_unupdated, f, ensure_ascii=False, indent=2)
    log.info("Saved confirmed items to %s", out_file)

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
