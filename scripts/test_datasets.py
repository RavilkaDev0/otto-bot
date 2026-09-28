import asyncio
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

async def main():
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=60.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    
    # Check dataset="products" for Timur 8
    fid = 483403
    print("Testing dataset=products...")
    try:
        resp = await ab.send_request(
            "GET",
            "/api/products",
            params={"account": "xl", "dataset": "products", "factory_id": fid, "limit": 2, "include_row": 1},
            cookies={"session": session},
        )
        print("dataset=products status:", resp.status_code)
        data = resp.json()
        print("dataset=products total:", len(data.get("items", [])))
        if data.get("items"):
            print("First item keys:", list(data["items"][0].keys()))
            row = data["items"][0].get("row", {})
            print("Row keys:", sorted(list(row.keys())))
            for k, v in row.items():
                if any(x in k.lower() for x in ["kurz", "short", "desc", "name", "titel"]):
                    print(f"  {k}: {str(v)[:100]}")
    except Exception as e:
        print("dataset=products error:", e)

    # Let's search query for 'Beiges Kinderbett Leder Jugendbett'
    print("\nSearching with q='Beiges Kinderbett Leder Jugendbett'...")
    try:
        resp = await ab.send_request(
            "GET",
            "/api/products",
            params={"account": "xl", "include_row": 1, "limit": 5, "q": "Beiges Kinderbett Leder Jugendbett"},
            cookies={"session": session},
        )
        data = resp.json()
        print("Found items on XL:", len(data.get("items", [])))
        for it in data.get("items", []):
            print("  Item:", it.get("name"), "| Factory:", it.get("factory_id"))
            row = it.get("row", {})
            for k, v in row.items():
                if "country" in str(v).lower() or "kurz" in k.lower():
                    print(f"    {k}: {v}")
    except Exception as e:
        print("search error:", e)

    # Let's search query on JV
    print("\nSearching with q='Beiges Kinderbett Leder Jugendbett' on JV...")
    try:
        resp = await ab.send_request(
            "GET",
            "/api/products",
            params={"account": "jv", "include_row": 1, "limit": 5, "q": "Beiges Kinderbett Leder Jugendbett"},
            cookies={"session": session},
        )
        data = resp.json()
        print("Found items on JV:", len(data.get("items", [])))
        for it in data.get("items", []):
            print("  Item:", it.get("name"), "| Factory:", it.get("factory_id"))
            row = it.get("row", {})
            for k, v in row.items():
                if "country" in str(v).lower() or "kurz" in k.lower():
                    print(f"    {k}: {v}")
    except Exception as e:
        print("search JV error:", e)

if __name__ == "__main__":
    asyncio.run(main())
