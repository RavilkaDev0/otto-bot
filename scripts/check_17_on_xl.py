import asyncio
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, func
from app.database import SessionLocal, engine
from app.models.factories import Factories

MISSING_17 = [
    "Timur_for_Otto_Standard_9",
    "NEW_NOTT_PRICE_CHANGE",
    "NEW_Person_4_Xenia",
    "NEW_SERHATHOME_SPECIAL_AINUR",
    "NEW_FAVORY_SPECIAL",
    "Timur_for_Otto_Standard_8",
    "NEW_Radmila",
    "Montro_(TR)",
    "Twins_(TR)",
    "Narista_(TR)",
    "NEW_DEPOTUM",
    "Bürosit_(TR)",
    "New_Home_Design_(TR)",
    "T&H_(TR)",
    "Others_Löschen",
    "MillHouse_(TR)",
    "Kromev_(TR)",
]

async def check():
    async with SessionLocal() as db:
        res = await db.execute(
            select(Factories).where(func.lower(Factories.account) == "xl")
        )
        xl_factories = res.scalars().all()

    xl_dict = {f.name.lower().strip(): f for f in xl_factories}

    print("=== STATUS OF 17 FACTORIES ON XL ACCOUNT ===\n")
    found_count = 0
    not_found_count = 0
    for name in MISSING_17:
        low = name.lower().strip()
        f = xl_dict.get(low)
        if not f:
            # try fuzzy
            clean = name.replace("_(TR)", "").replace("(TR)", "").lower().strip()
            for k, v in xl_dict.items():
                if clean in k:
                    f = v
                    break
        if f:
            print(f"  FOUND on XL: {f.name} (ID: {f.factory_id}, items_count: {f.items_count})")
            found_count += 1
        else:
            print(f"  NOT PRESENT on XL: {name} (0 items on XL)")
            not_found_count += 1

    print(f"\nSummary: {found_count} exist on XL, {not_found_count} DO NOT exist on XL account at all.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check())
