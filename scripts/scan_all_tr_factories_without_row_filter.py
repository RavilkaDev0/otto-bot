"""Comprehensive scan of ALL Turkish factories in Afterbuy for account XL.
Key fix: If a factory has (TR) or is a known Turkish supplier, ALL of its items
belong to Turkey (we do not reject items just because 'country TR' is missing from description).
We verify each item directly against OTTO XL API and check if deliveryTime != 39.
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/scan_batch3.log", mode="w", encoding="utf-8"),
        logging.FileHandler("/app/logs/otto_bot.log", mode="a", encoding="utf-8"),
    ],
)
log = logging.getLogger("scan_batch3")

KNOWN_TR_PATTERNS = [
    r"\(tr\)", r"_tr\b", r"_tr_", r"\(tr\b",
    r"timur", r"favory", r"radmila", r"serhat", r"alpino",
    r"modelin", r"kaju", r"eymense", r"tuare", r"montro",
    r"voguess", r"ropez", r"katre", r"epika", r"inci",
    r"sandalie", r"brezza", r"soffi", r"selimoglu", r"boyneroglu",
    r"burosit", r"bürosit", r"bussy", r"redof", r"alber",
    r"alite", r"nills", r"titi", r"uyku", r"vionse", r"duga",
    r"arwen", r"tomi", r"hafsam", r"myway", r"nevizada",
    r"saka", r"rotta", r"somm", r"zeta", r"varisse", r"cchm",
    r"bogao", r"alist"
]

def is_candidate_tr_factory(name: str) -> bool:
    low = name.lower()
    if "others_löschen" in low or "others_loschen" in low:
        return False
    # Skip clearly non-Turkish
    if "_(pl)" in low or "_(it)" in low or "_(cn)" in low or "_(de)" in low or "_(uk)" in low:
        return False
    for pat in KNOWN_TR_PATTERNS:
        if re.search(pat, low):
            return True
    return False

async def main():
    log.info("================================================================")
    log.info("  SCANNING ALL CANDIDATE TURKISH FACTORIES FOR OTTO XL (BATCH 3)")
    log.info("================================================================")

    # 1. Load already updated SKUs across Batch 1 & 2
    updated_skus = set()
    for path in ["/app/tr_products_xl.json", "/app/tr_products_xl_missed_batch.json"]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
                for fact in d.get("factories", []):
                    for it in fact.get("items", []):
                        s = str(it.get("ean") or it.get("sku") or "").strip()
                        if s:
                            updated_skus.add(s)
    log.info("Total already updated SKUs on XL: %d", len(updated_skus))

    # 2. Load all Afterbuy factories
    with open("/app/all_afterbuy_xl_factories.json", "r", encoding="utf-8") as f:
        all_factories = json.load(f)

    candidate_factories = []
    for f in all_factories:
        name = f.get("name", "").strip()
        cnt = f.get("items_count", 0)
        if cnt == 0:
            continue
        if is_candidate_tr_factory(name):
            candidate_factories.append(f)

    log.info("Total candidate Turkish factories to inspect: %d", len(candidate_factories))

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
    confirmed_by_factory = defaultdict(list)
    total_new_live = 0

    for idx, fact in enumerate(candidate_factories, 1):
        fname = fact.get("name")
        fid = fact.get("id")
        cnt = fact.get("items_count", 0)

        # Paginated fetch from Afterbuy
        items = []
        offset = 0
        limit = 200
        while True:
            try:
                resp = await afterbuy.send_request(
                    "GET",
                    "/api/products",
                    params={
                        "account": "xl",
                        "dataset": "lister",
                        "factory_id": fid,
                        "limit": limit,
                        "offset": offset,
                        "include_row": 1,
                    },
                    cookies={"session": session},
                )
                batch = resp.json().get("items", [])
                if not batch:
                    break
                items.extend(batch)
                if len(batch) < limit or len(items) >= cnt:
                    break
                offset += limit
            except Exception as e:
                log.warning("Factory '%s' fetch error at offset %d: %s", fname, offset, e)
                break

        # Filter items not yet updated
        candidate_items = []
        for it in items:
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if not sku or sku in updated_skus:
                continue
            candidate_items.append((sku, row))

        if not candidate_items:
            continue

        # Check live OTTO XL status
        live_in_factory = []

        async def check_one(sku, row):
            async with sem:
                for attempt in range(2):
                    try:
                        res = await otto.get_products(GetProductRequest(sku=sku), "xl")
                        vars_ = res.get("productVariations") or []
                        if vars_:
                            dt = (vars_[0].get("delivery") or {}).get("deliveryTime")
                            if dt != 39:
                                live_in_factory.append({
                                    "sku": sku,
                                    "ean": sku,
                                    "title": str(row.get("Artikelbeschreibung") or "")[:60],
                                    "factory_name": fname,
                                    "factory_id": fid,
                                    "current_delivery_time": dt,
                                })
                        return
                    except Exception:
                        await asyncio.sleep(0.5)

        await asyncio.gather(*[check_one(s, r) for s, r in candidate_items])

        if live_in_factory:
            confirmed_by_factory[fname].extend(live_in_factory)
            total_new_live += len(live_in_factory)
            log.info(">>> [%d/%d] '%s': FOUND %d LIVE ITEMS ON OTTO XL! (Total batch 3 so far: %d)",
                     idx, len(candidate_factories), fname, len(live_in_factory), total_new_live)
        else:
            if idx % 10 == 0:
                log.info("[%d/%d] '%s': checked %d items (0 online unupdated)",
                         idx, len(candidate_factories), fname, len(candidate_items))

    log.info("================================================================")
    log.info("  BATCH 3 SCAN COMPLETE!")
    log.info("  Total new live Turkish products on OTTO XL: %d across %d factories",
             total_new_live, len(confirmed_by_factory))
    log.info("================================================================")

    for fn, it_list in sorted(confirmed_by_factory.items(), key=lambda x: len(x[1]), reverse=True):
        log.info("  * %s: %d items", fn, len(it_list))

    # Save to JSON
    output_json = {
        "account": "xl",
        "total_new_items": total_new_live,
        "factories": [
            {
                "factory_name": fn,
                "factory_id": it_list[0]["factory_id"] if it_list else None,
                "items_count": len(it_list),
                "items": it_list,
            }
            for fn, it_list in confirmed_by_factory.items()
        ]
    }

    with open("/app/tr_products_xl_batch3.json", "w", encoding="utf-8") as f:
        json.dump(output_json, f, ensure_ascii=False, indent=2)

    with open("/app/factories_xl_batch3.txt", "w", encoding="utf-8") as f:
        f.write(f"СПИСОК ТУРЕЦКИХ ТОВАРОВ ИЗ ДОПОЛНИТЕЛЬНЫХ ФАБРИК ДЛЯ XL (ВСЕГО {total_new_live} ТОВАРОВ):\n")
        f.write("=" * 60 + "\n")
        for i, (fn, it_list) in enumerate(sorted(confirmed_by_factory.items(), key=lambda x: len(x[1]), reverse=True), 1):
            f.write(f"{i}. {fn} — {len(it_list)}\n")

    log.info("Saved to /app/tr_products_xl_batch3.json and /app/factories_xl_batch3.txt")

if __name__ == "__main__":
    asyncio.run(main())
