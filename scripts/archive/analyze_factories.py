import asyncio
import re
import sys
from app.database import SessionLocal
from sqlalchemy import select
from app.models.factories import Factories

async def analyze():
    async with SessionLocal() as db:
        res = await db.execute(select(Factories))
        factories = res.scalars().all()
        
        country_patterns = [
            r"_\(TR\)", r"_\(PL\)", r"_\(CN\)", r"_\(IT\)", r"_\(DE\)", r"_\(CZ\)", r"_\(SPAIN\)", r"_\(LT\)", r"_\(RO\)", r"_\(FR\)",
            r"_TR\b", r"_CN\b", r"_PL\b", r"_IT\b"
        ]
        
        with_country = []
        without_country = []
        
        for f in factories:
            has_c = any(re.search(pat, f.name, re.IGNORECASE) for pat in country_patterns)
            if has_c:
                with_country.append(f)
            else:
                without_country.append(f)
                
        print(f"Total factories: {len(factories)}", flush=True)
        print(f"With country tag: {len(with_country)}", flush=True)
        print(f"Without country tag: {len(without_country)}", flush=True)
        
        new_special = [f for f in without_country if "NEW_" in f.name]
        delete_dont = [f for f in without_country if "DELETE_" in f.name]
        other = [f for f in without_country if "NEW_" not in f.name and "DELETE_" not in f.name]
        
        print(f"  - Starting with NEW_: {len(new_special)}", flush=True)
        print(f"  - Starting with DELETE_: {len(delete_dont)}", flush=True)
        print(f"  - Regular names without country tag: {len(other)}", flush=True)
        
        print("\nSample of regular names without country tag (first 25):", flush=True)
        for f in other[:25]:
            print(f"    * {f.name} (ID: {f.factory_id}, items: {f.items_count}, account: {f.account})", flush=True)
            
        print("\nSample of NEW_ names (first 15):", flush=True)
        for f in new_special[:15]:
            print(f"    * {f.name} (ID: {f.factory_id}, items: {f.items_count}, account: {f.account})", flush=True)

if __name__ == "__main__":
    asyncio.run(analyze())
