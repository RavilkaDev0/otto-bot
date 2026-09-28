"""Find remaining Turkish factories and their Online products in JV."""

import asyncio
import json
import re
from collections import defaultdict
from app.core.configs import settings
from app.database import SessionLocal, engine
from sqlalchemy import select, func
from app.models.factories import Factories
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient
from scripts.tr_factories_list import VALID_TR_FACTORIES

KNOWN_TR_BRANDS = [
    "woodface", "ulgen", "sandalya", "koltuk", "mobilya", "depotum", "nott", "favory",
    "seyran", "bermonti", "evart", "vivanza", "wossew", "atolyekoltuk", "merli",
    "saloni", "saltanat", "sehzade", "selenga", "sude", "tarik", "topline", "twins",
    "uygul", "umut", "vitaly", "vivaldi", "woox", "yakamoz", "zanotti", "modis",
    "alite", "alaf", "alper", "afenza", "aliden", "alonex", "alpino", "aneta", "arnisa", "artem"
]

def is_factory_name_turkish(name: str) -> tuple[bool, str]:
    """Check if factory name indicates Turkish origin."""
    name_clean = name.strip()
    
    # Check TR tags
    if re.search(r"(\bTR\b|_TR|_TR_|\.com\.tr|_\(TR\)|_ \(TR\)|\(TR\)|_\(Tr\))", name_clean, re.I):
        # Exclude other countries if explicitly tagged like _(PL), _(CN), etc.
        if re.search(r"_\((PL|CN|IT|DE|CZ|SPAIN|LT|RO|FR)\)", name_clean, re.I):
            return False, ""
        return True, "TR tag in name"
        
    # Check known brands
    name_low = name_clean.lower()
    for brand in KNOWN_TR_BRANDS:
        if brand in name_low:
            return True, f"brand '{brand}' in name"
            
    return False, ""

async def main():
    print("=== Scanning for Remaining Turkish Factories (JV) ===\n", flush=True)
    
    # 1. Load already updated SKUs from yesterday to avoid duplicates
    updated_skus_yesterday = set()
    try:
        with open("/app/jv_run.log", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "OK (Catalog & Availability updated)" in line or "Accepted by OTTO async queue" in line:
                    match = re.search(r"sku=([^\s]+)", line)
                    if match:
                        updated_skus_yesterday.add(match.group(1).strip())
        print(f"Loaded {len(updated_skus_yesterday)} SKUs already updated yesterday.", flush=True)
    except Exception as e:
        print(f"Warning loading jv_run.log: {e}", flush=True)

    # 2. Get all JV factories from DB
    async with SessionLocal() as db:
        res = await db.execute(
            select(Factories)
            .where(func.upper(Factories.account) == "JV", Factories.items_count > 0)
        )
        all_jv_factories = res.scalars().all()
        
    print(f"Total non-empty JV factories in DB: {len(all_jv_factories)}", flush=True)
    
    # Filter out factories that were already in VALID_TR_FACTORIES
    candidate_factories = []
    already_included = []
    
    for f in all_jv_factories:
        if f.name in VALID_TR_FACTORIES:
            already_included.append(f)
        else:
            is_tr, reason = is_factory_name_turkish(f.name)
            if is_tr:
                candidate_factories.append((f, reason))
                
    print(f"Already in previous TR list: {len(already_included)} factories", flush=True)
    print(f"New candidate TR factories by name/brand: {len(candidate_factories)} factories\n", flush=True)
    
    # 3. Connect to Aftercool
    client = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=45.0)
    session = await client.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    print("Connected to Aftercool.\n", flush=True)
    
    # 4. For each candidate factory, fetch its products from Aftercool
    factory_online_products = defaultdict(list)
    
    # Load all Online EANs for JV from DB
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.ean)
            .where(OttoXlsxImportRow.account == "jv", OttoXlsxImportRow.marketplace_status == "Online")
        )
        online_eans_set = {str(r[0]).strip() for r in res.all() if r[0]}
        
    print(f"Total Online EANs in local DB for JV: {len(online_eans_set)}\n", flush=True)
    print("Checking products for candidate factories...", flush=True)
    
    total_new_online_found = 0
    results_summary = []
    
    for f, reason in candidate_factories:
        try:
            resp = await client.send_request(
                "GET",
                "/api/products",
                params={
                    "account": "jv",
                    "dataset": "lister",
                    "factory_id": f.factory_id,
                    "include_row": 1,
                    "limit": 0,
                },
                cookies={"session": session}
            )
            data = resp.json()
            items = data.get("items", [])
            
            # Extract EANs
            eans = []
            for it in items:
                row = it.get("row", {})
                ean = row.get("EAN") or row.get("ean") or row.get("sku")
                if ean:
                    eans.append(str(ean).strip())
                    
            eans = list(set(eans))
            # Intersect with Online EANs
            online_eans = [e for e in eans if e in online_eans_set]
            
            # Filter out already updated
            new_online_eans = [e for e in online_eans if e not in updated_skus_yesterday]
            
            if new_online_eans:
                results_summary.append({
                    "factory_id": f.factory_id,
                    "name": f.name,
                    "reason": reason,
                    "total_afterbuy_items": len(items),
                    "online_items": len(online_eans),
                    "new_online_items": len(new_online_eans),
                    "sample_eans": new_online_eans[:3]
                })
                total_new_online_found += len(new_online_eans)
                print(f"  [+] {f.name} (ID: {f.factory_id}) -> {len(new_online_eans)} new Online items (Reason: {reason})", flush=True)
            elif online_eans:
                print(f"  [.] {f.name} (ID: {f.factory_id}) -> {len(online_eans)} Online items (ALL already updated)", flush=True)
            else:
                pass
                
        except Exception as exc:
            print(f"  [!] Error fetching {f.name} ({f.factory_id}): {exc}", flush=True)
            
    print(f"\n=== SUMMARY ===", flush=True)
    print(f"Found {len(results_summary)} factories with new Online products.", flush=True)
    print(f"Total new Online products to update: {total_new_online_found}\n", flush=True)
    
    # Save results to JSON file for review and execution
    with open("/app/remaining_tr_results.json", "w", encoding="utf-8") as out:
        json.dump(results_summary, out, ensure_ascii=False, indent=2)
    print("Results saved to /app/remaining_tr_results.json", flush=True)

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
