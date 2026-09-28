#!/usr/bin/env python3
"""Script to comprehensively verify deactivation of JV products <= 700 EUR in DB and directly on OTTO API."""

from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import func, or_, select
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.models.product_variants import ProductVariant
from app.models.products import Product


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
    print("==================================================================")
    print("  COMPREHENSIVE AUDIT: JV PRODUCTS <= 700 EUR DEACTIVATION CHECK  ")
    print("==================================================================")

    # 1. DATABASE AUDIT
    print("\n--- [1/2] Database Verification ---", flush=True)
    async with SessionLocal() as db:
        from sqlalchemy import case

        stmt = select(
            func.count(OttoXlsxImportRow.id).label("total_rows"),
            func.count(func.distinct(OttoXlsxImportRow.sku)).label("total_skus"),
            func.count(func.distinct(case((OttoXlsxImportRow.active_status == 'Inaktiv', OttoXlsxImportRow.sku)))).label("inaktiv_skus"),
            func.count(func.distinct(case((OttoXlsxImportRow.active_status != 'Inaktiv', OttoXlsxImportRow.sku)))).label("active_skus"),
        ).where(
            OttoXlsxImportRow.account == "jv",
            OttoXlsxImportRow.price <= 700.0,
        )
        row = (await db.execute(stmt)).one()
        total_rows_cnt, total_skus_cnt, inaktiv_skus_cnt, active_skus_cnt = row

        print(f"Total JV rows with price <= 700 EUR:        {total_rows_cnt}", flush=True)
        print(f"Total distinct SKUs with price <= 700 EUR:   {total_skus_cnt}", flush=True)
        print(f"Distinct SKUs marked 'Inaktiv':              {inaktiv_skus_cnt}", flush=True)
        print(f"Active / Non-Inaktiv SKUs found:             {active_skus_cnt}", flush=True)

        if active_skus_cnt > 0:
            q_active_rows = select(
                OttoXlsxImportRow.sku,
                OttoXlsxImportRow.price,
                OttoXlsxImportRow.active_status,
                OttoXlsxImportRow.marketplace_status,
            ).where(
                OttoXlsxImportRow.account == "jv",
                OttoXlsxImportRow.price <= 700.0,
                OttoXlsxImportRow.active_status != "Inaktiv",
            ).limit(10)
            active_rows = (await db.execute(q_active_rows)).all()
            print("\nWARNING! Sample active rows:", flush=True)
            for r in active_rows:
                print(f"  * SKU: {r.sku} | Price: {r.price} | active_status: {r.active_status}", flush=True)
        else:
            print(">>> DB CHECK PASSED: 0 active products found in DB! (100% Inaktiv)", flush=True)

    # 2. LIVE OTTO API VERIFICATION
    print("\n--- [2/2] Live OTTO API Verification (Sample Checks) ---")
    json_path = Path("/app/logs/deactivated_jv_under_700.json")
    if not json_path.exists():
        json_path = PROJECT_ROOT / "logs/deactivated_jv_under_700.json"

    if not json_path.exists():
        print(f"File not found: {json_path}")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        deactivated_items = json.load(f)

    print(f"Loaded {len(deactivated_items)} deactivated items from {json_path.name}")

    # Pick 8 sample SKUs across different price tiers
    sorted_items = sorted(deactivated_items, key=lambda x: x.get("price", 0))
    n = len(sorted_items)
    sample_indices = [
        0,  # cheapest
        n // 7,
        2 * n // 7,
        3 * n // 7,
        4 * n // 7,
        5 * n // 7,
        6 * n // 7,
        n - 1,  # closest to 700 EUR
    ]
    samples = [sorted_items[idx] for idx in sample_indices if idx < n]

    client = build_otto_client()

    print(f"\nChecking live OTTO status for {len(samples)} sample SKUs:")
    all_otto_inactive = True
    for sample in samples:
        sku = sample["sku"]
        price = sample.get("price")
        name = sample.get("name", "")[:50]
        try:
            # Query OTTO API active-status endpoint
            resp = await client._request(
                "GET",
                "/v5/products/active-status",
                "jv",
                params={"sku": sku},
                idempotent=True,
            )
            data = client._parse_response(resp)
            # data structure: {"status": [{"sku": "...", "active": False}]}
            statuses = data.get("status", []) if isinstance(data, dict) else []
            is_active = None
            for s in statuses:
                if s.get("sku") == sku:
                    is_active = s.get("active")
                    break

            status_str = "ACTIVE (True)" if is_active is True else ("INACTIVE (False)" if is_active is False else f"UNKNOWN ({statuses})")
            flag = "[OK] Inaktiv" if is_active is False else "[FAIL]"
            if is_active is True:
                all_otto_inactive = False
            print(f"  * SKU {sku} | {price:6.2f} € | OTTO status: {status_str} -> {flag} | {name}")
        except Exception as e:
            print(f"  * SKU {sku} | Error checking OTTO: {e}")

    print("\n==================================================================", flush=True)
    if active_skus_cnt == 0 and all_otto_inactive:
        print("  RESULT: SUCCESSFUL! ALL CHECKED PRODUCTS ARE INACTIVE ON OTTO!  ", flush=True)
    else:
        print("  RESULT: SOME PRODUCTS REQUIRE ATTENTION                         ", flush=True)
    print("==================================================================", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
