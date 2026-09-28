import asyncio
import json
import random
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings

async def verify():
    with open("/app/timur_cn_to_update_12w.json") as f:
        data = json.load(f)

    items = data.get("items", [])
    sample = random.sample(items, min(5, len(items)))

    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
    )
    otto = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)

    print("Checking 5 random SKUs from the 308 updated Timur Chinese items:")
    for it in sample:
        sku = it["sku"]
        info = await otto.get_shipping_profile_for_product(sku, "xl")
        print(f"  SKU: {sku} | PT: {info.get('processingTime')} | Profile: {info.get('shippingProfileId')} | Supp: {it['supplier']}")

if __name__ == "__main__":
    asyncio.run(verify())
