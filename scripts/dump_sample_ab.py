import asyncio
import sys
import json

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

async def main():
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=60.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    
    resp = await ab.send_request(
        "GET",
        "/api/products",
        params={"account": "xl", "include_row": 1, "limit": 1, "q": "Beiges Kinderbett Leder Jugendbett"},
        cookies={"session": session},
    )
    items = resp.json().get("items", [])
    if items:
        it = items[0]
        print("Product ID:", it.get("product_id"))
        print("Artikelnummer:", it.get("artikelnummer"))
        print("EAN:", it.get("ean"))
        print("Factory ID:", it.get("factory_id"))
        print("Name:", it.get("name"))
        row = it.get("row", {})
        print("\nAll keys and values in row:")
        for k, v in sorted(row.items()):
            v_str = str(v)
            if len(v_str) > 0:
                print(f"  {k}: {v_str[:150]}")
                
        # Also let's check if there is an Afterbuy XML API or endpoint for product details by ID / artikelnummer
        pid = it.get("product_id")
        artnr = it.get("artikelnummer")
        print(f"\nTrying to fetch product details for pid={pid}, artnr={artnr}...")
        for ep in [f"/api/products/{pid}", f"/api/product/{pid}", f"/api/products/{artnr}", f"/api/lister/{pid}"]:
            try:
                r = await ab.send_request("GET", ep, cookies={"session": session})
                print(f"Endpoint {ep}: status {r.status_code}")
                if r.status_code == 200:
                    print("Data:", str(r.json())[:200])
            except Exception as e:
                print(f"Endpoint {ep}: error {e}")

if __name__ == "__main__":
    asyncio.run(main())
