import asyncio
import json
import re
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

async def main():
    afterbuy = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await afterbuy.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    folders_to_check = [
        ("UK-COCO", "482439"),
        ("UK-Ben-Company", "482506"),
        ("UK-SCORZANYSWIAT", "482442"),
        ("UK-Eltap", "482571"),
        ("UK-Comad", "482458"),
        ("NEW_GANASI_SPECIAL", "483389"),
    ]

    for fname, fid in folders_to_check:
        resp = await afterbuy.send_request(
            "GET",
            "/api/products",
            params={
                "account": "xl",
                "dataset": "lister",
                "factory_id": fid,
                "limit": 5,
                "include_row": 1,
            },
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        pics = [it.get("row", {}).get("PictureURL", "") for it in items[:3]]
        print(f"Folder '{fname}':")
        for p in pics:
            print(f"  {p}")

if __name__ == "__main__":
    asyncio.run(main())
