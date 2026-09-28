"""Deep analysis of all Timur_for_Otto_Standard folders (II, 3, 4, 5, 6, 7, 8, 9) on Aftercool vs OTTO API."""

import asyncio
import json
import os
import re
import sys
from collections import defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.database import SessionLocal, engine
from app.schemas.external_schemes.until_schemes import GetProductRequest

TR_FOLDERS_OR_BRANDS = [
    "tr", "merli", "evart", "enza", "nott", "woodface", "depotum", "seyran",
    "acel", "saloni", "saltanat", "bellona", "burosit", "bürosit", "duga"
]

def is_turkish_product(row: dict) -> tuple[bool, str]:
    all_imgs = f"{row.get('GalleryURL', '')} {row.get('PictureURL', '')} {row.get('pictureurls', '')}"
    title = str(row.get("Artikelbeschreibung") or "")
    
    # 1. Folder in upload path
    match = re.search(r"AfterbuyUploadService/([^/]+)/", all_imgs)
    if match:
        folder = match.group(1).lower()
        if re.search(r"(_tr\b|/tr/|_tr_|\(tr\))", folder):
            return True, f"folder '{folder}' has TR tag"
        for b in TR_FOLDERS_OR_BRANDS:
            if b in folder:
                return True, f"folder '{folder}' has TR brand '{b}'"
                
    # 2. Title marker
    if re.search(r"(_TR\b|_TR_|\(TR\))", title, re.I):
        return True, "TR tag in title"
        
    return False, ""

async def main():
    print("=== DEEP AUDIT: Timur_for_Otto_Standard (II, 3, 4, 5, 6, 7, 8, 9) ===\n", flush=True)

    client = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await client.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

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

    # Load previously updated EANs (from jv_run.log and master_remaining_tr_factories.json)
    already_updated_eans = set()
    try:
        with open("/app/jv_run.log") as f:
            for line in f:
                if "OK (Catalog & Availability updated)" in line:
                    m = re.search(r"sku=([^\s]+)", line)
                    if m:
                        already_updated_eans.add(m.group(1).strip())
    except Exception:
        pass
        
    try:
        with open("/app/master_remaining_tr_factories.json") as f:
            data = json.load(f)
            for it in data:
                already_updated_eans.update(it.get("all_eans", []))
    except Exception:
        pass

    print(f"Loaded {len(already_updated_eans)} already updated EANs.\n", flush=True)

    timur_folders = [
        ("503865", "Timur_for_Otto_Standard_II"),
        ("503923", "Timur_for_Ottor_Standard_3"),
        ("503944", "Timur_for_Ottor_Standard_4"),
        ("504001", "Timur_for_Otto_Standard_5"),
        ("504063", "Timur_for_Otto_Standard_6"),
        ("504104", "Timur_for_Otto_Standard_7"),
        ("504135", "Timur_for_Otto_Standard_8"),
        ("504190", "Timur_for_Otto_Standard_9"),
    ]

    summary = []

    for fid, name in timur_folders:
        resp = await client.send_request(
            "GET",
            "/api/products",
            params={"account": "jv", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
            cookies={"session": session}
        )
        items = resp.json().get("items", [])
        
        tr_items = []
        for it in items:
            row = it.get("row", {})
            ean = str(row.get("EAN") or row.get("sku") or "").strip()
            if not ean:
                continue
            is_tr, reason = is_turkish_product(row)
            if is_tr:
                tr_items.append((ean, reason, str(row.get("Artikelbeschreibung", ""))[:40]))

        # Check how many are already updated vs NOT updated
        already_cnt = sum(1 for ean, _, _ in tr_items if ean in already_updated_eans)
        unupdated = [x for x in tr_items if x[0] not in already_updated_eans]
        
        print(f"--- {name} (ID: {fid}) ---", flush=True)
        print(f"  Total items in Afterbuy: {len(items)}", flush=True)
        print(f"  Turkish items detected: {len(tr_items)}", flush=True)
        print(f"  Already updated today/yesterday: {already_cnt}", flush=True)
        print(f"  Remaining un-updated TR items: {len(unupdated)}", flush=True)
        if unupdated:
            print(f"  Sample un-updated EANs: {[x[0] for x in unupdated[:3]]}", flush=True)
            
        summary.append({
            "factory_id": fid,
            "name": name,
            "total_afterbuy": len(items),
            "turkish_total": len(tr_items),
            "already_updated": already_cnt,
            "unupdated_count": len(unupdated),
            "unupdated_items": unupdated
        })

    # Test 5 sample un-updated items on OTTO API directly
    all_unupdated = [x for s in summary for x in s["unupdated_items"]]
    print(f"\n==================================================", flush=True)
    print(f"TOTAL UNUPDATED TR ITEMS IN TIMUR FOLDERS: {len(all_unupdated)}", flush=True)
    print(f"Checking status of sample un-updated items directly on OTTO API (JV):", flush=True)
    
    for ean, reason, title in all_unupdated[:5]:
        try:
            res = await otto.get_products(GetProductRequest(sku=ean), "jv")
            vars_jv = res.get("productVariations", [])
            if vars_jv:
                deliv = vars_jv[0].get("delivery", {})
                print(f"  SKU: {ean} -> FOUND ON OTTO! current deliveryTime: {deliv.get('deliveryTime')} | Title: {title}")
            else:
                print(f"  SKU: {ean} -> NOT found on OTTO (draft/deleted)")
        except Exception as e:
            print(f"  SKU: {ean} -> OTTO error: {e}")

    with open("/app/timur_unupdated_audit.json", "w") as out:
        json.dump(summary, out, ensure_ascii=False, indent=2)

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
