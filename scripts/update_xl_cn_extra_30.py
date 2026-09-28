import asyncio
import json
import logging
import os
import sys
import time

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.schemas.external_schemes.until_schemes import DeliveryInformationRequest, DeliveryInformation
from sqlalchemy import select, func
import httpx

SHIPPING_PROFILE_CN_12W = "027a1a46-cf60-56f4-917b-81661569f016"  # LIEFERZEIT (8-12 WOCHEN)
PT_12W = "57"  # 57 + 3 = 60 working days = 12 weeks

os.makedirs("/app/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/logs/update_xl_cn_extra30.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("update_cn_extra30")


def _build_otto_client() -> ExternalOttoClient:
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
    )
    return ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)


async def main():
    log.info("================================================================")
    log.info("  UPDATING 30 EXTRA CHINESE PRODUCTS FROM UK- FOLDERS ON XL")
    log.info("================================================================")

    # 1. Load already updated Chinese SKUs from cn_products_xl.json
    with open("/app/cn_products_xl.json", "r", encoding="utf-8") as f:
        cn_data = json.load(f)

    existing_skus = set()
    for fact in cn_data.get("factories", []):
        for it in fact.get("items", []):
            s = str(it.get("ean") or it.get("sku") or "").strip()
            if s:
                existing_skus.add(s)

    # 2. Get online SKUs from DB
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean, OttoXlsxImportRow.product_name)
            .where(
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

    # 3. Collect from UK- folders in Afterbuy
    with open("/app/all_afterbuy_xl_factories.json", "r", encoding="utf-8") as f:
        all_facts = json.load(f)

    uk_targets = []
    for f in all_facts:
        name = f.get("name", "").strip()
        low = name.lower()
        if low.startswith("uk-"):
            root = low.replace("uk-", "").strip()
            if any(cn in root for cn in ["ganasi", "yqc", "oak", "you-bond"]):
                uk_targets.append(f)

    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=45.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    new_items_by_factory = {}
    items_to_update = []
    seen = set()

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
        fact_items = []
        for it in items:
            row = it.get("row", {})
            s = str(row.get("EAN") or row.get("sku") or "").strip()
            if s and s in online_skus and s not in existing_skus and s not in seen:
                seen.add(s)
                title = sku_to_title.get(s, row.get("name", ""))
                fact_items.append({"sku": s, "ean": s, "title": title})
                items_to_update.append({"sku": s, "factory": fname, "title": title})

        if fact_items:
            new_items_by_factory[fname] = {"factory_id": fid, "items": fact_items}
            log.info("Found %d new items in %s", len(fact_items), fname)

    log.info("Total new Chinese items to update: %d", len(items_to_update))

    if not items_to_update:
        log.warning("No new items found!")
        return

    # 4. Send update to OTTO API
    client = _build_otto_client()
    req = DeliveryInformationRequest(
        root=[
            DeliveryInformation(
                shipping_profile_id=SHIPPING_PROFILE_CN_12W,
                processing_time=PT_12W,
                sku=it["sku"],
            )
            for it in items_to_update
        ]
    )

    log.info("Sending batch update of %d items to OTTO API...", len(items_to_update))
    res = await client.update_product_delivery(req, "xl")
    log.info("OTTO API update result: %s", res)

    # 5. Live verification via OTTO API
    log.info("Verifying updated items live via OTTO API:")
    header = await client._header("xl")
    all_verified = True
    for it in items_to_update:
        s = it["sku"]
        fn = it["factory"]
        url = f"{client.base_url}/v1/availability/product-delivery-information/{s}"
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            r = await http_client.get(url, headers=header)
            d = r.json() if r.status_code == 200 else {}
            pt = d.get("processingTime")
            prof = d.get("shippingProfileId")
            ok = (pt == PT_12W and prof == SHIPPING_PROFILE_CN_12W)
            if not ok:
                all_verified = False
            log.info("  SKU %s (%s): PT=%s | Profile=%s | OK=%s", s, fn, pt, prof[:8] if prof else "None", ok)

    log.info("All 30 items verified: %s", all_verified)

    # 6. Append to cn_products_xl.json
    for fname, data in new_items_by_factory.items():
        cn_data["factories"].append(
            {
                "factory_id": data["factory_id"],
                "factory_name": fname,
                "items_count": len(data["items"]),
                "items": data["items"],
            }
        )

    cn_data["total_items"] = sum(f["items_count"] for f in cn_data["factories"])
    cn_data["total_factories"] = len(cn_data["factories"])

    with open("/app/cn_products_xl.json", "w", encoding="utf-8") as f:
        json.dump(cn_data, f, indent=2, ensure_ascii=False)
    log.info("Updated /app/cn_products_xl.json (Now %d items across %d factories)", cn_data["total_items"], cn_data["total_factories"])

    # 7. Update factories_xl_cn.txt
    lines = [
        f"СПИСОК КИТАЙСКИХ ФАБРИК OTTO XL (12 НЕДЕЛЬ / PROCESSING_TIME: 57)",
        f"Всего фабрик/каталогов: {cn_data['total_factories']} | Всего онлайн-товаров: {cn_data['total_items']}",
        "=" * 70,
    ]
    # sort by count
    sorted_facts = sorted(cn_data["factories"], key=lambda x: x["items_count"], reverse=True)
    for idx, f in enumerate(sorted_facts, 1):
        lines.append(f"{idx}. {f['factory_name']} — {f['items_count']} товаров (ID: {f['factory_id']})")

    with open("/app/factories_xl_cn.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log.info("Updated /app/factories_xl_cn.txt")


if __name__ == "__main__":
    asyncio.run(main())
