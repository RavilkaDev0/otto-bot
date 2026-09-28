import asyncio
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow

eans = [
    "4070863389648",  # Alikotuk
    "4070863389655",  # Alikotuk
    "4069424219593",  # Gabba
    "4069424219821",  # Gabba
    "4070863316965",  # LUCE GARDEN
    "4069943111033",  # LUCE GARDEN
]

async def check():
    async with SessionLocal() as db:
        for e in eans:
            res = await db.execute(select(OttoXlsxImportRow).where(OttoXlsxImportRow.ean == e))
            rows = res.scalars().all()
            print(f"EAN {e}: {len(rows)} rows in DB")
            for r in rows:
                print(f"   -> account: {r.account}, status: {r.marketplace_status}, product_name: {r.product_name}")

if __name__ == "__main__":
    asyncio.run(check())
