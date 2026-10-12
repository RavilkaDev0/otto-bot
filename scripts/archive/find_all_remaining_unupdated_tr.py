"""Exhaustive scan for any remaining Turkish products in Afterbuy that need updating.
Skips all products already updated in previous runs.
"""

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
from sqlalchemy import select, func
from app.database import SessionLocal, engine
from app.models.factories import Factories
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.schemas.external_schemes.until_schemes import GetProductRequest
from scripts.tr_factories_list import VALID_TR_FACTORIES

# Clean Turkish factory names
CLEAN_TR_NAMES = set()
for f in VALID_TR_FACTORIES:
    clean = re.sub(r"(_|\s)*\((TR|Tr)\)", "", f).strip().lower()
    clean = re.sub(r"(_|\s)*TR$", "", clean).strip()
    if len(clean) >= 3:
        CLEAN_TR_NAMES.add(clean)

ADDITIONAL_TR_BRANDS = {
    "woodface", "ulgen", "depotum", "nott", "favory", "seyran", "bermonti",
    "evart", "vivanza", "wossew", "atolyekoltuk", "merli", "burosit", "bürosit",
    "duga", "acel", "saltanat", "saloni", "sandalya", "koltuk", "mobilya",
    "deco_hill", "vanitti", "luce_garden", "newsett", "alonex", "mse_bahce",
    "enter_sofa", "natura_outdoor", "newberonni", "essence", "zekioglu", "ada",
    "vettoro", "brezza", "voguess", "trio", "triodoor", "burobig", "rixxe",
    "erika", "tuare", "verengarden", "safa", "modelin", "cisa", "kuka", "patroon"
}
ALL_TR_TARGETS = CLEAN_TR_NAMES | ADDITIONAL_TR_BRANDS

def extract_tr_origin(row: dict) -> tuple[bool, str]:
    all_imgs = f"{row.get('GalleryURL', '')} {row.get('PictureURL', '')} {row.get('pictureurls', '')}"
    title = str(row.get("Artikelbeschreibung") or "")
    desc = str(row.get("Description") or "") + " " + str(row.get("TranslatedDescription") or "")
    specifics = str(row.get("CustomItemSpecifics") or "")
    combined_text = f"{title} {desc} {specifics}"

    # 1. Image upload folder
    m = re.search(r"AfterbuyUploadService/([^/]+)/", all_imgs)
    if m:
        folder = m.group(1).strip()
        folder_low = folder.lower()
        if re.search(r"(_tr\b|/tr/|_tr_|\(tr\))", folder, re.I):
            return True, f"Upload folder '{folder}' has TR tag"
        clean_folder = re.sub(r"^\d+_", "", folder_low).strip()
        for tr_name in ALL_TR_TARGETS:
            if clean_folder == tr_name or clean_folder.startswith(tr_name + "_") or clean_folder.endswith("_" + tr_name):
                return True, f"Upload folder '{folder}' matches TR brand '{tr_name}'"

    # 2. Text markers
    if re.search(r"\bcountry\s+TR\b", combined_text, re.I):
        return True, "'country TR' in description"
    if re.search(r"\bcity\s+INE\b", combined_text, re.I):
        return True, "'city INE' in description"
    if re.search(r"(_TR\b|_TR_|\(TR\)|_\(TR\))", title, re.I):
        return True, "TR tag in title"

    return False, ""

