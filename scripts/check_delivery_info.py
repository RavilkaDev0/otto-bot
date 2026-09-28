import asyncio
import sys
import json
if "/app" not in sys.path:
    sys.path.insert(0, "/app")
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.clients.external_otto_client import ExternalOttoClient

async def check():
    auth = OttoAuth(
        client_id=settings.otto_xl_client_id,
        client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
    )
    otto = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)
    skus = ["4070863646895", "4070863638531"]
    for sku in skus:
        try:
            res = await otto.get_shipping_profile_for_product(sku, "xl")
            print(f"SKU {sku}: {json.dumps(res, indent=2)}")
        except Exception as e:
            print(f"Error for {sku}: {e}")

if __name__ == "__main__":
    asyncio.run(check())
