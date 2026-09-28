import asyncio
import json
import logging
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

# Load known TR and CN factories
with open("/app/factories_xl_cn.txt") as f:
    cn_text = f.read()

with open("/app/factories_xl.txt") as f:
    tr_text = f.read()

with open("/app/all_afterbuy_xl_factories.json") as f:
    facts = json.load(f)

# Find all factories that are NOT in TR list, NOT in CN list, and NOT in the 10 we just checked
EXCLUDE_IDS = {
    483389, 483390, 483393, 482556, 482465, 482547, 482406, 482582, 482568, 482586,
    482609, 481853
}

candidates = []
for f in facts:
    fid = f.get("id")
    fname = f.get("name", "").strip()
    if fid in EXCLUDE_IDS or fname in cn_text or fname in tr_text:
        continue
    candidates.append(f)

print(f"Remaining unchecked factories on XL: {len(candidates)}")

async def main():
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=60.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    fact_items_count = []
    for f in candidates:
        fid = f.get("id")
        fname = f.get("name")
        resp = await ab.send_request(
            "GET",
            "/api/products",
            params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 1, "include_row": 0},
            cookies={"session": session},
        )
        total = resp.json().get("total_items", 0)
        if total > 0:
            fact_items_count.append((fid, fname, total))

    print(f"Of those, {len(fact_items_count)} factories have > 0 items in Afterbuy:")
    for fid, fname, total in sorted(fact_items_count, key=lambda x: x[2], reverse=True):
        print(f"  ID: {fid} | {fname:<45} : {total:4d} items")

if __name__ == "__main__":
    asyncio.run(main())
