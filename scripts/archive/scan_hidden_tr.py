import asyncio
import json
import re
from app.core.configs import settings
from app.database import SessionLocal, engine
from sqlalchemy import select, func
from app.models.factories import Factories
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient

async def scan_all_others():
    with open("/app/remaining_tr_results.json") as f:
        already_found = {item["factory_id"] for item in json.load(f)}
        
    from scripts.tr_factories_list import VALID_TR_FACTORIES
    
    # Load all Online EANs
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.ean)
            .where(OttoXlsxImportRow.account == "jv", OttoXlsxImportRow.marketplace_status == "Online")
        )
        online_eans_set = {str(r[0]).strip() for r in res.all() if r[0]}
        
        f_res = await db.execute(
            select(Factories).where(func.upper(Factories.account) == "JV", Factories.items_count > 0)
        )
        all_factories = f_res.scalars().all()

    # Exclude those already processed
    to_scan = [f for f in all_factories if f.name not in VALID_TR_FACTORIES and f.factory_id not in already_found]
    print(f"Scanning {len(to_scan)} other remaining factories for hidden TR items...", flush=True)

    client = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=30.0)
    session = await client.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    
    found_hidden = []
    
    for f in to_scan:
        # Ignore obvious non-TR countries
        if re.search(r"_\((PL|CN|IT|DE|CZ|SPAIN|LT|RO|FR)\)", f.name, re.I):
            continue
            
        try:
            resp = await client.send_request(
                "GET",
                "/api/products",
                params={"account": "jv", "dataset": "lister", "factory_id": f.factory_id, "include_row": 1, "limit": 50},
                cookies={"session": session}
            )
            items = resp.json().get("items", [])
            tr_online_items = []
            sample_reason = ""
            
            for it in items:
                row = it.get("row", {})
                ean = str(row.get("EAN") or row.get("ean") or row.get("sku") or "").strip()
                if not ean or ean not in online_eans_set:
                    continue
                    
                gallery = row.get("GalleryURL", "")
                picture = row.get("PictureURL", "")
                title = row.get("Artikelbeschreibung", "")
                
                # Check for TR indicators
                is_tr = False
                reason = ""
                if re.search(r"(_TR\b|/TR/|_TR_)", gallery + picture, re.I):
                    is_tr = True
                    reason = "TR in image path"
                elif any(b in (gallery + picture).lower() for b in ["nott", "merli", "favory", "seyran", "evart", "moreno", "woodface", "depotum"]):
                    is_tr = True
                    reason = "Known TR brand in image path"
                elif "_TR" in title:
                    is_tr = True
                    reason = "_TR in title"
                    
                if is_tr:
                    tr_online_items.append(ean)
                    sample_reason = reason
                    
            if tr_online_items:
                print(f"  [+] Hidden TR found in '{f.name}' (ID: {f.factory_id}): {len(tr_online_items)} Online items ({sample_reason})", flush=True)
                found_hidden.append({
                    "factory_id": f.factory_id,
                    "name": f.name,
                    "reason": sample_reason,
                    "online_items": len(tr_online_items),
                    "new_online_items": len(tr_online_items),
                    "sample_eans": tr_online_items[:3]
                })
        except Exception as e:
            pass

    print(f"\nTotal additional hidden factories found: {len(found_hidden)}", flush=True)
    with open("/app/hidden_tr_results.json", "w", encoding="utf-8") as out:
        json.dump(found_hidden, out, ensure_ascii=False, indent=2)

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(scan_all_others())
