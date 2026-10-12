import asyncio
import httpx
from app.core.configs import settings

async def check():
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{settings.afterbuy_base_url}/auth/login",
            json={"username": settings.afterbuy_username, "password": settings.afterbuy_password}
        )
        session = r.cookies.get("session")
        
        for dataset in ["lister", "products"]:
            res = await client.get(
                f"{settings.afterbuy_base_url}/api/products",
                params={"account": "jv", "dataset": dataset, "q": "183970226", "include_row": 1},
                cookies={"session": session}
            )
            data = res.json()
            items = data.get("items", [])
            print(f"Dataset {dataset}: total = {data.get('total')}, items = {len(items)}", flush=True)
            if items:
                row = items[0].get("row", {})
                for k, v in row.items():
                    val_str = str(v).lower()
                    if any(sub in val_str for sub in ["country tr", "city ine", "merli"]):
                        print(f"  [{dataset}] {k}: {str(v)[:150]}", flush=True)

if __name__ == "__main__":
    asyncio.run(check())
