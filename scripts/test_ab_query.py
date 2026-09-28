import asyncio
import json
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

TEST_SKUS = [
    "4069424244236",
    "4070863441360",
    "4070863796439",
    "4070863307048",
    "4069943472806",
]

async def main():
    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    for sku in TEST_SKUS:
        row = await afterbuy.get_lister_row_by_query(session=session, account="xl", query=sku)
        if row:
            print(f"SKU {sku}: FOUND in AB! kollektion/factory_id: {row.get('kollektion')}, Title: {row.get('Artikelbeschreibung')}")
            print(f"  PictureURL: {row.get('PictureURL')}")
        else:
            print(f"SKU {sku}: Not found in AB")

if __name__ == "__main__":
    asyncio.run(main())
