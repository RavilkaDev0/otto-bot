"""Test candidate Turkish factories directly on OTTO XL API."""

import asyncio
import json
import logging
import sys
import time

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.schemas.external_schemes.until_schemes import GetProductRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("test_tr")

FACTORIES_TO_TEST = [
    {"name": "Bussy_(TR)", "id": 481642},
    {"name": "Redof_(TR)", "id": 482404},
    {"name": "Alber_mobilya_(TR)", "id": 482756},
    {"name": "Alite_(TR)", "id": 481602},
    {"name": "Nills_(TR)", "id": 481907},
    {"name": "TITI_(TR)", "id": 481981},
    {"name": "Bürosit_(TR)", "id": 482511},
    {"name": "NEW_ROPEZ_SPECIAL", "id": 483385},
    {"name": "NEW_Person_4_Xenia", "id": 483387},
    {"name": "NEW_SERHATHOME_SPECIAL_AINUR", "id": 483386},
    {"name": "NEW_FAVORY_SPECIAL", "id": 483388},
    {"name": "NEW_Radmila", "id": 483390},
]

async def run():
    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=30.0)
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

    total_online = 0
    results = {}

    for f in FACTORIES_TO_TEST:
        fname = f["name"]
        fid = f["id"]
        try:
            resp = await afterbuy.send_request(
                "GET",
                "/api/products",
                params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 20, "include_row": 1},
                cookies={"session": session},
            )
            items = resp.json().get("items", [])
            if not items:
                log.info("Factory '%s' (ID %s): 0 items returned from Afterbuy", fname, fid)
                results[fname] = 0
                continue

            online_in_f = 0
            for it in items[:15]:
                sku = str(it.get("row", {}).get("EAN") or it.get("row", {}).get("sku") or "").strip()
                if not sku:
                    continue
                try:
                    res = await otto.get_products(GetProductRequest(sku=sku), "xl")
                    vars_ = res.get("productVariations") or []
                    if vars_:
                        online_in_f += 1
                        dt = (vars_[0].get("delivery") or {}).get("deliveryTime")
                        log.info("  [ONLINE] %s | SKU %s | DT: %s", fname, sku, dt)
                except Exception:
                    pass

            results[fname] = online_in_f
            total_online += online_in_f
            log.info("Factory '%s' (checked 15): %d ONLINE on OTTO XL", fname, online_in_f)
        except Exception as e:
            log.warning("Error fetching %s: %s", fname, e)

    log.info("==================================================")
    log.info("FINAL RESULT: Total Online in test factories: %d", total_online)
    for fn, cnt in results.items():
        log.info("  * %s: %d online", fn, cnt)

if __name__ == "__main__":
    asyncio.run(run())
