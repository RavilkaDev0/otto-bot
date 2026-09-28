import asyncio
import json
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.schemas.external_schemes.until_schemes import GetProductRequest

async def main():
    with open("/app/missed_online_tr_xl.json") as f:
        data = json.load(f)

    items = data.get("items", [])
    print(f"Total missed items loaded: {len(items)}")

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

    sample = items[:12]
    print("\n=== VERIFYING SAMPLE OF 12 MISSED ITEMS DIRECTLY ON LIVE OTTO XL ===")
    for idx, it in enumerate(sample, 1):
        sku = it["sku"]
        fname = it["factory_name"]
        await asyncio.sleep(0.3)
        try:
            res = await otto.get_products(GetProductRequest(sku=sku), "xl")
            vars_ = res.get("productVariations") or []
            if vars_:
                v = vars_[0]
                dt = (v.get("delivery") or {}).get("deliveryTime")
                profile = (v.get("delivery") or {}).get("profileId")
                status = (v.get("variationControl") or {}).get("processStatus")
                print(f"[{idx}] SKU: {sku} ({fname}) -> status: {status}, deliveryTime: {dt}, profile: {profile}")
            else:
                print(f"[{idx}] SKU: {sku} ({fname}) -> No variations returned")
        except Exception as e:
            print(f"[{idx}] SKU: {sku} ({fname}) -> Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
