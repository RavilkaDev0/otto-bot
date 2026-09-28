import asyncio
import sys
import json

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.schemas.external_schemes.until_schemes import GetProductRequest

async def check():
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
    sku = "4071489319071"
    res = await otto.get_products(GetProductRequest(sku=sku), "xl")
    vars_ = res.get("productVariations") or []
    print(f"Variations count for {sku}: {len(vars_)}")
    if vars_:
        v = vars_[0]
        print("SKU:", v.get("sku"))
        print("Delivery:", v.get("delivery"))

if __name__ == "__main__":
    asyncio.run(check())
