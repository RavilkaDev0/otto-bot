import asyncio
import json
import re
import sys
from collections import defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

# Load all known Turkish keywords and factory names
def build_tr_regex():
    with open("/app/factories_xl.txt") as f:
        lines = f.readlines()
    
    tr_names = []
    for line in lines:
        m = re.match(r"^\d+\.\s+(.+?)\s+—\s+(\d+)", line)
        if m:
            clean = m.group(1).lower()
            clean = re.sub(r"\s*\(tr\).*", "", clean).strip()
            clean = re.sub(r"\s*20\.12\.24.*", "", clean).strip()
            clean = re.sub(r"[_\W]+", ".*", clean).strip()
            if len(clean) >= 3:
                tr_names.append(clean)
    
    # Add other common TR keywords in image paths
    tr_names.extend([
        r"titi", r"timur", r"turk", r"inegol", r"bursa", r"yusuf",
        r"alber", r"alite", r"redof", r"burosit", r"favory", r"radmila",
        r"deer_furniture", r"amazon_garden", r"luce_garden", r"twins_life",
        r"muzaffer", r"sholpan", r"vrl_home", r"narista", r"aracikan",
        r"roll_furniture", r"bohem_shezade", r"mediapax", r"zanotti",
        r"materport.*titi", r"galja_jobs"
    ])
    pattern = "|".join(set(tr_names))
    return re.compile(pattern, re.IGNORECASE)

async def main():
    print("=== DEEP SCAN FOR HIDDEN TURKISH PRODUCTS IN ALL AFTERBUY FOLDERS ===")

    tr_regex = build_tr_regex()

    # 1. Load online DB set
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.ean, OttoXlsxImportRow.sku)
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        online_db_items = res.all()

    online_db_set = set()
    for ean, sku in online_db_items:
        if ean:
            online_db_set.add(str(ean).strip())
        if sku:
            online_db_set.add(str(sku).strip())

    # 2. Load already updated SKUs
    updated_skus = set()
    for path in [
        "/app/tr_products_xl.json",
        "/app/tr_products_xl_missed_batch.json",
        "/app/tr_products_xl_batch3.json",
        "/app/tr_products_xl_batch4.json",
    ]:
        try:
            with open(path) as f:
                d = json.load(f)
                for fact in d.get("factories", []):
                    for it in fact.get("items", []):
                        s = str(it.get("ean") or it.get("sku") or "").strip()
                        if s:
                            updated_skus.add(s)
        except Exception:
            pass

    print(f"Total already updated SKUs: {len(updated_skus)}")

    with open("/app/unupdated_online_scan_all_factories.json") as f:
        folders_with_unupdated = json.load(f)

    print(f"Folders with unupdated online items to inspect: {len(folders_with_unupdated)}")

    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    hidden_tr_items = defaultdict(list)
    total_hidden_found = 0

    for idx, (fname, info) in enumerate(folders_with_unupdated.items(), 1):
        fid = info["factory_id"]
        # Paginated fetch
        offset = 0
        limit = 250
        while True:
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
            for it in batch:
                row = it.get("row", {})
                sku = str(row.get("EAN") or row.get("sku") or "").strip()
                if not sku or sku not in online_db_set or sku in updated_skus:
                    continue

                pic = str(row.get("PictureURL") or row.get("pictureurls") or "")
                title = str(row.get("Artikelbeschreibung") or "")
                text_to_check = f"{fname} {pic} {title}"

                # Match against Turkish regex
                m = tr_regex.search(text_to_check)
                if m:
                    matched_kw = m.group(0)
                    hidden_tr_items[fname].append({
                        "sku": sku,
                        "ean": sku,
                        "title": title[:60],
                        "pic": pic[:90],
                        "folder_name": fname,
                        "matched_kw": matched_kw,
                    })
                    total_hidden_found += 1

            if len(batch) < limit:
                break
            offset += limit

    print("\n================================================================")
    print(f"SCAN FINISHED! Found {total_hidden_found} HIDDEN TURKISH ITEMS across {len(hidden_tr_items)} folders!")
    print("================================================================")

    for fname, items in sorted(hidden_tr_items.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"  * Folder '{fname}': {len(items)} Turkish items found")
        for it in items[:2]:
            print(f"      - [{it['matched_kw']}] SKU {it['sku']} | {it['title']} | {it['pic']}")

    # Save to JSON
    out_data = {
        "account": "xl",
        "total_hidden_tr_items": total_hidden_found,
        "factories": [
            {
                "factory_name": fn,
                "items_count": len(its),
                "items": its,
            }
            for fn, its in hidden_tr_items.items()
        ]
    }
    with open("/app/hidden_tr_items_xl.json", "w", encoding="utf-8") as out:
        json.dump(out_data, out, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
