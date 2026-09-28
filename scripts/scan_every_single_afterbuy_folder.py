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

async def main():
    print("=== SCANNING EVERY SINGLE AFTERBUY FOLDER FOR UNUPDATED ONLINE ITEMS ON OTTO XL ===")

    # 1. Load online DB items for XL
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

    # 3. Load all Afterbuy factories
    with open("/app/all_afterbuy_xl_factories.json") as f:
        all_factories = json.load(f)

    # Filter only those with items_count > 0
    active_factories = [f for f in all_factories if f.get("items_count", 0) > 0]
    print(f"Total active Afterbuy factories: {len(active_factories)}")

    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    unupdated_online_by_factory = {}
    known_non_tr_tags = [r"\(pl\)", r"\(it\)", r"\(cn\)", r"\(de\)", r"\(uk\)", r"\(spain\)"]

    for idx, fact in enumerate(active_factories, 1):
        fid = fact["id"]
        fname = fact["name"]
        cnt = fact["items_count"]

        # Fetch products from Afterbuy
        offset = 0
        limit = 250
        unupdated_in_folder = []
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
                if sku and sku in online_db_set and sku not in updated_skus:
                    unupdated_in_folder.append({
                        "sku": sku,
                        "title": str(row.get("Artikelbeschreibung") or "")[:50],
                    })
            if len(batch) < limit:
                break
            offset += limit

        if unupdated_in_folder:
            is_explicit_non_tr = any(re.search(tag, fname.lower()) for tag in known_non_tr_tags)
            is_tr = bool(re.search(r"\(tr\)", fname.lower()) or "timur" in fname.lower())
            unupdated_online_by_factory[fname] = {
                "factory_id": fid,
                "total_in_ab": cnt,
                "unupdated_online_count": len(unupdated_in_folder),
                "is_tr": is_tr,
                "is_explicit_non_tr": is_explicit_non_tr,
                "sample_items": unupdated_in_folder[:3],
            }

    print("\n================================================================")
    print(f"SCAN COMPLETED. Found {len(unupdated_online_by_factory)} factories with unupdated Online items on XL.")
    print("================================================================")

    # Save full report
    with open("/app/unupdated_online_scan_all_factories.json", "w", encoding="utf-8") as out:
        json.dump(unupdated_online_by_factory, out, ensure_ascii=False, indent=2)

    # Group by category
    tr_missed = []
    suspicious_unmarked = []
    explicit_non_tr = []

    for fname, data in unupdated_online_by_factory.items():
        if data["is_tr"]:
            tr_missed.append((fname, data))
        elif data["is_explicit_non_tr"]:
            explicit_non_tr.append((fname, data))
        else:
            suspicious_unmarked.append((fname, data))

    print(f"\n1. Explicitly TURKISH folders with unupdated items ({len(tr_missed)}):")
    for fn, d in sorted(tr_missed, key=lambda x: x[1]["unupdated_online_count"], reverse=True):
        print(f"   * {fn}: {d['unupdated_online_count']} unupdated online items")

    print(f"\n2. UNMARKED / SUSPICIOUS folders (neither (TR) nor explicit (PL)/(IT)/(CN)/(DE)) ({len(suspicious_unmarked)}):")
    for fn, d in sorted(suspicious_unmarked, key=lambda x: x[1]["unupdated_online_count"], reverse=True):
        samples = [it['title'] for it in d['sample_items']]
        print(f"   * {fn} ({d['unupdated_online_count']} items) - Samples: {samples}")

    print(f"\n3. Explicit Non-TR folders ((PL), (IT), (CN), (DE)) count: {len(explicit_non_tr)}, total items: {sum(d['unupdated_online_count'] for _, d in explicit_non_tr)}")

if __name__ == "__main__":
    asyncio.run(main())
