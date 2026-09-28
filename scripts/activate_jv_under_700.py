#!/usr/bin/env python3
"""Script to bulk reactivate products priced <= 700 EUR on OTTO JV without modifying other fields."""

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
from app.models.product_variants import ProductVariant  # noqa: F401

os.makedirs("/app/logs", exist_ok=True)
os.makedirs(str(PROJECT_ROOT / "logs"), exist_ok=True)

log_file_path = "/app/logs/activate_jv_under_700.log" if Path("/app/logs").exists() else str(PROJECT_ROOT / "logs/activate_jv_under_700.log")
progress_file_path = "/app/logs/activate_jv_progress.json" if Path("/app/logs").exists() else str(PROJECT_ROOT / "logs/activate_jv_progress.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file_path, encoding="utf-8"),
    ],
)
logger = logging.getLogger("activate_jv_under_700")


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


def load_skus_from_file(input_file: Path) -> list[dict]:
    """Load candidates from previous deactivation json."""
    logger.info("Loading candidates from file: %s", input_file)
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    raise ValueError(f"Unexpected data format in {input_file}: expected list")


async def get_candidates_from_db(max_price: float = 700.0) -> list[dict]:
    """Fallback: query DB for JV products with price <= max_price and active_status == 'Inaktiv'."""
    logger.info("Querying database for JV products with price <= %.2f EUR...", max_price)
    async with SessionLocal() as db:
        stmt = (
            select(
                OttoXlsxImportRow.sku,
                OttoXlsxImportRow.ean,
                OttoXlsxImportRow.price,
                OttoXlsxImportRow.product_name,
                OttoXlsxImportRow.active_status,
                OttoXlsxImportRow.marketplace_status,
            )
            .where(
                func.lower(OttoXlsxImportRow.account) == "jv",
                OttoXlsxImportRow.price <= max_price,
                OttoXlsxImportRow.sku.isnot(None),
                OttoXlsxImportRow.sku != "",
            )
            .order_by(OttoXlsxImportRow.price.asc())
        )
        result = await db.execute(stmt)
        rows = result.all()

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
    return list(candidates_by_sku.values())


