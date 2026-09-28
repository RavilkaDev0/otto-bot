import asyncio
import json
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.schemas.external_schemes.until_schemes import GetProductRequest

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
    with open("/app/hidden_tr_found_unmarked.json") as f:
        data = json.load(f)

    items = data["items"]
    print(f"Total hidden TR items in file: {len(items)}")

    client = _build_otto_client()

    print("\nChecking sample items on OTTO XL live:")
    for it in items[:6]:
        sku = it["sku"]
        tr_brand = it["tr_brand"]
        fn = it["folder_name"]
        try:
            resp = await client.get_products(GetProductRequest(sku=sku), "xl")
            vars = resp.get("productVariations") or []
            if vars:
                d_time = (vars[0].get("delivery") or {}).get("deliveryTime")
                print(f"  SKU {sku} ({tr_brand} in '{fn}'): deliveryTime on OTTO = {d_time}")
            else:
                print(f"  SKU {sku} ({tr_brand}): Not found on OTTO")
        except Exception as e:
            print(f"  SKU {sku} ({tr_brand}): Error checking OTTO: {e}")

if __name__ == "__main__":
    asyncio.run(main())
