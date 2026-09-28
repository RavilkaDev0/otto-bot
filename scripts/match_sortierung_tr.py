import asyncio
import json
import re
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

async def main():
    # 1. Load known Turkish factory names from factories_xl.txt
    known_tr = set()
    with open("/app/factories_xl.txt") as f:
        for line in f:
            m = re.match(r"^\d+\.\s+(.+?)\s+—\s+(\d+)", line)
            if m:
                clean_name = m.group(1).lower()
                clean_name = re.sub(r"\s*\(tr\).*", "", clean_name).strip()
                clean_name = re.sub(r"\s*20\.12\.24.*", "", clean_name).strip()
                if len(clean_name) >= 3:
                    known_tr.add(clean_name)

    print(f"Loaded {len(known_tr)} base Turkish factory names")

    # 2. Load online DB set
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

    # 3. Load already updated
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

    matched_tr = []
    unmatched = []

    for it in sortierung_items:
        row = it.get("row", {})
        sku = str(row.get("EAN") or row.get("sku") or "").strip()
        if not (sku in online_db_set and sku not in updated_skus):
            continue

        pic = str(row.get("PictureURL") or row.get("pictureurls") or "")
        title = str(row.get("Artikelbeschreibung") or "")
        full_text = (pic + " " + title).lower()

        matched_fn = None
        for fn in known_tr:
            if fn in full_text:
                matched_fn = fn
                break

        if matched_fn:
            matched_tr.append({
                "sku": sku,
                "matched_factory": matched_fn,
                "title": title[:60],
                "pic": pic[:80]
            })
        else:
            unmatched.append({
                "sku": sku,
                "title": title[:60],
                "pic": pic[:80]
            })

    print(f"\nTotal online unupdated in SORTIERUNG: {len(matched_tr) + len(unmatched)}")
    print(f"Matched to Turkish factories: {len(matched_tr)}")
    for it in matched_tr[:20]:
        print(f"  * Matched: '{it['matched_factory']}' | SKU: {it['sku']} | {it['title']}")

    print(f"\nUnmatched: {len(unmatched)}")
    for it in unmatched[:10]:
        print(f"  ? SKU: {it['sku']} | {it['title']} | {it['pic']}")

if __name__ == "__main__":
    asyncio.run(main())
