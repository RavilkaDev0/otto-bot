import asyncio
import json
import logging
import os
import sys
import time

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from sqlalchemy import select, func

os.makedirs("/app/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/logs/scan_cn_xl.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("scan_cn_xl")


async def main():
    log.info("================================================================")
    log.info("  SCANNING CHINESE PRODUCTS FOR OTTO XL")
    log.info("================================================================")

    # 1. Load online SKUs from DB
    log.info("Querying online SKUs on OTTO XL from database...")
    async with SessionLocal() as db:
        res = await db.execute(
            select(
                OttoXlsxImportRow.sku,
                OttoXlsxImportRow.ean,
                OttoXlsxImportRow.product_name,
            ).where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        rows = res.all()

    online_skus = set()
    sku_to_title = {}
    for s, e, title in rows:
        if s:
            s_str = str(s).strip()
            online_skus.add(s_str)
            if title:
                sku_to_title[s_str] = title
        if e:
            e_str = str(e).strip()
            online_skus.add(e_str)
            if title and e_str not in sku_to_title:
                sku_to_title[e_str] = title

    log.info("Found %d online SKUs on OTTO XL in database.", len(online_skus))

    # 2. Load Afterbuy Chinese factories
    factories_path = "/app/all_afterbuy_xl_factories.json"
    if not os.path.exists(factories_path):
        log.error("File %s not found!", factories_path)
        return

    with open(factories_path, "r", encoding="utf-8") as f:
        all_factories = json.load(f)

    cn_factories = [
        f
        for f in all_factories
        if "(cn)" in (f.get("name") or "").lower()
        or "china" in (f.get("name") or "").lower()
    ]
    log.info("Identified %d Chinese factories in Afterbuy.", len(cn_factories))

    # 3. Connect to Afterbuy
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=45.0)
    session = await ab.login(
        username=settings.afterbuy_username, password=settings.afterbuy_password
    )
    log.info("Connected to Afterbuy.")

    sem = asyncio.Semaphore(10)
    factories_data = []
    all_matched_skus = set()

    async def process_factory(fact):
        fid = fact.get("id")
        fname = fact.get("name")
        cnt = fact.get("items_count", 0)
        if cnt == 0:
            return

        async with sem:
            for attempt in range(3):
                try:
                    resp = await ab.send_request(
                        "GET",
                        "/api/products",
                        params={
                            "account": "xl",
                            "dataset": "lister",
                            "factory_id": fid,
                            "limit": 0,
                            "include_row": 1,
                        },
                        cookies={"session": session},
                    )
                    items = resp.json().get("items", [])
                    break
                except Exception as exc:
                    if attempt == 2:
                        log.error("Failed to fetch %s (ID %s): %s", fname, fid, exc)
                        return
                    await asyncio.sleep(2)

        matched_items = []
        for it in items:
            row = it.get("row", {})
            s = str(row.get("EAN") or row.get("sku") or "").strip()
            if s and s in online_skus and s not in all_matched_skus:
                all_matched_skus.add(s)
                matched_items.append(
                    {
                        "sku": s,
                        "ean": s,
                        "title": sku_to_title.get(s, row.get("name", "")),
                    }
                )

        if matched_items:
            log.info("  * Factory '%s' (ID %s): %d online items", fname, fid, len(matched_items))
            factories_data.append(
                {
                    "factory_id": fid,
                    "factory_name": fname,
                    "items_count": len(matched_items),
                    "items": matched_items,
                }
            )

    await asyncio.gather(*[process_factory(f) for f in cn_factories])

    # Sort factories by count descending
    factories_data.sort(key=lambda x: x["items_count"], reverse=True)
    total_cn_items = sum(f["items_count"] for f in factories_data)

    log.info("================================================================")
    log.info("  SCAN RESULT: %d online items across %d Chinese factories", total_cn_items, len(factories_data))
    log.info("================================================================")

    # 4. Save cn_products_xl.json
    out_json = {
        "account": "xl",
        "target_weeks": 12,
        "processing_time": 57,
        "shipping_profile": "027a1a46-cf60-56f4-917b-81661569f016",
        "total_items": total_cn_items,
        "total_factories": len(factories_data),
        "factories": factories_data,
    }
    with open("/app/cn_products_xl.json", "w", encoding="utf-8") as f:
        json.dump(out_json, f, indent=2, ensure_ascii=False)
    log.info("Saved JSON output to /app/cn_products_xl.json")

    # 5. Save factories_xl_cn.txt
    lines = [
        f"СПИСОК КИТАЙСКИХ ФАБРИК OTTO XL (12 НЕДЕЛЬ / PROCESSING_TIME: 57)",
        f"Всего фабрик: {len(factories_data)} | Всего онлайн-товаров: {total_cn_items}",
        "=" * 70,
    ]
    for idx, f in enumerate(factories_data, 1):
        lines.append(f"{idx}. {f['factory_name']} — {f['items_count']} товаров (ID: {f['factory_id']})")

    with open("/app/factories_xl_cn.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log.info("Saved text list to /app/factories_xl_cn.txt")


if __name__ == "__main__":
    asyncio.run(main())
