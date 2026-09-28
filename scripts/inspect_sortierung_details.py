import asyncio
import json
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

async def main():
    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    resp = await afterbuy.send_request(
        "GET",
        "/api/products",
        params={
            "account": "xl",
            "dataset": "lister",
            "factory_id": "482963",
            "limit": 5,
            "include_row": 1,
        },
        cookies={"session": session},
    )
    items = resp.json().get("items", [])
    for it in items[:3]:
        row = it.get("row", {})
        non_empty = {k: v for k, v in row.items() if v}
        print("--- ITEM ---")
        for k, v in sorted(non_empty.items()):
            if len(str(v)) < 100:
                print(f"  {k}: {v}")
            else:
                print(f"  {k}: {str(v)[:100]}...")

if __name__ == "__main__":
    asyncio.run(main())
