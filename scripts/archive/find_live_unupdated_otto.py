"""Fast async checker to verify which remaining candidate items are ACTUALLY live on OTTO JV and need updating."""

import asyncio
import json
import sys
import time
from collections import defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

import httpx
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.clients.external_otto_client import ExternalOttoClient
from app.schemas.external_schemes.until_schemes import GetProductRequest

async def main():
    print("=== Checking all 4,888 candidate items directly against live OTTO API ===\n", flush=True)

    with open("/app/remaining_unupdated_tr_all.json") as f:
        folders = json.load(f)

    # Collect unique items with metadata
    unique_items_map = {}
    for f in folders:
        fname = f["factory_name"]
        fid = f["factory_id"]
        for it in f["all_items"]:
            ean = it["ean"]
            if ean not in unique_items_map:
                unique_items_map[ean] = {
                    "ean": ean,
                    "factory_name": fname,
                    "factory_id": fid,
                    "reason": it["reason"],
                    "title": it["title"]
                }

    items_list = list(unique_items_map.values())
    print(f"Total unique candidate items to check on OTTO: {len(items_list)}", flush=True)

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
    checked_count = 0
    found_live = []
    already_39 = 0
    not_found = 0

    async def check_item(it):
        nonlocal checked_count, already_39, not_found
        sku = it["ean"]
        async with sem:
            checked_count += 1
            if checked_count % 500 == 0:
                print(f"  Checked {checked_count} / {len(items_list)} items on OTTO (Found live: {len(found_live)})...", flush=True)

            try:
                res = await otto.get_products(GetProductRequest(sku=sku), "jv")
                vars_jv = res.get("productVariations") or []
                if not vars_jv:
                    not_found += 1
                    return
                    
                v = vars_jv[0]
                current_time = (v.get("delivery") or {}).get("deliveryTime")
                if current_time == 39:
                    already_39 += 1
                    return
                    
                found_live.append({
                    "sku": sku,
                    "factory_name": it["factory_name"],
                    "factory_id": it["factory_id"],
                    "reason": it["reason"],
                    "title": it["title"],
                    "current_delivery_time": current_time
                })
            except Exception:
                pass

    start = time.time()
    await asyncio.gather(*[check_item(it) for it in items_list])
    elapsed = time.time() - start

    print(f"\n=== OTTO LIVE CHECK RESULTS (in {elapsed:.1f}s) ===", flush=True)
    print(f"Checked: {len(items_list)}")
    print(f"Not found on OTTO (drafts/non-uploaded): {not_found}")
    print(f"Already set to 39 days: {already_39}")
    print(f"ACTUALLY LIVE AND NEED UPDATING: {len(found_live)}\n", flush=True)

    by_factory = defaultdict(list)
    for it in found_live:
        by_factory[it["factory_name"]].append(it)

    print("Breakdown of live unupdated items by factory/folder:")
    summary_report = []
    for fname, items in sorted(by_factory.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"  * {fname} (ID: {items[0]['factory_id']}): {len(items)} items | Samples: {[x['sku'] for x in items[:2]]}")
        summary_report.append({
            "factory_name": fname,
            "factory_id": items[0]["factory_id"],
            "count": len(items),
            "sample_skus": [x["sku"] for x in items[:3]],
            "all_skus": [x["sku"] for x in items]
        })

    with open("/app/live_unupdated_tr_otto.json", "w", encoding="utf-8") as out:
        json.dump(summary_report, out, ensure_ascii=False, indent=2)

    print("\nSaved live items report to /app/live_unupdated_tr_otto.json", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
