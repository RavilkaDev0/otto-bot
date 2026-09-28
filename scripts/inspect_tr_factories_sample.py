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

    for fid, fname in [(481601, "Alikotuk_(TR)"), (481809, "Gabba_(TR)"), (482584, "LUCE GARDEN_(TR)")]:
        resp = await afterbuy.send_request(
            "GET",
            "/api/products",
            params={
                "account": "xl",
                "dataset": "lister",
                "factory_id": fid,
                "limit": 5,
                "offset": 0,
                "include_row": 1,
            },
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        print(f"\n=======================================================")
        print(f"Factory: {fname} (ID {fid}), fetched {len(items)} items")
        for it in items[:2]:
            print("--- ITEM ---")
            print("it keys:", list(it.keys()))
            print("products_id:", it.get("products_id"))
            row = it.get("row", {})
            print("row keys:", list(row.keys())[:15])
            print("row EAN:", row.get("EAN"))
            print("row sku / Artikelnummer:", row.get("Artikelnummer") or row.get("sku") or row.get("Anr"))
            print("row Name:", str(row.get("Artikelbeschreibung") or row.get("Name") or "")[:80])

if __name__ == "__main__":
    asyncio.run(main())
