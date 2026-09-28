import asyncio
import json
import logging
import os
import re
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.schemas.external_schemes.until_schemes import GetProductRequest

# Silence httpx logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("app.clients.external_otto_client").setLevel(logging.WARNING)
logging.getLogger("app.core.otto_auth").setLevel(logging.WARNING)
logging.getLogger("external_api").setLevel(logging.WARNING)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("fast_tr_audit")

async def main():
    # Load all afterbuy factories
    with open("/app/all_afterbuy_xl_factories.json") as f:
        all_f = json.load(f)

    # Filter all possible TR factories with items > 0
    tr_factories = []
    for f in all_f:
        name = f.get("name", "").strip()
        cnt = f.get("items_count", 0)
        if cnt == 0:
            continue
        low = name.lower()
        if "others_löschen" in low or "others_loschen" in low:
            continue
        if "_(pl)" in low or "_(it)" in low or "_(cn)" in low or "_(de)" in low or "_(uk)" in low:
            continue
        if "(tr)" in low or "_tr" in low or "timur" in low or "ropez" in low:
            tr_factories.append(f)

    log.info("Total TR factories to inspect: %d", len(tr_factories))

    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
    )
    otto = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)

    sem = asyncio.Semaphore(20)

    results = []

    for fact in tr_factories:
        fid = fact["id"]
        fname = fact["name"]
        resp = await afterbuy.send_request(
            "GET",
            "/api/products",
            params={
                "account": "xl",
                "dataset": "lister",
                "factory_id": fid,
                "limit": 100,
                "offset": 0,
                "include_row": 1,
            },
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        if not items:
            continue

        xl_online_unupdated = []
        xl_online_39 = []
        jv_online_unupdated = []
        jv_online_39 = []

        async def check_item(it):
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if not sku:
                return
            async with sem:
                # Check XL
                try:
                    res_xl = await otto.get_products(GetProductRequest(sku=sku), "xl")
                    vars_xl = res_xl.get("productVariations") or []
                    if vars_xl:
                        dt = (vars_xl[0].get("delivery") or {}).get("deliveryTime")
                        if dt == 39:
                            xl_online_39.append(sku)
                        else:
                            xl_online_unupdated.append((sku, dt))
                except Exception:
                    pass

                # Check JV
                try:
                    res_jv = await otto.get_products(GetProductRequest(sku=sku), "jv")
                    vars_jv = res_jv.get("productVariations") or []
                    if vars_jv:
                        dt = (vars_jv[0].get("delivery") or {}).get("deliveryTime")
                        if dt == 39:
                            jv_online_39.append(sku)
                        else:
                            jv_online_unupdated.append((sku, dt))
                except Exception:
                    pass

        await asyncio.gather(*[check_item(it) for it in items])

        if xl_online_unupdated or jv_online_unupdated:
            log.warning("FOUND UNUPDATED ITEMS in '%s' (ID %d): XL unupdated=%d (already 39: %d), JV unupdated=%d (already 39: %d)",
                        fname, fid, len(xl_online_unupdated), len(xl_online_39), len(jv_online_unupdated), len(jv_online_39))
            results.append({
                "factory": fname,
                "id": fid,
                "xl_unupdated": len(xl_online_unupdated),
                "xl_39": len(xl_online_39),
                "jv_unupdated": len(jv_online_unupdated),
                "jv_39": len(jv_online_39),
                "sample_xl_unupdated": xl_online_unupdated[:3],
                "sample_jv_unupdated": jv_online_unupdated[:3],
            })
        else:
            log.info("'%s': checked %d items. XL (39=%d, unupd=0), JV (39=%d, unupd=0)",
                     fname, len(items), len(xl_online_39), len(jv_online_39))

    with open("/app/audit_all_tr_unupdated_results.json", "w", encoding="utf-8") as out:
        json.dump(results, out, ensure_ascii=False, indent=2)

    log.info("AUDIT COMPLETE! Total factories with unupdated items: %d", len(results))

if __name__ == "__main__":
    asyncio.run(main())
