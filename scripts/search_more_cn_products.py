import asyncio
import json
import logging
import os
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from sqlalchemy import select, func

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("search_cn")

# Known Chinese keywords, cities, furniture hubs, brand names
CN_KEYWORDS = [
    "china", "chinesisch", "chinesische", "prc", "p.r.c",
    "foshan", "shunde", "lecong", "longjiang", "dongguan", "shenzhen", "guangzhou",
    "zhongshan", "huizhou", "anji", "huzhou", "hangzhou", "ningbo", "shaoxing",
    "shengfang", "bazhou", "langfang", "nankang", "ganzhou", "chengdu", "qingdao",
    "xiamen", "yiwu", "ganasi", "you-bond", "you-me", "yqc", "weitao", "pengpai",
    "evergo", "mefan", "romeigo", "weeding", "sunray", "zibeauty"
]

async def main():
    log.info("Starting comprehensive search for any remaining Chinese products...")

    # 1. Load TR and CN factory lists
    tr_names = set()
    with open("/app/factories_xl.txt", "r", encoding="utf-8") as f:
        for line in f:
            if line.strip() and line.strip()[0].isdigit() and " — " in line:
                tr_names.add(line.split(" — ")[0].split(". ", 1)[-1].strip().lower())

    cn_names = set()
    with open("/app/factories_xl_cn.txt", "r", encoding="utf-8") as f:
        for line in f:
            if line.strip() and line.strip()[0].isdigit() and " — " in line:
                cn_names.add(line.split(" — ")[0].split(". ", 1)[-1].strip().lower())

    with open("/app/cn_products_xl.json", "r", encoding="utf-8") as f:
        cn_json = json.load(f)

    cn_skus = set()
    for f in cn_json["factories"]:
        for it in f["items"]:
            s = str(it.get("ean") or it.get("sku") or "").strip()
            if s:
                cn_skus.add(s)

    log.info("Currently recorded: %d CN SKUs across %d CN factories/catalogs", len(cn_skus), len(cn_names))

    # 2. Load all Afterbuy factories
    with open("/app/all_afterbuy_xl_factories.json", "r", encoding="utf-8") as f:
        all_facts = json.load(f)

    log.info("Total Afterbuy factories: %d", len(all_facts))

    # 3. Check all factories that are NOT already in CN list and NOT in TR list
    candidate_factories = []
    for f in all_facts:
        fname = f.get("name", "").strip()
        flow = fname.lower()
        if flow in cn_names or flow in tr_names:
            continue
        
        # Check if name contains any CN indication
        matched_kw = [kw for kw in CN_KEYWORDS if kw in flow]
        if matched_kw or "_(cn)" in flow or "china" in flow or "chinese" in flow:
            candidate_factories.append((f, matched_kw))

    log.info("Found %d candidate factories matching CN keywords by name:", len(candidate_factories))
    for f, kws in candidate_factories:
        log.info("  -> ID %s: %s (matched: %s)", f.get("id"), f.get("name"), kws)

    # 4. Check all online items in DB for XL
    async with SessionLocal() as db:
        res = await db.execute(
            select(
                OttoXlsxImportRow.sku,
                OttoXlsxImportRow.ean,
                OttoXlsxImportRow.product_name,
                OttoXlsxImportRow.brand,
            ).where(
                func.lower(OttoXlsxImportRow.account) == "xl",
                func.lower(OttoXlsxImportRow.marketplace_status) == "online",
            )
        )
        online_rows = res.all()

    log.info("Total online items on XL in DB: %d", len(online_rows))

    online_skus = set()
    for s, e, _, _ in online_rows:
        if s: online_skus.add(str(s).strip())
        if e: online_skus.add(str(e).strip())

    # Check candidate factories in Afterbuy
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=45.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    log.info("Querying Afterbuy for candidate factories...")
    factory_results = []
    for f, kws in candidate_factories:
        fid = f.get("id")
        fname = f.get("name")
        resp = await ab.send_request(
            "GET",
            "/api/products",
            params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        
        online_in_fact = []
        for it in items:
            row = it.get("row", {})
            s = str(row.get("EAN") or row.get("sku") or "").strip()
            if s and s in online_skus and s not in cn_skus:
                online_in_fact.append({"sku": s, "title": row.get("name", "")})

        log.info("Factory %s (ID %s): %d total items in AB, %d NEW ONLINE on XL", fname, fid, len(items), len(online_in_fact))
        if online_in_fact:
            factory_results.append({
                "factory_id": fid,
                "factory_name": fname,
                "matched_kws": kws,
                "items": online_in_fact
            })

    # Check online rows against CN keywords in title/brand
    matched_online_items = []
    for sku, ean, title, brand in online_rows:
        s_str = str(sku or "").strip()
        e_str = str(ean or "").strip()
        
        # skip if already in CN list
        if s_str in cn_skus or e_str in cn_skus:
            continue

        text_to_check = f"{title or ''} {brand or ''}".lower()
        matched_kw = [kw for kw in CN_KEYWORDS if kw in text_to_check]
        if matched_kw:
            matched_online_items.append({
                "sku": s_str,
                "ean": e_str,
                "title": title,
                "brand": brand,
                "matched": matched_kw
            })

    log.info("Found %d online items matching CN keywords in title/brand outside current CN catalog!", len(matched_online_items))
    for it in matched_online_items[:20]:
        log.info("  SKU: %s | Brand: %s | Matched: %s | Title: %s", it["sku"], it["brand"], it["matched"], it["title"][:60])

    # Save findings
    with open("/app/extra_cn_candidates.json", "w", encoding="utf-8") as out:
        json.dump({
            "candidate_factories": candidate_factories,
            "matched_online_items": matched_online_items
        }, out, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    asyncio.run(main())
