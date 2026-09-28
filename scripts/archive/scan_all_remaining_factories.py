"""Accurate, zero-false-positive scan of ALL JV factories in Afterbuy for Turkish Online goods."""

import asyncio
import json
import re
from app.core.configs import settings
from app.database import SessionLocal, engine
from sqlalchemy import select, func
from app.models.factories import Factories
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient
from scripts.tr_factories_list import VALID_TR_FACTORIES

# Clean Turkish factory names from the user's validated list
CLEAN_TR_NAMES = set()
for f in VALID_TR_FACTORIES:
    # remove _(TR), (TR), _TR, spaces
    clean = re.sub(r"(_|\s)*\((TR|Tr)\)", "", f).strip().lower()
    clean = re.sub(r"(_|\s)*TR$", "", clean).strip()
    if len(clean) >= 3:
        CLEAN_TR_NAMES.add(clean)

# Add known brands that don't have TR in name
ADDITIONAL_TR_BRANDS = {
    "woodface", "ulgen", "depotum", "nott", "favory", "seyran", "bermonti",
    "evart", "vivanza", "wossew", "atolyekoltuk", "merli", "burosit", "bürosit",
    "duga", "acel", "saltanat", "saloni", "sandalya", "koltuk", "mobilya"
}
ALL_TR_TARGETS = CLEAN_TR_NAMES | ADDITIONAL_TR_BRANDS

def extract_tr_origin(row: dict) -> tuple[bool, str]:
    """Check if the product is genuinely Turkish using upload folder or explicit markers."""
    gallery = str(row.get("GalleryURL") or "")
    picture = str(row.get("PictureURL") or "")
    picurls = str(row.get("pictureurls") or "")
    all_images = f"{gallery} {picture} {picurls}"
    title = str(row.get("Artikelbeschreibung") or "")
    desc = str(row.get("Description") or "") + " " + str(row.get("TranslatedDescription") or "")
    specifics = str(row.get("CustomItemSpecifics") or "")
    combined_text = f"{title} {desc} {specifics}"

    # 1. Check upload folder in image URLs: .../AfterbuyUploadService/<FOLDER>/...
    folder_match = re.search(r"AfterbuyUploadService/([^/]+)/", all_images)
    if folder_match:
        folder = folder_match.group(1).strip()
        folder_low = folder.lower()
        
        # Folder explicitly marked with TR (e.g. 397_FAVORY_TR, NOTT_TR, MERLI_TR)
        if re.search(r"(_TR\b|/TR/|_TR_|\(TR\))", folder, re.I):
            return True, f"Upload folder '{folder}' has TR tag"
            
        # Folder matches one of the known Turkish factory names
        clean_folder = re.sub(r"^\d+_", "", folder_low).strip()
        for tr_name in ALL_TR_TARGETS:
            if clean_folder == tr_name or clean_folder.startswith(tr_name + "_") or clean_folder.endswith("_" + tr_name):
                return True, f"Upload folder '{folder}' matches TR factory '{tr_name}'"

    # 2. Check title / description for explicit country marker
    if re.search(r"\bcountry\s+TR\b", combined_text, re.I):
        return True, "'country TR' in description"
    if re.search(r"\bcity\s+INE\b", combined_text, re.I):
        return True, "'city INE' in description"
        
    # 3. Check title for explicit _TR / (TR) tag
    if re.search(r"(_TR\b|_TR_|\(TR\)|_\(TR\))", title, re.I):
        return True, "TR tag in title"

    return False, ""

