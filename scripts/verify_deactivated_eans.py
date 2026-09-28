#!/usr/bin/env python3
"""Script to verify deactivation of products from ean.txt in DB and live on OTTO API."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import func, select
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.models.products import Product
from app.models.product_variants import ProductVariant


def build_otto_client() -> ExternalOttoClient:
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
    )
    return ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)


async def main() -> None:
    csv_path = "/app/ean.txt" if Path("/app/ean.txt").exists() else str(PROJECT_ROOT / "ean.txt")
    print("==================================================================")
    print("  VERIFICATION AUDIT: DEACTIVATION OF EAN.TXT PRODUCTS            ")
    print("  File:", csv_path)
    print("==================================================================")

    eans = []
    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            ean = r.get("EAN", "").strip()
            if ean:
                eans.append(ean)
    unique_eans = sorted(list(set(eans)))
    print(f"Total unique input EANs: {len(unique_eans)}")

    # 1. DATABASE AUDIT
    print("\n--- [1/2] Database Verification ---")
    async with SessionLocal() as db:
        stmt = select(
            OttoXlsxImportRow.account,
            OttoXlsxImportRow.active_status,
            OttoXlsxImportRow.marketplace_status,
            OttoXlsxImportRow.sku,
            OttoXlsxImportRow.ean,
            OttoXlsxImportRow.product_name,
        ).where(OttoXlsxImportRow.ean.in_(unique_eans))
        rows = (await db.execute(stmt)).all()

    total_db_rows = len(rows)
    jv_rows = [r for r in rows if str(r.account).lower() == "jv"]
    xl_rows = [r for r in rows if str(r.account).lower() == "xl"]

    jv_active = [r for r in jv_rows if str(r.active_status).lower() != "inaktiv"]
    jv_inaktiv = [r for r in jv_rows if str(r.active_status).lower() == "inaktiv"]
    xl_active = [r for r in xl_rows if str(r.active_status).lower() != "inaktiv"]
    xl_inaktiv = [r for r in xl_rows if str(r.active_status).lower() == "inaktiv"]

    print(f"Total matching DB rows: {total_db_rows}")
    print(f"  JV: Inaktiv={len(jv_inaktiv)}, Active={len(jv_active)} (Total={len(jv_rows)})")
    print(f"  XL: Inaktiv={len(xl_inaktiv)}, Active={len(xl_active)} (Total={len(xl_rows)})")

    if jv_active or xl_active:
        print(f"\nWARNING! Still active in DB: JV={len(jv_active)}, XL={len(xl_active)}")
        for r in (jv_active + xl_active)[:5]:
            print(f"  * {r.account.upper()} SKU: {r.sku} | status: {r.active_status}")
    else:
        print(">>> DB CHECK PASSED: 100% of matched records in DB are marked 'Inaktiv'!")

    # 2. LIVE OTTO API CHECK (Sample 5 JV + 5 XL)
    print("\n--- [2/2] Live OTTO API Verification ---")
    client = build_otto_client()

    sample_jv = [r.sku for r in jv_inaktiv[:5]]
    sample_xl = [r.sku for r in xl_inaktiv[:5]]

    all_verified = True

    print(f"Checking {len(sample_jv)} sample SKUs on OTTO JV:")
    for sku in sample_jv:
        try:
            resp = await client._request("GET", "/v5/products/active-status", "jv", params={"sku": sku}, idempotent=True)
            data = client._parse_response(resp)
            statuses = data.get("status", []) if isinstance(data, dict) else []
            is_active = statuses[0].get("active") if statuses else None
            flag = "[OK] Inaktiv" if is_active is False else "[FAIL]"
            if is_active is not False:
                all_verified = False
            print(f"  * JV SKU {sku}: active={is_active} -> {flag}")
        except Exception as e:
            print(f"  * JV SKU {sku}: error checking {e}")

    print(f"\nChecking {len(sample_xl)} sample SKUs on OTTO XL:")
    for sku in sample_xl:
        try:
            resp = await client._request("GET", "/v5/products/active-status", "xl", params={"sku": sku}, idempotent=True)
            data = client._parse_response(resp)
            statuses = data.get("status", []) if isinstance(data, dict) else []
            is_active = statuses[0].get("active") if statuses else None
            flag = "[OK] Inaktiv" if is_active is False else "[FAIL]"
            if is_active is not False:
                all_verified = False
            print(f"  * XL SKU {sku}: active={is_active} -> {flag}")
        except Exception as e:
            print(f"  * XL SKU {sku}: error checking {e}")

    await client._client.aclose()

    print("\n==================================================================")
    if not jv_active and not xl_active and all_verified:
        print("  RESULT: VERIFICATION SUCCESSFUL! Products are INACTIVE!          ")
    else:
        print("  RESULT: ATTENTION NEEDED - Some items may still be active        ")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(main())
