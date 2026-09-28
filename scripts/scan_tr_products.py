"""Universal scanner for Turkish products in Afterbuy and live OTTO.
Supports both JV and XL accounts.
"""

import argparse
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

from sqlalchemy import func, select
from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.database import SessionLocal, engine
from app.models.factories import Factories
from app.schemas.external_schemes.until_schemes import GetProductRequest
from scripts.tr_rules import is_turkish_product

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("scan_tr_products")


async def main():
    parser = argparse.ArgumentParser(description="Scan Turkish products for JV or XL account")
    parser.add_argument("--account", choices=["jv", "xl"], default="xl", help="Account: jv or xl")
    parser.add_argument("--skip-otto-verify", action="store_true", help="Skip live OTTO API verification")
    parser.add_argument("--concurrency", type=int, default=10, help="Async concurrency limit")
    args = parser.parse_args()

    account = args.account.lower()
    log.info("================================================================")
    log.info("  Scanning Turkish Products for Account: %s", account.upper())
    log.info("  Live OTTO Verification: %s", "OFF" if args.skip_otto_verify else "ON")
    log.info("================================================================")

    # 1. Load factories from database
    async with SessionLocal() as db:
        res = await db.execute(
            select(Factories).where(
                func.lower(Factories.account) == account,
                Factories.items_count > 0,
            )
        )
        factories = res.scalars().all()

    log.info("Found %d non-empty factories for account '%s'", len(factories), account)

    # 2. Connect to Afterbuy with retry
    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=45.0)
    session = None
    for attempt in range(5):
        try:
            session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
            log.info("Successfully connected to Afterbuy.")
            break
        except Exception as exc:
            log.warning("Afterbuy login attempt %d/5 failed: %s. Retrying in 3s...", attempt + 1, exc)
            await asyncio.sleep(3)

    if not session:
        log.error("Could not authenticate with Afterbuy after 5 attempts.")
        return

    sem = asyncio.Semaphore(args.concurrency)
    candidates_by_factory = defaultdict(list)
    scanned_count = 0

    async def fetch_factory(f: Factories):
        nonlocal scanned_count
        async with sem:
            scanned_count += 1
            if scanned_count % 100 == 0 or scanned_count == len(factories):
                log.info("  Fetched Afterbuy products from %d / %d factories...", scanned_count, len(factories))

            try:
                resp = await afterbuy.send_request(
                    "GET",
                    "/api/products",
                    params={"account": account, "dataset": "lister", "factory_id": f.factory_id, "limit": 0, "include_row": 1},
                    cookies={"session": session},
                )
                items = resp.json().get("items", [])
                for it in items:
                    row = it.get("row", {})
                    ean = str(row.get("EAN") or row.get("sku") or "").strip()
                    if not ean:
                        continue

                    is_tr, reason = is_turkish_product(row)
                    if is_tr:
                        title = str(row.get("Artikelbeschreibung") or "")
                        candidates_by_factory[f.name].append({
                            "ean": ean,
                            "factory_name": f.name,
                            "factory_id": f.factory_id,
                            "reason": reason,
                            "title": title[:60],
                        })
            except Exception as exc:
                log.warning("Failed to fetch factory %s (ID %s): %s", f.name, f.factory_id, exc)

    log.info("Fetching products from Afterbuy...")
    await asyncio.gather(*[fetch_factory(f) for f in factories])

    # Deduplicate candidates
    all_unique_candidates = {}
    for fname, items in candidates_by_factory.items():
        for it in items:
            ean = it["ean"]
            if ean not in all_unique_candidates:
                all_unique_candidates[ean] = it

    log.info("Afterbuy scan complete: %d candidate Turkish items across %d factories/folders",
             len(all_unique_candidates), len(candidates_by_factory))

    # 3. Live OTTO Verification
    final_live_items = []
    if not args.skip_otto_verify:
        log.info("Verifying candidate items against live OTTO %s API...", account.upper())
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

        otto_sem = asyncio.Semaphore(10)
        checked_cnt = 0
        already_39_cnt = 0
        not_found_cnt = 0

        async def verify_sku(it):
            nonlocal checked_cnt, already_39_cnt, not_found_cnt
            sku = it["ean"]
            async with otto_sem:
                checked_cnt += 1
                if checked_cnt % 500 == 0 or checked_cnt == len(all_unique_candidates):
                    log.info("  Verified %d / %d items on OTTO (Found needing update: %d)...",
                             checked_cnt, len(all_unique_candidates), len(final_live_items))

                for attempt in range(3):
                    try:
                        res = await otto.get_products(GetProductRequest(sku=sku), account)
                        vars_ = res.get("productVariations") or []
                        if not vars_:
                            not_found_cnt += 1
                            return
                        dt = (vars_[0].get("delivery") or {}).get("deliveryTime")
                        if dt == 39:
                            already_39_cnt += 1
                            return
                        it["current_delivery_time"] = dt
                        final_live_items.append(it)
                        return
                    except Exception as e:
                        if "429" in str(e):
                            await asyncio.sleep(2 * (attempt + 1))
                        else:
                            await asyncio.sleep(0.5)

        await asyncio.gather(*[verify_sku(it) for it in all_unique_candidates.values()])
        log.info("OTTO Verification summary: Live needing update: %d | Already 39 days: %d | Not on OTTO (drafts): %d",
                 len(final_live_items), already_39_cnt, not_found_cnt)
    else:
        final_live_items = list(all_unique_candidates.values())

    # 4. Group results and save reports
    by_folder = defaultdict(list)
    for it in final_live_items:
        by_folder[it["factory_name"]].append(it)

    json_output_file = f"/app/tr_products_{account}.json"
    summary_txt_file = f"/app/factories_{account}.txt"

    # Save JSON
    output_data = {
        "account": account,
        "total_items": len(final_live_items),
        "total_factories": len(by_folder),
        "scan_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "factories": [
            {
                "factory_name": fn,
                "factory_id": items[0]["factory_id"],
                "count": len(items),
                "items": items,
            }
            for fn, items in sorted(by_folder.items(), key=lambda x: len(x[1]), reverse=True)
        ],
    }
    with open(json_output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    # Save human-readable text file
    lines = []
    lines.append(f"СПИСОК ФАБРИК И ТОВАРОВ ИЗ ТУРЦИИ ДЛЯ АККАУНТА {account.upper()}:")
    lines.append("=" * 60)
    for idx, (fn, items) in enumerate(sorted(by_folder.items(), key=lambda x: len(x[1]), reverse=True), 1):
        lines.append(f"{idx}. {fn} — {len(items)}")
    lines.append("=" * 60)
    lines.append(f"ИТОГО: {len(final_live_items)} товаров в {len(by_folder)} фабриках / папках")

    with open(summary_txt_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    log.info("Saved data to %s and %s", json_output_file, summary_txt_file)
    print("\n" + "\n".join(lines) + "\n", flush=True)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
