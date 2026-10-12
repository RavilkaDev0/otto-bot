"""Scan Timur 8 and 9 and all other missed factories directly from Afterbuy."""

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("scan_all_xl")

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

async def scan():
    # 1. Load already updated SKUs from tr_products_xl.json
    already_updated_skus = set()
    if os.path.exists("/app/tr_products_xl.json"):
        with open("/app/tr_products_xl.json", "r", encoding="utf-8") as f:
            d = json.load(f)
            for fact in d.get("factories", []):
                for it in fact.get("items", []):
                    s = str(it.get("ean") or it.get("sku") or "").strip()
                    if s:
                        already_updated_skus.add(s)
    log.info("Already updated on XL: %d SKUs", len(already_updated_skus))

    # 2. Load all Afterbuy factories
    with open("/app/all_afterbuy_xl_factories.json", "r", encoding="utf-8") as f:
        all_factories = json.load(f)

    # 3. Identify all Turkish factories: (TR) in name, or Timur, or in JV factories.txt
    with open("/app/factories.txt", "r", encoding="utf-8") as f:
        jv_lines = f.readlines()
    jv_names = set()
    for l in jv_lines:
        m = re.match(r"^\d+\.\s+([^\s—]+(?:\s+[^\s—]+)*)\s+—", l)
        if m:
            clean = re.sub(r"\s+\([^)]+\)", "", m.group(1)).strip().lower()
            jv_names.add(clean)

    target_factories = []
    for f in all_factories:
        name = f.get("name", "").strip()
        low = name.lower()
        clean = re.sub(r"\s+\([^)]+\)", "", low).strip()
        cnt = f.get("items_count", 0)
        if cnt == 0:
            continue
        if "others_löschen" in low or "others_loschen" in low:
            continue  # 14k archive items, skip for now

        is_tr = (
            "(tr)" in low
            or "_tr" in low
            or low.endswith("_tr")
            or "timur" in low
            or any(j in low or low in j for j in jv_names)
        )
        if is_tr:
            target_factories.append(f)

    log.info("Total Turkish/Timur candidate factories to check: %d", len(target_factories))

    # 4. Connect to Afterbuy
    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=45.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    log.info("Connected to Afterbuy successfully.")

    # 5. Fetch products for each factory
    otto = build_otto_client()
    sem_afterbuy = asyncio.Semaphore(5)
    sem_otto = asyncio.Semaphore(15)

    all_missed_live = defaultdict(list)
    total_new_candidates = 0

    async def process_factory(fact):
        nonlocal total_new_candidates
        fname = fact.get("name")
        fid = fact.get("id")
        cnt = fact.get("items_count", 0)

        # Fetch products page by page or all
        async with sem_afterbuy:
            try:
                resp = await afterbuy.send_request(
                    "GET",
                    "/api/products",
                    params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
                    cookies={"session": session},
                )
                items = resp.json().get("items", [])
            except Exception as e:
                log.warning("Failed to fetch %s (ID %s): %s", fname, fid, e)
                return

        # Check TR rules
        tr_candidates = []
        for it in items:
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if not sku or sku in already_updated_skus:
                continue
            is_tr_prod, reason = is_turkish_product(row)
            if is_tr_prod:
                tr_candidates.append((sku, str(row.get("Artikelbeschreibung") or "")[:50], reason))

        if not tr_candidates:
            return

        total_new_candidates += len(tr_candidates)
        log.info("Factory '%s' (ID %s): %d items, %d TR candidates to check on OTTO...", fname, fid, len(items), len(tr_candidates))

        # Check OTTO XL API
        async def verify_sku(sku, title, reason):
            async with sem_otto:
                for attempt in range(3):
                    try:
                        res = await otto.get_products(GetProductRequest(sku=sku), "xl")
                        vars_ = res.get("productVariations") or []
                        if not vars_:
                            return  # Draft / Not online
                        dt = (vars_[0].get("delivery") or {}).get("deliveryTime")
                        if dt != 39:
                            all_missed_live[fname].append({
                                "sku": sku,
                                "ean": sku,
                                "title": title,
                                "factory_name": fname,
                                "factory_id": fid,
                                "current_delivery_time": dt,
                                "reason": reason,
                            })
                        return
                    except Exception:
                        await asyncio.sleep(1)

        await asyncio.gather(*[verify_sku(s, t, r) for s, t, r in tr_candidates])

    # Run in batches of factories
    batch_size = 15
    for i in range(0, len(target_factories), batch_size):
        batch = target_factories[i : i + batch_size]
        log.info("Processing factories batch %d - %d / %d...", i + 1, min(i + batch_size, len(target_factories)), len(target_factories))
        await asyncio.gather(*[process_factory(f) for f in batch])

    # 6. Summary
    log.info("================================================================")
    log.info("  SCAN COMPLETE!")
    total_live_missed = sum(len(v) for v in all_missed_live.values())
    log.info("  Total new live Turkish products found on OTTO XL: %d across %d factories", total_live_missed, len(all_missed_live))
    log.info("================================================================")

    for fn, items in sorted(all_missed_live.items(), key=lambda x: len(x[1]), reverse=True):
        log.info("  * %s: %d live items", fn, len(items))

    # Save to JSON
    output_data = {
        "account": "xl",
        "total_new_items": total_live_missed,
        "factories": [
            {
                "factory_name": fn,
                "factory_id": items[0]["factory_id"] if items else None,
                "items_count": len(items),
                "items": items,
            }
            for fn, items in sorted(all_missed_live.items(), key=lambda x: len(x[1]), reverse=True)
        ]
    }
    with open("/app/tr_products_xl_missed_batch.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    # Human readable text
    with open("/app/factories_xl_missed.txt", "w", encoding="utf-8") as f:
        f.write(f"СПИСОК УПУЩЕННЫХ ТУРЕЦКИХ ФАБРИК И ТОВАРОВ ДЛЯ XL (ВСЕГО {total_live_missed} ТОВАРОВ):\n")
        f.write("=" * 60 + "\n")
        for idx, (fn, items) in enumerate(sorted(all_missed_live.items(), key=lambda x: len(x[1]), reverse=True), 1):
            f.write(f"{idx}. {fn} — {len(items)}\n")

if __name__ == "__main__":
    asyncio.run(scan())
