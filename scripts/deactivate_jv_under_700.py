#!/usr/bin/env python3
"""Script to bulk deactivate products priced <= threshold EUR on OTTO JV without modifying other fields."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import func, select, update
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.database import SessionLocal
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.models.products import Product
from app.models.product_variants import ProductVariant

os.makedirs("/app/logs", exist_ok=True)
os.makedirs(str(PROJECT_ROOT / "logs"), exist_ok=True)

log_file_path = "/app/logs/deactivate_jv_under_700.log" if Path("/app/logs").exists() else str(PROJECT_ROOT / "logs/deactivate_jv_under_700.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file_path, encoding="utf-8"),
    ],
)
logger = logging.getLogger("deactivate_jv_under_700")


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


async def get_candidates(
    max_price: float = 700.0,
    only_active: bool = False,
) -> list[dict]:
    """Query DB for distinct JV products with price <= max_price."""
    logger.info("Querying database for JV products with price <= %.2f EUR...", max_price)
    async with SessionLocal() as db:
        conditions = [
            func.lower(OttoXlsxImportRow.account) == "jv",
            OttoXlsxImportRow.price <= max_price,
            OttoXlsxImportRow.sku.isnot(None),
            OttoXlsxImportRow.sku != "",
        ]
        if only_active:
            conditions.append(func.lower(OttoXlsxImportRow.active_status) != "inaktiv")

        stmt = (
            select(
                OttoXlsxImportRow.sku,
                OttoXlsxImportRow.ean,
                OttoXlsxImportRow.price,
                OttoXlsxImportRow.product_name,
                OttoXlsxImportRow.active_status,
                OttoXlsxImportRow.marketplace_status,
            )
            .where(*conditions)
            .order_by(OttoXlsxImportRow.price.asc())
        )
        result = await db.execute(stmt)
        rows = result.all()

    # Deduplicate by SKU (preserve lowest price if duplicates exist)
    candidates_by_sku: dict[str, dict] = {}
    for r in rows:
        sku = str(r.sku).strip()
        if not sku:
            continue
        if sku not in candidates_by_sku:
            candidates_by_sku[sku] = {
                "sku": sku,
                "ean": str(r.ean).strip() if r.ean else "",
                "price": float(r.price) if r.price is not None else 0.0,
                "name": str(r.product_name or "")[:100],
                "active_status": str(r.active_status or ""),
                "marketplace_status": str(r.marketplace_status or ""),
            }

    candidates = list(candidates_by_sku.values())
    logger.info(
        "Found %d unique SKUs on JV with price <= %.2f EUR (only_active=%s)",
        len(candidates),
        max_price,
        only_active,
    )
    return candidates


async def update_db_statuses(skus: list[str]) -> None:
    """Update active_status and marketplace_status to 'Inaktiv' for the given SKUs in local DB."""
    if not skus:
        return
    try:
        async with SessionLocal() as db:
            await db.execute(
                update(OttoXlsxImportRow)
                .where(
                    func.lower(OttoXlsxImportRow.account) == "jv",
                    OttoXlsxImportRow.sku.in_(skus),
                )
                .values(active_status="Inaktiv", marketplace_status="Inaktiv")
            )
            await db.execute(
                update(Product)
                .where(Product.sku.in_(skus))
                .values(active_status="Inaktiv", marketplace_status="Inaktiv")
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to update local DB status for batch of %d SKUs: %s", len(skus), e)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-price", type=float, default=700.0, help="Max price threshold (default: 700.0)")
    parser.add_argument("--only-active", action="store_true", help="Only deactivate products currently marked active in DB")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without making API calls")
    parser.add_argument("--batch-size", type=int, default=100, help="SKUs per OTTO API batch request (default: 100)")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay in seconds between batch requests (default: 0.15)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of SKUs to process (for testing, default: all)")
    args = parser.parse_args()

    start_time = time.time()
    logger.info("================================================================")
    logger.info("  OTTO JV BULK DEACTIVATION (PRICE <= %.2f EUR)", args.max_price)
    logger.info("  Mode: %s", "DRY-RUN (SIMULATION)" if args.dry_run else "LIVE EXECUTION")
    logger.info("  Batch size: %d, Delay: %.2fs", args.batch_size, args.delay)
    logger.info("================================================================")

    candidates = await get_candidates(max_price=args.max_price, only_active=args.only_active)
    if args.limit > 0:
        logger.info("Applying limit of %d items (out of %d)", args.limit, len(candidates))
        candidates = candidates[:args.limit]

    total_count = len(candidates)
    if total_count == 0:
        logger.info("No candidates found matching criteria. Exiting.")
        return

    # Save candidates snapshot
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logs_dir = Path("/app/logs") if Path("/app/logs").exists() else (PROJECT_ROOT / "logs")
    snapshot_path = logs_dir / f"jv_under_{int(args.max_price)}_candidates_{timestamp}.json"
    latest_path = logs_dir / f"jv_under_{int(args.max_price)}_candidates_latest.json"

    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
    logger.info("Candidate snapshot saved to: %s", snapshot_path)

    # Estimate duration
    num_batches = (total_count + args.batch_size - 1) // args.batch_size
    estimated_seconds = num_batches * (0.6 + args.delay)
    logger.info(
        "Total items to process: %d SKUs | Total batches: %d | Est. time: ~%.1f seconds (~%.1f minutes)",
        total_count,
        num_batches,
        estimated_seconds,
        estimated_seconds / 60.0,
    )

    # Print price breakdown sample
    prices = [c["price"] for c in candidates]
    logger.info("Price range: Min = %.2f €, Max = %.2f €, Avg = %.2f €", min(prices), max(prices), sum(prices) / len(prices))
    logger.info("Sample 5 candidates:")
    for sample in candidates[:5]:
        logger.info("  * SKU: %-25s | Price: %7.2f € | Name: %s", sample["sku"], sample["price"], sample["name"])

    if args.dry_run:
        logger.info("DRY-RUN completed. No requests sent to OTTO. Exiting.")
        return

    client = build_otto_client()
    success_count = 0
    failed_count = 0
    failed_skus = []
    deactivated_items = []

    logger.info("Starting live deactivation on OTTO API...")

    for i in range(0, total_count, args.batch_size):
        batch = candidates[i : i + args.batch_size]
        batch_skus = [c["sku"] for c in batch]
        batch_num = (i // args.batch_size) + 1
        progress_pct = (i / total_count) * 100.0

        # Construct status payload for OTTO
        # ONLY modifies active status to false. Does NOT touch quantity, price or any other property.
        payload = {
            "status": [{"sku": sku, "active": False} for sku in batch_skus]
        }

        try:
            status_code, resp = await client.update_active_status(payload, "jv")
            if status_code == 202:
                success_count += len(batch_skus)
                deactivated_items.extend(batch)
                await update_db_statuses(batch_skus)
                logger.info(
                    "[%d/%d] (%.1f%%) Batch of %d SKUs -> OTTO 202 ACCEPTED",
                    batch_num,
                    num_batches,
                    progress_pct,
                    len(batch_skus),
                )
            else:
                failed_count += len(batch_skus)
                failed_skus.extend(batch_skus)
                logger.error(
                    "[%d/%d] (%.1f%%) Batch of %d SKUs FAILED with status %s: %s",
                    batch_num,
                    num_batches,
                    progress_pct,
                    len(batch_skus),
                    status_code,
                    resp,
                )
        except Exception as e:
            failed_count += len(batch_skus)
            failed_skus.extend(batch_skus)
            logger.exception(
                "[%d/%d] Exception processing batch of %d SKUs: %s",
                batch_num,
                num_batches,
                len(batch_skus),
                e,
            )

        if args.delay > 0:
            await asyncio.sleep(args.delay)

    # Save deactivated products to JSON
    deactivated_file_ts = logs_dir / f"deactivated_jv_under_{int(args.max_price)}_{timestamp}.json"
    deactivated_file_latest = logs_dir / "deactivated_jv_under_700.json"
    for out_path in [deactivated_file_ts, deactivated_file_latest]:
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(deactivated_items, f, ensure_ascii=False, indent=2)
            logger.info("Saved %d deactivated items to: %s", len(deactivated_items), out_path)
        except Exception as e:
            logger.warning("Could not write to %s: %s", out_path, e)

    elapsed = time.time() - start_time
    logger.info("================================================================")
    logger.info("  DEACTIVATION COMPLETED in %.1f seconds (%.2f minutes)", elapsed, elapsed / 60.0)
    logger.info("  Total items:   %d", total_count)
    logger.info("  Success (202): %d", success_count)
    logger.info("  Failed:        %d", failed_count)
    if failed_skus:
        failed_file = logs_dir / f"jv_deactivate_failed_{timestamp}.json"
        with open(failed_file, "w", encoding="utf-8") as f:
            json.dump(failed_skus, f, indent=2)
        logger.warning("Saved list of %d failed SKUs to %s", len(failed_skus), failed_file)
    logger.info("================================================================")


if __name__ == "__main__":
    asyncio.run(main())
