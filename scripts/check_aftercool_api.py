import asyncio
import sys
import json

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

async def main():
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=30.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    
    # Try openapi.json
    try:
        resp = await ab.send_request("GET", "/openapi.json", cookies={"session": session})
        print("openapi.json status:", resp.status_code)
        if resp.status_code == 200:
            schema = resp.json()
            print("Paths in openapi.json:")
            for path, methods in schema.get("paths", {}).items():
                print(f"  {list(methods.keys())} {path}")
    except Exception as e:
        print("openapi error:", e)

if __name__ == "__main__":
    asyncio.run(main())