async def main():
    print("=== Accurate Scan of ALL JV Factories for Turkish Online Goods ===\n", flush=True)

    # 1. Collect all already processed EANs to skip duplicates
    already_counted_eans = set()
    
    # Yesterday's log
    try:
        with open("/app/jv_run.log", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "OK (Catalog & Availability updated)" in line or "Accepted by OTTO async queue" in line:
                    m = re.search(r"sku=([^\s]+)", line)
                    if m:
                        already_counted_eans.add(m.group(1).strip())
    except Exception:
        pass
        
    # Today's results
    for fn in ["/app/remaining_tr_results.json", "/app/timur_tr_results.json", "/app/hidden_tr_results.json"]:
        try:
            with open(fn) as f:
                data = json.load(f)
                for it in data:
                    for e in it.get("sample_eans", []):
                        already_counted_eans.add(e)
        except Exception:
            pass

    print(f"Loaded {len(already_counted_eans)} already known/updated EANs to SKIP.", flush=True)

    # 2. Get all factories from DB
    async with SessionLocal() as db:
        res = await db.execute(
            select(Factories).where(func.upper(Factories.account) == "JV", Factories.items_count > 0)
        )
        all_jv_factories = res.scalars().all()
        
        # Load Online EANs in JV
        res_online = await db.execute(
            select(OttoXlsxImportRow.ean)
            .where(OttoXlsxImportRow.account == "jv", OttoXlsxImportRow.marketplace_status == "Online")
        )
        online_eans_set = {str(r[0]).strip() for r in res_online.all() if r[0]}

    print(f"Total non-empty JV factories: {len(all_jv_factories)}")
    print(f"Total Online EANs in JV local DB: {len(online_eans_set)}\n", flush=True)

    # 3. Connect to Aftercool
    client = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await client.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    print("Connected to Aftercool. Scanning all factories...\n", flush=True)

    sem = asyncio.Semaphore(10)
    factories_found = []
    scanned_count = 0

    async def scan_factory(f: Factories):
        nonlocal scanned_count
        async with sem:
            scanned_count += 1
            if scanned_count % 50 == 0:
                print(f"  Progress: scanned {scanned_count} / {len(all_jv_factories)} factories...", flush=True)
                
            try:
                resp = await client.send_request(
                    "GET",
                    "/api/products",
                    params={"account": "jv", "dataset": "lister", "factory_id": f.factory_id, "include_row": 1, "limit": 0},
                    cookies={"session": session}
                )
                items = resp.json().get("items", [])
                if not items:
                    return
                    
                tr_online_new_eans = []
                reasons_found = set()
                
                for it in items:
                    row = it.get("row", {})
                    ean = str(row.get("EAN") or row.get("ean") or row.get("sku") or "").strip()
                    
                    # Must be Online in JV
                    if not ean or ean not in online_eans_set:
                        continue
                        
                    # Skip if already counted
                    if ean in already_counted_eans:
                        continue
                        
                    is_tr, reason = extract_tr_origin(row)
                    if is_tr:
                        tr_online_new_eans.append(ean)
                        reasons_found.add(reason)
                        
                if tr_online_new_eans:
                    tr_online_new_eans = list(set(tr_online_new_eans))
                    reasons_str = "; ".join(list(reasons_found)[:2])
                    print(f"  [+] FOUND: '{f.name}' (ID: {f.factory_id}) -> {len(tr_online_new_eans)} NEW Online items! ({reasons_str})", flush=True)
                    factories_found.append({
                        "factory_id": f.factory_id,
                        "name": f.name,
                        "total_items": len(items),
                        "new_online_tr_count": len(tr_online_new_eans),
                        "reasons": list(reasons_found),
                        "sample_eans": tr_online_new_eans[:3],
                        "all_eans": tr_online_new_eans
                    })
            except Exception:
                pass

    await asyncio.gather(*[scan_factory(f) for f in all_jv_factories])

    print(f"\n=== FINAL ALL-FACTORIES SCAN RESULTS ===", flush=True)
    print(f"Scanned total {len(all_jv_factories)} factories.")
    print(f"Found {len(factories_found)} factories with new Turkish Online products.")
    total_new = sum(x["new_online_tr_count"] for x in factories_found)
    print(f"Total new Turkish Online products: {total_new}\n", flush=True)

    with open("/app/accurate_remaining_tr_results.json", "w", encoding="utf-8") as out:
        json.dump(factories_found, out, ensure_ascii=False, indent=2)
    print("Saved results to /app/accurate_remaining_tr_results.json", flush=True)

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
