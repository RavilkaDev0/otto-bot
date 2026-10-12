import json
import sys
import os
from collections import Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.database import SessionLocal, engine
from sqlalchemy import select, func
from app.models.otto_xlsx_import import OttoXlsxImportRow
import asyncio

async def analyze():
    with open("remaining_unupdated_tr_all.json") as f:
        data = json.load(f)

    all_eans = set()
    for d in data:
        for it in d["all_items"]:
            all_eans.add(it["ean"])

    print(f"Total unique candidate EANs to analyze: {len(all_eans)}")

    async with SessionLocal() as db:
        # Check by account and marketplace_status
        res = await db.execute(
            select(OttoXlsxImportRow.account, OttoXlsxImportRow.marketplace_status, func.count(OttoXlsxImportRow.id))
            .where(OttoXlsxImportRow.ean.in_(all_eans))
            .group_by(OttoXlsxImportRow.account, OttoXlsxImportRow.marketplace_status)
        )
        counts = res.all()

    print("\nDatabase breakdown of these candidate items:")
    total_in_db = 0
    for acc, status, cnt in counts:
        print(f"  Account '{acc}' | Status '{status}': {cnt} items")
        total_in_db += cnt

    not_in_db = len(all_eans) - total_in_db
    print(f"\nItems NOT present in local DB (never uploaded to OTTO or new drafts): {not_in_db}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(analyze())