async def update_db_statuses_active(skus: list[str]) -> None:
    """Update active_status and marketplace_status to 'Aktiv' for the given SKUs in local DB."""
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
                .values(active_status="Aktiv", marketplace_status="Online")
            )
            await db.execute(
                update(Product)
                .where(Product.sku.in_(skus))
                .values(active_status="Aktiv", marketplace_status="Online")
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to update local DB status to Aktiv for batch of %d SKUs: %s", len(skus), e)


def save_progress(data: dict) -> None:
    """Save progress status to json file for live monitoring."""
    try:
        with open(progress_file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.debug("Failed to write progress file: %s", e)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Input JSON file with candidates (defaults to logs/deactivated_jv_under_700.json)",
    )
    parser.add_argument("--batch-size", type=int, default=100, help="SKUs per OTTO API batch request (default: 100)")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay in seconds between batch requests (default: 0.2)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of SKUs to process (for testing, default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without making API calls")
    args = parser.parse_args()

    start_time = time.time()
    logger.info("================================================================")
    logger.info("  OTTO JV BULK ACTIVATION (REVERT DEACTIVATION)")
    logger.info("  Mode: %s", "DRY-RUN (SIMULATION)" if args.dry_run else "LIVE EXECUTION")
    logger.info("  Batch size: %d, Delay: %.2fs", args.batch_size, args.delay)
    logger.info("================================================================")

    # Determine input file
    input_file = Path(args.input) if args.input else None
    if input_file is None or not input_file.exists():
        candidates_paths = [
            Path("/app/logs/deactivated_jv_under_700.json"),
            PROJECT_ROOT / "logs/deactivated_jv_under_700.json",
        ]
        for p in candidates_paths:
            if p.exists():
                input_file = p
                break

    candidates: list[dict] = []
    if input_file and input_file.exists():
        candidates = load_skus_from_file(input_file)
        logger.info("Loaded %d candidates from %s", len(candidates), input_file)
    else:
        logger.warning("Input file not found. Falling back to DB query...")
        candidates = await get_candidates_from_db(max_price=700.0)

    if args.limit > 0:
        logger.info("Applying limit of %d items (out of %d)", args.limit, len(candidates))
        candidates = candidates[:args.limit]

    total_count = len(candidates)
    if total_count == 0:
        logger.info("No candidates found to activate. Exiting.")
        return

    num_batches = (total_count + args.batch_size - 1) // args.batch_size
    estimated_seconds = num_batches * (0.6 + args.delay)
    logger.info(
        "Total items to activate: %d SKUs | Total batches: %d | Est. time: ~%.1f seconds (~%.1f minutes)",
        total_count,
        num_batches,
        estimated_seconds,
        estimated_seconds / 60.0,
    )

    save_progress({
        "status": "running",
        "mode": "dry-run" if args.dry_run else "live",
        "total": total_count,
        "processed": 0,
        "progress_percent": 0.0,
        "success": 0,
        "failed": 0,
        "current_batch": 0,
        "total_batches": num_batches,
        "elapsed_seconds": 0.0,
        "updated_at": datetime.now().isoformat(),
    })

    if args.dry_run:
        logger.info("DRY-RUN completed. No requests sent to OTTO. Exiting.")
        save_progress({
            "status": "completed",
            "mode": "dry-run",
            "total": total_count,
            "processed": total_count,
            "progress_percent": 100.0,
            "success": total_count,
            "failed": 0,
            "current_batch": num_batches,
            "total_batches": num_batches,
            "elapsed_seconds": round(time.time() - start_time, 1),
            "updated_at": datetime.now().isoformat(),
        })
        return

    client = build_otto_client()
    success_count = 0
    failed_count = 0
    failed_skus = []
    activated_items = []

    logger.info("Starting live activation on OTTO API...")

    for i in range(0, total_count, args.batch_size):
        batch = candidates[i : i + args.batch_size]
        batch_skus = [c["sku"] for c in batch]
        batch_num = (i // args.batch_size) + 1
        progress_pct = (i / total_count) * 100.0

        # Construct status payload for OTTO
        # Sets active = True for each SKU. Stock is untouched.
        payload = {
            "status": [{"sku": sku, "active": True} for sku in batch_skus]
        }

        try:
            status_code, resp = await client.update_active_status(payload, "jv")
            if status_code == 202:
                success_count += len(batch_skus)
                activated_items.extend(batch)
                await update_db_statuses_active(batch_skus)
                logger.info(
                    "[%d/%d] (%.1f%%) Batch of %d SKUs -> OTTO 202 ACCEPTED (ACTIVATED)",
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

        processed = min(i + args.batch_size, total_count)
        save_progress({
            "status": "running",
            "mode": "live",
            "total": total_count,
            "processed": processed,
            "progress_percent": round((processed / total_count) * 100.0, 1),
            "success": success_count,
            "failed": failed_count,
            "current_batch": batch_num,
            "total_batches": num_batches,
            "elapsed_seconds": round(time.time() - start_time, 1),
            "updated_at": datetime.now().isoformat(),
        })

        if args.delay > 0:
            await asyncio.sleep(args.delay)

    # Save final report
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logs_dir = Path("/app/logs") if Path("/app/logs").exists() else (PROJECT_ROOT / "logs")
    report_path = logs_dir / f"activated_jv_under_700_{timestamp}.json"
    latest_report_path = logs_dir / "activated_jv_under_700_latest.json"

    report_data = {
        "timestamp": timestamp,
        "total": total_count,
        "success": success_count,
        "failed": failed_count,
        "elapsed_seconds": round(time.time() - start_time, 1),
        "failed_skus": failed_skus,
    }

    for p in [report_path, latest_report_path]:
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=2, ensure_ascii=False)
            logger.info("Saved activation report to: %s", p)
        except Exception as e:
            logger.warning("Could not write to %s: %s", p, e)

    save_progress({
        "status": "completed",
        "mode": "live",
        "total": total_count,
        "processed": total_count,
        "progress_percent": 100.0,
        "success": success_count,
        "failed": failed_count,
        "current_batch": num_batches,
        "total_batches": num_batches,
        "elapsed_seconds": round(time.time() - start_time, 1),
        "updated_at": datetime.now().isoformat(),
    })

    elapsed = time.time() - start_time
    logger.info("================================================================")
    logger.info("  ACTIVATION COMPLETED in %.1f seconds (%.2f minutes)", elapsed, elapsed / 60.0)
    logger.info("  Total items:   %d", total_count)
    logger.info("  Success (202): %d", success_count)
    logger.info("  Failed:        %d", failed_count)
    if failed_skus:
        logger.warning("  Failed SKUs count: %d", len(failed_skus))
    logger.info("================================================================")


if __name__ == "__main__":
    asyncio.run(main())
