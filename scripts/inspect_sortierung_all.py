import asyncio
import json
import re
import sys
from collections import defaultdict, Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

async def main():
    print("=== INSPECTING ALL ITEMS IN 'SORTIERUNG' ===")

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

    # 2. Load already updated
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

    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    offset = 0
    limit = 250
    sortierung_items = []
    while True:
        resp = await afterbuy.send_request(
            "GET",
            "/api/products",
            params={
                "account": "xl",
                "dataset": "lister",
                "factory_id": "482963",
                "limit": limit,
                "offset": offset,
                "include_row": 1,
            },
            cookies={"session": session},
        )
        batch = resp.json().get("items", [])
        if not batch:
            break
        sortierung_items.extend(batch)
        if len(batch) < limit:
            break
        offset += limit

    print(f"Total items in SORTIERUNG: {len(sortierung_items)}")

    # Analyze URL paths and sources
    path_keywords = Counter()
    online_unupdated = []

    for it in sortierung_items:
        row = it.get("row", {})
        sku = str(row.get("EAN") or row.get("sku") or "").strip()
        pic = str(row.get("PictureURL") or row.get("pictureurls") or "")
        title = str(row.get("Artikelbeschreibung") or "")

        # Extract folder/path name from URL: e.g. jvfurniture.eu/Materport%20proekt/TITI/...
        m = re.search(r"jvfurniture\.eu/([^/]+)/([^/]+)", pic)
        subpath = f"{m.group(1)}/{m.group(2)}" if m else "OTHER"
        path_keywords[subpath] += 1

        if sku in online_db_set and sku not in updated_skus:
            online_unupdated.append({
                "sku": sku,
                "title": title,
                "pic": pic[:80],
                "subpath": subpath,
            })

    print(f"Total online & unupdated in SORTIERUNG: {len(online_unupdated)}")
    print("\nURL subpaths breakdown in SORTIERUNG:")
    for p, c in path_keywords.most_common(20):
        print(f"  {p}: {c}")

    # Check which subpaths are Turkish
    tr_in_sortierung = []
    for item in online_unupdated:
        p_low = item["subpath"].lower() + " " + item["pic"].lower()
        if any(tr_kw in p_low for tr_kw in ["titi", "timur", "turk", "inegol", "bursa", "alber", "alite", "redof", "burosit", "favory", "radmila", "serhat"]):
            tr_in_sortierung.append(item)

    print(f"\nSpecifically TURKISH items in SORTIERUNG that are ONLINE on XL: {len(tr_in_sortierung)}")
    for it in tr_in_sortierung[:10]:
        print(f"  * SKU: {it['sku']} | {it['subpath']} | {it['title'][:50]}")

if __name__ == "__main__":
    asyncio.run(main())
