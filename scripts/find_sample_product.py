import asyncio
import sys
import json

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from sqlalchemy import select

async def main():
    async with SessionLocal() as db:
        res = await db.execute(
            select(OttoXlsxImportRow.sku, OttoXlsxImportRow.ean, OttoXlsxImportRow.product_name, OttoXlsxImportRow.raw_payload)
            .where(
                OttoXlsxImportRow.product_name.ilike("%Beiges Kinderbett Leder Jugendbett%")
            )
        )
        rows = res.all()
        print(f"Found in DB: {len(rows)}")
        for r in rows:
            print("SKU:", r[0], "EAN:", r[1], "Name:", r[2])
            raw = r[3] or {}
            print("Raw keys:", list(raw.keys()))
            for k, v in raw.items():
                if any(x in str(v).lower() for x in ["country", "cn", "china", "leder", "kinderbett"]):
                    print(f"  {k}: {v}")

if __name__ == "__main__":
    asyncio.run(main())
