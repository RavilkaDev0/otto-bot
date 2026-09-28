import asyncio
import json
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.schemas.external_schemes.until_schemes import DeliveryInformationRequest, DeliveryInformation

SHIPPING_PROFILE_CN_12W = "027a1a46-cf60-56f4-917b-81661569f016"

async def main():
    skus = ["4071489184198", "4071489184136", "4070863463379", "4062292218217"]
    print(f"Updating 4 default items to 12 weeks...")

    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
    )
    client = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)

    items_req = [
        DeliveryInformation(
            shipping_profile_id=SHIPPING_PROFILE_CN_12W,
            processing_time="57",
            sku=s,
        )
        for s in skus
    ]
    del_req = DeliveryInformationRequest(root=items_req)
    await client.update_product_delivery(del_req, "xl")
    print("Update successful!")

    # Verify
    for s in skus:
        info = await client.get_shipping_profile_for_product(s, "xl")
        print(f"  SKU {s}: PT={info.get('processingTime')}, Profile={info.get('shippingProfileId')}")

if __name__ == "__main__":
    asyncio.run(main())
