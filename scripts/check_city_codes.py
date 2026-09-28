import asyncio
import sys
import re
from collections import Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings
from app.database import SessionLocal
from sqlalchemy import select
from app.models.factories import Factories

async def check_cities():
    client = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=35.0)
    session = await client.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    
    city_counter = Counter()
    country_counter = Counter()
    
    async with SessionLocal() as db:
        res = await db.execute(select(Factories).where(Factories.items_count > 20).limit(80))
        facts = res.scalars().all()
        
    print(f"Sampling {len(facts)} factories across JV and XL for city/country markers...")
    for f in facts:
        try:
            resp = await client.send_request(
                "GET", "/api/products",
                params={"account": f.account.lower(), "dataset": "lister", "factory_id": f.factory_id, "limit": 100, "include_row": 1},
                cookies={"session": session}
            )
            items = resp.json().get("items", [])
            for it in items:
                row = it.get("row", {})
                all_text = " ".join([
                    str(row.get("Description") or ""),
                    str(row.get("TranslatedDescription") or ""),
                    str(row.get("CustomItemSpecifics") or ""),
                    str(row.get("ShortDescription") or ""),
                    str(row.get("Kurzbeschreibung") or ""),
                ])
                for m in re.finditer(r"\bcity\s+([A-Za-z0-9_-]+)", all_text, re.I):
                    city_counter[m.group(1).upper()] += 1
                for m in re.finditer(r"\bcountry\s+([A-Za-z0-9_-]+)", all_text, re.I):
                    country_counter[m.group(1).upper()] += 1
        except Exception:
            pass
            
    print("\n--- Found Country Codes ---")
    for k, v in country_counter.most_common(15):
        print(f"  country {k}: {v}")

    print("\n--- Found City Codes ---")
    for k, v in city_counter.most_common(15):
        print(f"  city {k}: {v}")

if __name__ == "__main__":
    asyncio.run(check_cities())
