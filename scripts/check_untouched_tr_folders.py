import asyncio
import json
import sys
from collections import defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

UNTOUCHED_TR_FOLDER_IDS = [
    (483296, "SOLNE_(TR)"),
    (483301, "ARDCLAS_(TR)"),
    (481777, "DELETE_DONT_TOUCH Lorenzi_(TR)"),
    (483297, "FUGMS_(TR)"),
    (482405, "DELETE_DONT_TOUCH Neromobilya_(TR)"),
    (483000, "Woodine_(TR)"),
    (483311, "SANAT_(TR)"),
    (483300, "EDG_(TR)"),
    (483299, "SLGA_(TR)"),
    (483337, "Rot_Con_(TR)"),
    (483295, "MAGI_(TR)"),
    (483304, "CCHM_(TR)"),
    (483294, "MBVA_(TR)"),
    (483306, "NARISTA_(TR)"),
    (483346, "Varisse_(TR)"),
    (483334, "ALIST_(TR)"),
    (483345, "Bogao_(TR)"),
    (483302, "CHFO_(TR)"),
    (482444, "M-STAR_(TR)"),
    (483349, "MNS_(TR)"),
    (483310, "MUSVEN_(TR)"),
    (483335, "Zeta_(TR)"),
    (483307, "BRNI_(TR)"),
    (483303, "HERA_(TR)"),
    (483309, "BRST_(TR)"),
    (483313, "Chesterfield_(TR)"),
    (482382, "My_May_(TR)"),
    (483319, "woodinefurniture@gmail.com_(TR)"),
    (481726, "Guemuesev_(TR)"),
    (483354, "Lorenzi_Furniture_(TR)"),
    (483392, "NEW_ROPEZ_SPECIAL_3"),
]

async def main():
    print("=== CHECKING UNTOUCHED TR FOLDERS AGAINST OTTO XL ONLINE ===")

    # 1. Load all online EANs & SKUs for XL from DB
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

    print(f"Total Online items on XL: {len(online_db_set)}")

    # 2. Load already updated SKUs across all batches
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

    print(f"Total already updated: {len(updated_skus)}")

    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    found_missed = defaultdict(list)

    for fid, fname in UNTOUCHED_TR_FOLDER_IDS:
        offset = 0
        limit = 250
        folder_items = []
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
            folder_items.extend(batch)
            if len(batch) < limit:
                break
            offset += limit

        for it in folder_items:
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if sku and sku in online_db_set and sku not in updated_skus:
                found_missed[fname].append({
                    "sku": sku,
                    "ean": sku,
                    "title": str(row.get("Artikelbeschreibung") or "")[:60],
                    "factory_name": fname,
                    "factory_id": fid,
                })

        online_unup = len(found_missed[fname])
        print(f"Folder '{fname}' (ID {fid}): {len(folder_items)} items in AB -> {online_unup} ONLINE on XL & NOT updated")

    total_missed = sum(len(v) for v in found_missed.values())
    print("----------------------------------------------------------------")
    print(f"TOTAL MISSED ONLINE ITEMS IN THESE TR FOLDERS: {total_missed}")

if __name__ == "__main__":
    asyncio.run(main())