async def main():
    print("=== SEARCHING FOR ALL REMAINING UNUPDATED TURKISH PRODUCTS ===\n", flush=True)

    # 1. Collect ALL already updated EANs to skip
    all_updated_eans = set()
    
    # Yesterday
    try:
        with open("/app/jv_run.log") as f:
            for line in f:
                if "OK (Catalog & Availability updated)" in line:
                    m = re.search(r"sku=([^\s]+)", line)
                    if m:
                        all_updated_eans.add(m.group(1).strip())
    except Exception:
        pass
        
    # Today batch 1
    try:
        with open("/app/master_remaining_tr_factories.json") as f:
            for it in json.load(f):
                all_updated_eans.update(it.get("all_eans", []))
    except Exception:
        pass
        
    # Today batch 2 (Timur 1264)
    try:
        with open("/app/timur_unupdated_audit.json") as f:
            for it in json.load(f):
                for sub in it.get("unupdated_items", []):
                    all_updated_eans.add(str(sub[0]).strip())
    except Exception:
        pass

    print(f"Loaded {len(all_updated_eans)} already updated EANs to SKIP.\n", flush=True)

    # 2. Connect to Aftercool and database
    client = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await client.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    async with SessionLocal() as db:
        res = await db.execute(
            select(Factories).where(func.upper(Factories.account) == "JV", Factories.items_count > 0)
        )
        all_jv_factories = res.scalars().all()

        res_online = await db.execute(
            select(OttoXlsxImportRow.ean)
            .where(OttoXlsxImportRow.account == "jv", OttoXlsxImportRow.marketplace_status == "Online")
        )
        db_online_eans = {str(r[0]).strip() for r in res_online.all() if r[0]}

    print(f"Total non-empty JV factories: {len(all_jv_factories)}")
    print(f"Total Online EANs in local DB: {len(db_online_eans)}\n", flush=True)

    # 3. Setup OTTO client for verifying presence
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
    found_by_factory = defaultdict(list)
    scanned_count = 0

    async def scan_factory(f: Factories):
        nonlocal scanned_count
        async with sem:
            scanned_count += 1
            if scanned_count % 100 == 0:
                print(f"  Scanned {scanned_count} / {len(all_jv_factories)} factories...", flush=True)
            try:
                resp = await client.send_request(
                    "GET",
                    "/api/products",
                    params={"account": "jv", "dataset": "lister", "factory_id": f.factory_id, "limit": 0, "include_row": 1},
                    cookies={"session": session},
                )
                items = resp.json().get("items", [])
                for it in items:
                    row = it.get("row", {})
                    ean = str(row.get("EAN") or row.get("sku") or "").strip()
                    if not ean or ean in all_updated_eans:
                        continue
                        
                    is_tr, reason = extract_tr_origin(row)
                    if is_tr:
                        title = str(row.get("Artikelbeschreibung") or "")[:40]
                        found_by_factory[f.name].append({
                            "factory_id": f.factory_id,
                            "factory_name": f.name,
                            "ean": ean,
                            "reason": reason,
                            "title": title,
                            "is_db_online": ean in db_online_eans
                        })
            except Exception:
                pass

    await asyncio.gather(*[scan_factory(f) for f in all_jv_factories])

    print("\n=== SCAN RESULTS ===", flush=True)
    total_found = sum(len(items) for items in found_by_factory.values())
    print(f"Total candidate Turkish items found (not yet updated): {total_found}")
    print(f"Found across {len(found_by_factory)} factories/folders.\n")

    summary_list = []
    for fname, items in sorted(found_by_factory.items(), key=lambda x: len(x[1]), reverse=True):
        db_online_count = sum(1 for it in items if it["is_db_online"])
        print(f"  * {fname} (ID: {items[0]['factory_id']}): {len(items)} items (in local DB Online: {db_online_count}) | Reason: {items[0]['reason']}")
        summary_list.append({
            "factory_name": fname,
            "factory_id": items[0]["factory_id"],
            "count": len(items),
            "db_online_count": db_online_count,
            "reason": items[0]["reason"],
            "sample_eans": [x["ean"] for x in items[:3]],
            "all_items": items
        })

    with open("/app/remaining_unupdated_tr_all.json", "w", encoding="utf-8") as out:
        json.dump(summary_list, out, ensure_ascii=False, indent=2)

    print("\nSaved full results to /app/remaining_unupdated_tr_all.json", flush=True)
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
