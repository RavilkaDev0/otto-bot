import asyncio
import json
import re
import sys
from collections import Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

async def main():
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

    # Fetch all 255 online items in SORTIERUNG
    offset = 0
    limit = 250
    items_to_categorize = []
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
        for it in batch:
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if sku and sku in online_db_set and sku not in updated_skus:
                pic = str(row.get("PictureURL") or row.get("pictureurls") or "").lower()
                title = str(row.get("Artikelbeschreibung") or "")
                items_to_categorize.append({"sku": sku, "title": title, "pic": pic})
        if len(batch) < limit:
            break
        offset += limit

    print(f"Total online unupdated items in SORTIERUNG: {len(items_to_categorize)}")

    # Classify each item
    tr_matches = Counter()
    non_tr_matches = Counter()
    unclassified = []

    TR_MAP = {
        "kopru": "Kopru_(TR)",
        "sanat": "Sanatmobilya_(TR)",
        "idollife": "IDOLLIFE_(TR)",
        "pinus": "PINUS_(TR)",
        "amazon_garden": "AMAZON_GARDEN_(TR)",
        "muzaffer": "MUZAFFAR_(TR)",
        "vrl_home": "VRL_HOME_(TR)",
        "onno": "Onno_(TR)",
        "arnisa": "Arnisa_(TR)",
        "twins_life": "Twins_(TR)",
        "gk/klein": "GK_MOBILIA_(TR)",
        "bohem_shezade": "Sehzade_(TR)",
        "sholpan": "VNN_Sholpan_(TR)",
        "aracikan": "ARACIKAN_(TR)",
        "narista": "NARISTA_(TR)",
        "roll_furniture": "Roll_Furniture_(TR)",
        "titi": "TITI_(TR)",
        "legno": "Legno_(TR)",
    }

    NON_TR_MAP = {
        "taranko": "Taranko (Poland)",
        "elt": "Eltap (Poland)",
        "epoxid_cn": "Epoxid (China)",
        "oelbilder": "Oil Paintings (Germany/Art)",
        "varvara.de/galja": "German/Polish Art/Statues",
    }

    categorized_tr = []

    for it in items_to_categorize:
        pic = it["pic"]
        found_tr = None
        for k, v in TR_MAP.items():
            if k in pic:
                found_tr = v
                break
        if found_tr:
            tr_matches[found_tr] += 1
            categorized_tr.append({"sku": it["sku"], "factory": found_tr, "title": it["title"]})
            continue

        found_non = None
        for k, v in NON_TR_MAP.items():
            if k in pic:
                found_non = v
                break
        if found_non:
            non_tr_matches[found_non] += 1
            continue

        unclassified.append(it)

    print("\n--- TURKISH ITEMS IN SORTIERUNG ---")
    for f, c in tr_matches.most_common():
        print(f"  * {f}: {c} items")
    print(f"TOTAL CONFIRMED TURKISH IN SORTIERUNG: {sum(tr_matches.values())}")

    print("\n--- NON-TURKISH ITEMS IN SORTIERUNG ---")
    for f, c in non_tr_matches.most_common():
        print(f"  * {f}: {c} items")
    print(f"TOTAL NON-TURKISH IN SORTIERUNG: {sum(non_tr_matches.values())}")

    print(f"\n--- UNCLASSIFIED IN SORTIERUNG ({len(unclassified)}) ---")
    for it in unclassified[:15]:
        print(f"  ? SKU: {it['sku']} | {it['pic'][:80]}")

if __name__ == "__main__":
    asyncio.run(main())
