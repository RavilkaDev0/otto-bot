import asyncio
import json
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.schemas.external_schemes.until_schemes import GetProductRequest

async def main():
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
    )
    client = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)

    targets = [
        ("xl", "4071489790283"),
        ("jv", "4071489790269"),
    ]

    for ctrl, sku in targets:
        print(f"\n{'='*20} {ctrl.upper()}: {sku} {'='*20}")
        # 1. Catalog product info
        try:
            p = await client.get_products(GetProductRequest(sku=sku), ctrl)
            variations = p.get("productVariations", [])
            print(f"Product variations count: {len(variations)}")
            if variations:
                v = variations[0]
                desc = v.get("productDescription", {})
                print(f"  Category: {desc.get('category')}")
                print(f"  ProductLine: {desc.get('productLine')}")
                print(f"  BrandId: {desc.get('brandId')}")
                print(f"  Delivery (Catalog): {v.get('delivery')}")
                print(f"  Pricing: {v.get('pricing')}")
                print(f"  Attributes count: {len(desc.get('attributes', []))}")
                for attr in desc.get('attributes', []):
                    print(f"    - {attr.get('name')}: {attr.get('values')}")
            else:
                print(f"  --> NOT FOUND in {ctrl.upper()} catalog by sku={sku}")
        except Exception as e:
            print(f"  Catalog lookup error: {e}")

        # 2. Shipping Profile
        try:
            sp = await client.get_shipping_profile_for_product(sku, ctrl)
            print(f"  Shipping Profile info: {sp}")
        except Exception as e:
            print(f"  Shipping Profile lookup error: {e}")

        # 3. Quantity
        try:
            q = await client.get_quantity(sku, ctrl)
            print(f"  Quantity info: {q}")
        except Exception as e:
            print(f"  Quantity lookup error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
