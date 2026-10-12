import asyncio
from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from app.schemas.external_schemes.until_schemes import DeliveryInformationRequest, DeliveryInformation

async def main():
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret
    )
    client = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)

    skus = [
        "4069943934489",
        "4067282913516",
        "4069943366716",
        "4069943930627",
        "4069424183290"
    ]

    profile_id = "786c6468-3baf-52e0-88b5-13757eb7f873" # (4-8 WOCHEN) JV

    for sku in skus:
        print(f"Updating {sku}...")
        del_req = DeliveryInformationRequest(
            root=[
                DeliveryInformation(
                    shipping_profile_id=profile_id,
                    processing_time="39",
                    sku=sku
                )
            ]
        )
        try:
            resp = await client.update_product_delivery(del_req, "jv")
            print(f"  -> SUCCESS: {resp}")
        except Exception as e:
            print(f"  -> ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(main())
