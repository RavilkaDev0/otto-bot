import asyncio
import json
import logging
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from sqlalchemy import select, func

async def main():
    # 1. Load updated CN SKUs
    with open("/app/cn_products_xl.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    updated_cn_skus = set()
    for fact in data.get("factories", []):
        for it in fact.get("items", []):
            s = str(it.get("ean") or it.get("sku") or "").strip()
            if s:
                updated_cn_skus.add(s)
    print(f"Already updated CN SKUs: {len(updated_cn_skus)}")

    # 2. Load DB online SKUs
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean)
            .where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        rows = res.all()
    online_skus = set()
    for s, e in rows:
        if s:
            online_skus.add(str(s).strip())
        if e:
            online_skus.add(str(e).strip())
    print(f"Total online SKUs on XL: {len(online_skus)}")

    # 3. Target UK- folders
    with open("/app/all_afterbuy_xl_factories.json", "r", encoding="utf-8") as f:
        facts = json.load(f)

    uk_targets = []
    for f in facts:
        name = f.get("name", "").strip()
        low = name.lower()
        if low.startswith("uk-"):
            root = low.replace("uk-", "").strip()
            if any(cn in root for cn in ["ganasi", "yqc", "coco", "mefan", "dmb", "oak", "you-bond", "youbond", "jxr", "weeding", "defaico"]):
                uk_targets.append(f)

    print(f"Scanning {len(uk_targets)} UK- candidate folders...")
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=45.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    new_items_found = []
    already_count = 0

    for f in uk_targets:
        fid = f.get("id")
        fname = f.get("name")
        resp = await ab.send_request(
            "GET",
            "/api/products",
            params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        matched = 0
        new_matched = 0
        for it in items:
            row = it.get("row", {})
            s = str(row.get("EAN") or row.get("sku") or "").strip()
            if s and s in online_skus:
                matched += 1
                if s in updated_cn_skus:
                    already_count += 1
                else:
                    new_matched += 1
                    new_items_found.append((s, fname, row.get("name", "")))
        print(f"  * {fname} (ID {fid}): {len(items)} items | {matched} online in DB ({new_matched} NEW, {matched - new_matched} already in CN batch)")

    print(f"\nTOTAL in UK- folders: Already in CN batch: {already_count} | NEW online items: {len(new_items_found)}")
    if new_items_found:
        print("Sample new items:")
        for s, fn, nm in new_items_found[:10]:
            print(f"    - SKU {s} | {fn} | {nm[:50]}")

if __name__ == "__main__":
    asyncio.run(main())
