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

    # Let's inspect SORTIERUNG (fid 483321 or check all_afterbuy_xl_factories.json)
    with open("/app/all_afterbuy_xl_factories.json") as f:
        all_factories = json.load(f)

    target_folders = ["SORTIERUNG", "UK-COCO", "UK-Milano-Furniture", "UK-OAK-Furniture"]
    f_map = {f["name"]: f["id"] for f in all_factories if f["name"] in target_folders}

    for name, fid in f_map.items():
        resp = await afterbuy.send_request(
            "GET",
            "/api/products",
            params={
                "account": "xl",
                "dataset": "lister",
                "factory_id": fid,
                "limit": 10,
                "include_row": 1,
            },
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        print(f"\n=== Folder '{name}' (ID {fid}) ===")
        for it in items[:5]:
            row = it.get("row", {})
            print(f"  SKU: {row.get('sku') or row.get('EAN')}")
            print(f"  Title: {row.get('Artikelbeschreibung')}")
            print(f"  Hersteller/Supplier: {row.get('Hersteller')} | {row.get('Lieferant')} | {row.get('Freifeld9')}")

if __name__ == "__main__":
    asyncio.run(main())
