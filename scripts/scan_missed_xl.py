"""Full reliable scanner of all Turkish factories directly from Afterbuy for XL account.
Features:
- Reads all factories directly from Afterbuy
- Safe pagination (limit 250) to prevent Aftercool 502/timeouts
- Incremental saving after each factory
- Dual logging to console and /app/scan_missed_xl.log
- Verifies live OTTO XL status
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

from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.schemas.external_schemes.until_schemes import GetProductRequest
from scripts.tr_rules import is_turkish_product

# Setup logging
log_file = "/app/scan_missed_xl.log"
handlers = [
    logging.StreamHandler(sys.stdout),
    logging.FileHandler(log_file, mode="w", encoding="utf-8"),
]
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=handlers,
)
log = logging.getLogger("scan_missed_xl")

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

async def fetch_factory_products(afterbuy: AfterbuyClient, session: str, factory_id: int) -> list[dict]:
    """Fetch all products for a factory with pagination to avoid Aftercool timeouts."""
    all_items = []
    offset = 0
    limit = 200

    while True:
        success = False
        for attempt in range(4):
            try:
                resp = await afterbuy.send_request(
                    "GET",
                    "/api/products",
                    params={
                        "account": "xl",
                        "dataset": "lister",
                        "factory_id": factory_id,
                        "offset": offset,
                        "limit": limit,
                        "include_row": 1,
                    },
                    cookies={"session": session},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])
                    all_items.extend(items)
                    total = data.get("total", len(all_items))
                    if len(items) < limit or len(all_items) >= total:
                        return all_items
                    offset += limit
                    success = True
                    break
                elif resp.status_code == 502:
                    await asyncio.sleep(2 * (attempt + 1))
            except Exception as e:
                await asyncio.sleep(2 * (attempt + 1))

        if not success:
            log.warning("Could not fetch page at offset %d for factory %s", offset, factory_id)
            break

    return all_items

async def main():
    log.info("================================================================")
    log.info("  STARTING FULL SCAN OF MISSED TURKISH PRODUCTS (ACCOUNT: XL)")
    log.info("================================================================")

    # 1. Load already updated SKUs from tr_products_xl.json
    already_updated_skus = set()
    if os.path.exists("/app/tr_products_xl.json"):
        try:
            with open("/app/tr_products_xl.json", "r", encoding="utf-8") as f:
                d = json.load(f)
                for fact in d.get("factories", []):
                    for it in fact.get("items", []):
                        s = str(it.get("ean") or it.get("sku") or "").strip()
                        if s:
                            already_updated_skus.add(s)
        except Exception as e:
            log.warning("Could not read tr_products_xl.json: %s", e)
    log.info("Already updated on XL: %d SKUs (will skip these)", len(already_updated_skus))

    # 2. Load all Afterbuy factories
    if not os.path.exists("/app/all_afterbuy_xl_factories.json"):
        log.error("File /app/all_afterbuy_xl_factories.json not found!")
        return
    with open("/app/all_afterbuy_xl_factories.json", "r", encoding="utf-8") as f:
        all_factories = json.load(f)

    # 3. Load JV factory names for cross-matching
    jv_names = set()
    if os.path.exists("/app/factories.txt"):
        with open("/app/factories.txt", "r", encoding="utf-8") as f:
            for l in f:
                m = re.match(r"^\d+\.\s+([^\s—]+(?:\s+[^\s—]+)*)\s+—", l)
                if m:
                    clean = re.sub(r"\s+\([^)]+\)", "", m.group(1)).strip().lower()
                    jv_names.add(clean)

    # Filter target factories
    target_factories = []
    for f in all_factories:
        name = f.get("name", "").strip()
        low = name.lower()
        cnt = f.get("items_count", 0)
        if cnt == 0:
            continue
        if "others_löschen" in low or "others_loschen" in low:
            continue

        is_tr = (
            "(tr)" in low
            or "_tr" in low
            or low.endswith("_tr")
            or "timur" in low
            or any(j in low or low in j for j in jv_names)
        )
        if is_tr:
            target_factories.append(f)

    log.info("Identified %d candidate Turkish factories to scan in Afterbuy.", len(target_factories))

    # 4. Connect to Afterbuy
    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = None
    for attempt in range(5):
        try:
            session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
            log.info("Connected to Afterbuy successfully.")
            break
        except Exception as e:
            log.warning("Afterbuy login attempt %d failed: %s", attempt + 1, e)
            await asyncio.sleep(3)

    if not session:
        log.error("Failed to authenticate with Afterbuy.")
        return

    otto = build_otto_client()
    sem_otto = asyncio.Semaphore(15)

    all_missed_live = defaultdict(list)
    total_tr_candidates = 0
    total_live_missed = 0
    start_time = time.time()

    out_json = "/app/tr_products_xl_missed_batch.json"
    out_txt = "/app/factories_xl_missed.txt"

    for idx, fact in enumerate(target_factories, 1):
        fname = fact.get("name")
        fid = fact.get("id")
        cnt = fact.get("items_count", 0)

        t0 = time.time()
        items = await fetch_factory_products(afterbuy, session, fid)
        t_fetch = time.time() - t0

        # Filter TR products
        tr_candidates = []
        for it in items:
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if not sku or sku in already_updated_skus:
                continue
            is_tr_prod, reason = is_turkish_product(row)
            if is_tr_prod:
                tr_candidates.append({
                    "sku": sku,
                    "ean": sku,
                    "title": str(row.get("Artikelbeschreibung") or row.get("Artikelname") or "")[:60],
                    "reason": reason,
                    "factory_name": fname,
                    "factory_id": fid,
                })

        if not tr_candidates:
            if idx % 10 == 0 or idx == len(target_factories):
                log.info("[%d/%d] '%s': %d items (0 new TR candidates) (%.1fs)", idx, len(target_factories), fname, len(items), t_fetch)
            continue

        total_tr_candidates += len(tr_candidates)

        # Check live OTTO XL API
        factory_live = []

        async def verify_sku(cand):
            sku = cand["sku"]
            async with sem_otto:
                for attempt in range(3):
                    try:
                        res = await otto.get_products(GetProductRequest(sku=sku), "xl")
                        vars_ = res.get("productVariations") or []
                        if not vars_:
                            return  # Draft / Not online
                        dt = (vars_[0].get("delivery") or {}).get("deliveryTime")
                        if dt != 39:
                            cand["current_delivery_time"] = dt
                            factory_live.append(cand)
                        return
                    except Exception:
                        await asyncio.sleep(1)

        await asyncio.gather(*[verify_sku(c) for c in tr_candidates])

        if factory_live:
            all_missed_live[fname].extend(factory_live)
            total_live_missed += len(factory_live)
            log.info(
                "[%d/%d] >>> '%s' (ID %s): %d/%d candidates are LIVE ON OTTO XL! (Total missed live so far: %d)",
                idx, len(target_factories), fname, fid, len(factory_live), len(tr_candidates), total_live_missed
            )

            # Incremental save
            summary_data = {
                "account": "xl",
                "total_new_items": total_live_missed,
                "factories": [
                    {
                        "factory_name": fn,
                        "factory_id": it_list[0]["factory_id"] if it_list else None,
                        "items_count": len(it_list),
                        "items": it_list,
                    }
                    for fn, it_list in sorted(all_missed_live.items(), key=lambda x: len(x[1]), reverse=True)
                ]
            }
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(summary_data, f, ensure_ascii=False, indent=2)

            with open(out_txt, "w", encoding="utf-8") as f:
                f.write(f"СПИСОК УПУЩЕННЫХ ТУРЕЦКИХ ФАБРИК И ТОВАРОВ ДЛЯ XL (ВСЕГО {total_live_missed} ТОВАРОВ):\n")
                f.write("=" * 60 + "\n")
                for f_idx, (fn, it_list) in enumerate(sorted(all_missed_live.items(), key=lambda x: len(x[1]), reverse=True), 1):
                    f.write(f"{f_idx}. {fn} — {len(it_list)}\n")
        else:
            log.info(
                "[%d/%d] '%s' (ID %s): %d TR candidates in Afterbuy, but 0 live online on OTTO XL (drafts)",
                idx, len(target_factories), fname, fid, len(tr_candidates)
            )

    elapsed = time.time() - start_time
    log.info("================================================================")
    log.info("  SCAN COMPLETED in %.1f minutes!", elapsed / 60)
    log.info("  Total new live Turkish products found on OTTO XL: %d across %d factories", total_live_missed, len(all_missed_live))
    log.info("  Results saved to %s and %s", out_json, out_txt)
    log.info("================================================================")

    for f_idx, (fn, it_list) in enumerate(sorted(all_missed_live.items(), key=lambda x: len(x[1]), reverse=True), 1):
        log.info("  %d. %s: %d items", f_idx, fn, len(it_list))

if __name__ == "__main__":
    asyncio.run(main())
