import asyncio
import os
import re
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

async def check():
    client = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await client.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    res = await client.send_request("GET", "/api/factories", params={"account": "jv"}, cookies={"session": session})
    data = res.json()
    if isinstance(data, dict):
        data = data.get("items", []) or data.get("factories", [])
        
    person_factories = [f for f in data if "person" in f.get("name", "").lower() or "others" in f.get("name", "").lower()]
    print(f"Total Person/Others folders in Aftercool: {len(person_factories)}")
    
    for f in person_factories:
        fid = f.get("id")
        fname = f.get("name")
        resp = await client.send_request("GET", "/api/products", params={"account": "jv", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1}, cookies={"session": session})
        items = resp.json().get("items", [])
        tr_cnt = 0
        sample_imgs = []
        for it in items:
            row = it.get("row", {})
            imgs = f"{row.get('GalleryURL', '')} {row.get('PictureURL', '')}"
            if re.search(r"(_tr\b|/tr/|_tr_|\(tr\))", imgs, re.I) or any(b in imgs.lower() for b in ["evart", "merli", "nott", "woodface", "depotum"]):
                tr_cnt += 1
                if len(sample_imgs) < 2:
                    sample_imgs.append(imgs[:60])
        if tr_cnt > 0:
            print(f"  [+] {fname} (ID: {fid}) -> {tr_cnt} TR items detected! Samples: {sample_imgs}")

if __name__ == "__main__":
    asyncio.run(check())
