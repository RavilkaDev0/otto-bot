import asyncio
import json
import re
from collections import Counter
from app.core.configs import settings
from app.database import SessionLocal, engine
from sqlalchemy import select, func
from app.models.factories import Factories
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient

async def check():
    print("=== Scanning All Timur Factories ===\n", flush=True)
    
    # 1. Load already updated SKUs from jv_run.log
    updated_yesterday = set()
    try:
        with open("/app/jv_run.log", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "OK (Catalog & Availability updated)" in line or "Accepted by OTTO async queue" in line:
                    match = re.search(r"sku=([^\s]+)", line)
                    if match:
                        updated_yesterday.add(match.group(1).strip())
        print(f"Loaded {len(updated_yesterday)} SKUs already updated yesterday.", flush=True)
    except Exception as e:
        print(f"Warning: {e}", flush=True)

    # 2. Get all Timur factories from DB
    async with SessionLocal() as db:
        res = await db.execute(select(Factories).where(Factories.name.ilike("%Timur%")))
        timur_factories = res.scalars().all()
        
        # Load Online EANs for JV
        res_online = await db.execute(
            select(OttoXlsxImportRow.ean)
            .where(OttoXlsxImportRow.account == "jv", OttoXlsxImportRow.marketplace_status == "Online")
        )
        online_eans_set = {str(r[0]).strip() for r in res_online.all() if r[0]}
        
    print(f"Found {len(timur_factories)} factories matching 'Timur':", flush=True)
    for f in timur_factories:
        print(f"  * ID: {f.factory_id} | Name: '{f.name}' | Account: {f.account} | Items: {f.items_count}", flush=True)
        
    print(f"\nTotal Online EANs in local DB (JV): {len(online_eans_set)}\n", flush=True)

    # 3. Connect to Aftercool and inspect all products for each Timur factory
    client = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=45.0)
    session = await client.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    
    detailed_results = []
    
    for f in timur_factories:
        # Check by account
        acc = f.account.lower()
        try:
            resp = await client.send_request(
                "GET",
                "/api/products",
                params={
                    "account": acc,
                    "dataset": "lister",
                    "factory_id": f.factory_id,
                    "include_row": 1,
                    "limit": 0,
                },
                cookies={"session": session}
            )
            items = resp.json().get("items", [])
            print(f"\n--- Checking {f.name} (ID: {f.factory_id}, Account: {f.account}) ---", flush=True)
            print(f"Total items in Aftercool: {len(items)}", flush=True)
            
            tr_items = []
            non_tr_items = []
            
            for it in items:
                row = it.get("row", {})
                ean = str(row.get("EAN") or row.get("ean") or row.get("sku") or "").strip()
                title = str(row.get("Artikelbeschreibung") or "")
                gallery = str(row.get("GalleryURL") or "")
                picture = str(row.get("PictureURL") or "")
                picurls = str(row.get("pictureurls") or "")
                all_images = f"{gallery} {picture} {picurls}"
                
                # Check Turkey markers:
                is_tr = False
                reasons = []
                
                # Marker 1: image path
                if re.search(r"(_TR\b|/TR/|_TR_)", all_images, re.I):
                    is_tr = True
                    reasons.append("TR in image path")
                for brand in ["nott", "merli", "favory", "seyran", "evart", "moreno", "woodface", "depotum", "acel", "saloni", "saltanat", "bellona", "enza"]:
                    if brand in all_images.lower():
                        is_tr = True
                        reasons.append(f"brand '{brand}' in image path")
                        break
                        
                # Marker 2: title / description
                if re.search(r"(\bTR\b|_TR|_TR_|\(TR\))", title, re.I):
                    is_tr = True
                    reasons.append("TR tag in title")
                    
                is_online = ean in online_eans_set if acc == "jv" else False
                already_up = ean in updated_yesterday
                
                item_data = {
                    "ean": ean,
                    "title": title[:50],
                    "is_tr": is_tr,
                    "reasons": reasons,
                    "is_online": is_online,
                    "already_updated": already_up,
                    "image_sample": gallery[:70]
                }
                
                if is_tr:
                    tr_items.append(item_data)
                else:
                    non_tr_items.append(item_data)
                    
            print(f"  Turkish items detected: {len(tr_items)} / {len(items)}", flush=True)
            if tr_items:
                reasons_count = Counter([", ".join(x["reasons"]) for x in tr_items])
                print(f"  Detection reasons: {dict(reasons_count)}", flush=True)
                
            online_tr = [x for x in tr_items if x["is_online"]]
            new_online_tr = [x for x in online_tr if not x["already_updated"]]
            print(f"  Online in JV: {len(online_tr)} (New to update: {len(new_online_tr)})", flush=True)
            if new_online_tr:
                print(f"  Sample new EANs: {[x['ean'] for x in new_online_tr[:3]]}", flush=True)
                
            detailed_results.append({
                "factory_id": f.factory_id,
                "name": f.name,
                "account": f.account,
                "total_items": len(items),
                "tr_items_count": len(tr_items),
                "online_tr_count": len(online_tr),
                "new_online_tr_count": len(new_online_tr),
                "sample_new_eans": [x["ean"] for x in new_online_tr[:3]],
                "sample_image": tr_items[0]["image_sample"] if tr_items else ""
            })
            
        except Exception as e:
            print(f"  Error: {e}", flush=True)

    print("\n=== TIMUR FACTORIES SUMMARY ===", flush=True)
    total_new = sum(r["new_online_tr_count"] for r in detailed_results)
    print(f"Total new Turkish Online products in Timur factories: {total_new}\n", flush=True)
    
    with open("/app/timur_tr_results.json", "w", encoding="utf-8") as out:
        json.dump(detailed_results, out, ensure_ascii=False, indent=2)

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check())
