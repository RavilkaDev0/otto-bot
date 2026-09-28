#!/usr/bin/env python3
"""Script to bulk deactivate products from ean.txt on OTTO (JV and XL) and update local DB."""

from __future__ import annotations

import argparse
import asyncio
import csv
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

log_file_path = (
    "/app/logs/deactivate_eans_from_file.log"
    if Path("/app/logs").exists()
    else str(PROJECT_ROOT / "logs/deactivate_eans_from_file.log")
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file_path, encoding="utf-8"),
    ],
)
logger = logging.getLogger("deactivate_eans")


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


def load_eans_from_file(file_path: str) -> list[dict]:
    """Read CSV file and extract unique EANs with metadata."""
    eans_map: dict[str, dict] = {}
    with open(file_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            ean = r.get("EAN", "").strip()
            if not ean:
                continue
            if ean not in eans_map:
                try:
                    curr_p = float(r.get("CURRENT_PRICE", 0.0))
                except (ValueError, TypeError):
                    curr_p = 0.0
                try:
                    target_p = float(r.get("TARGET_MAX_PRICE", 0.0))
                except (ValueError, TypeError):
                    target_p = 0.0
                try:
                    diff_p = float(r.get("PRICE_DIFF", 0.0))
                except (ValueError, TypeError):
                    diff_p = 0.0

                eans_map[ean] = {
                    "ean": ean,
                    "sku": ean,
                    "url": r.get("URL", ""),
                    "title": r.get("TITLE", ""),
                    "current_price": curr_p,
                    "target_max_price": target_p,
                    "price_diff": diff_p,
                }
    return list(eans_map.values())


async def map_eans_to_db(items: list[dict]) -> tuple[list[str], list[str], list[str], dict]:
    """Map EANs against local database (OttoXlsxImportRow)."""
    all_eans = [it["ean"] for it in items]
    logger.info("Mapping %d EANs against database...", len(all_eans))

    async with SessionLocal() as db:
        stmt = select(
            OttoXlsxImportRow.account,
            OttoXlsxImportRow.active_status,
            OttoXlsxImportRow.marketplace_status,
            OttoXlsxImportRow.sku,
            OttoXlsxImportRow.ean,
            OttoXlsxImportRow.price,
            OttoXlsxImportRow.product_name,
        ).where(OttoXlsxImportRow.ean.in_(all_eans))
        res = await db.execute(stmt)
        rows = res.all()

    db_info_by_ean: dict[str, list[dict]] = {}
    for r in rows:
        ean = str(r.ean).strip()
        if ean not in db_info_by_ean:
            db_info_by_ean[ean] = []
        db_info_by_ean[ean].append({
            "account": str(r.account).lower(),
            "active_status": str(r.active_status or ""),
            "marketplace_status": str(r.marketplace_status or ""),
            "price": float(r.price) if r.price is not None else None,
            "product_name": r.product_name or "",
        })

    jv_skus = set()
    xl_skus = set()
    neither_skus = set()

    for it in items:
        ean = it["ean"]
        records = db_info_by_ean.get(ean, [])
        accounts = set(rec["account"] for rec in records)
        if "jv" in accounts:
            jv_skus.add(ean)
        if "xl" in accounts:
            xl_skus.add(ean)
        if not accounts:
            neither_skus.add(ean)

    logger.info("DB mapping results:")
    logger.info("  Total input EANs:       %d", len(all_eans))
    logger.info("  Matched on JV:          %d", len(jv_skus))
    logger.info("  Matched on XL:          %d", len(xl_skus))
    logger.info("  Matched on Both:        %d", len(jv_skus & xl_skus))
    logger.info("  Not found in DB:        %d", len(neither_skus))

    return sorted(list(jv_skus)), sorted(list(xl_skus)), sorted(list(neither_skus)), db_info_by_ean


async def update_db_statuses(skus: list[str], account: str | None = None) -> int:
    """Update active_status and marketplace_status to 'Inaktiv' in DB."""
    if not skus:
        return 0
    updated_count = 0
    try:
        async with SessionLocal() as db:
            conds = [OttoXlsxImportRow.sku.in_(skus)]
            if account:
                conds.append(func.lower(OttoXlsxImportRow.account) == account.lower())

            stmt = (
                update(OttoXlsxImportRow)
                .where(*conds)
                .values(active_status="Inaktiv", marketplace_status="Inaktiv")
            )
            res = await db.execute(stmt)
            updated_count += res.rowcount

            stmt_p = (
                update(Product)
                .where(Product.sku.in_(skus))
                .values(active_status="Inaktiv", marketplace_status="Inaktiv")
            )
            await db.execute(stmt_p)

            await db.commit()
    except Exception as e:
        logger.warning("Failed to update local DB status for batch: %s", e)
    return updated_count


async def deactivate_controller_skus(
    client: ExternalOttoClient,
    controller: str,
    skus: list[str],
    batch_size: int = 100,
    delay: float = 0.15,
) -> tuple[int, int, list[str]]:
    """Send deactivation requests to OTTO API in batches."""
    total = len(skus)
    if total == 0:
        return 0, 0, []

    success_count = 0
    failed_count = 0
    failed_skus = []
    num_batches = (total + batch_size - 1) // batch_size

    logger.info(
        "Sending deactivation for %s: %d SKUs in %d batches (batch_size=%d)...",
        controller.upper(),
        total,
        num_batches,
        batch_size,
    )

    for i in range(0, total, batch_size):
        batch = skus[i : i + batch_size]
        batch_num = (i // batch_size) + 1
        pct = (i / total) * 100.0

        payload = {
            "status": [{"sku": sku, "active": False} for sku in batch]
        }

        try:
            status_code, resp = await client.update_active_status(payload, controller)
            if status_code == 202:
                success_count += len(batch)
                await update_db_statuses(batch, account=controller)
                logger.info(
                    "[%s %d/%d] (%.1f%%) Batch of %d SKUs -> 202 ACCEPTED",
                    controller.upper(),
                    batch_num,
                    num_batches,
                    pct,
                    len(batch),
                )
            else:
                failed_count += len(batch)
                failed_skus.extend(batch)
                logger.error(
                    "[%s %d/%d] (%.1f%%) Batch of %d SKUs FAILED (%s): %s",
                    controller.upper(),
                    batch_num,
                    num_batches,
                    pct,
                    len(batch),
                    status_code,
                    resp,
                )
        except Exception as e:
            failed_count += len(batch)
            failed_skus.extend(batch)
            logger.exception(
                "[%s %d/%d] Exception processing batch of %d SKUs: %s",
                controller.upper(),
                batch_num,
                num_batches,
                len(batch),
                e,
            )

        if delay > 0:
            await asyncio.sleep(delay)

    return success_count, failed_count, failed_skus


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        type=str,
        default="/app/ean.txt" if Path("/app/ean.txt").exists() else str(PROJECT_ROOT / "ean.txt"),
        help="Path to ean.txt CSV file",
    )
    parser.add_argument("--batch-size", type=int, default=100, help="SKUs per OTTO API batch request")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay in seconds between batch requests")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without making API calls")
    parser.add_argument(
        "--include-unknown",
        action="store_true",
        default=True,
        help="Also send unclassified EANs to both JV and XL (default: True)",
    )
    args = parser.parse_args()

    start_time = time.time()
    logger.info("==================================================================")
    logger.info("  OTTO BULK DEACTIVATION FROM FILE")
    logger.info("  File: %s", args.file)
    logger.info("  Mode: %s", "DRY-RUN (SIMULATION)" if args.dry_run else "LIVE EXECUTION")
    logger.info("  Batch size: %d, Delay: %.2fs", args.batch_size, args.delay)
    logger.info("==================================================================")

    items = load_eans_from_file(args.file)
    if not items:
        logger.error("No valid EANs found in file: %s", args.file)
        return

    logger.info("Loaded %d unique EANs from %s", len(items), args.file)

    jv_skus, xl_skus, neither_skus, db_info = await map_eans_to_db(items)

    # If include-unknown, add neither_skus to both JV and XL targets so that
    # any unclassified item is also covered on OTTO.
    if args.include_unknown and neither_skus:
        jv_targets = sorted(list(set(jv_skus) | set(neither_skus)))
        xl_targets = sorted(list(set(xl_skus) | set(neither_skus)))
        logger.info(
            "Including %d unclassified EANs in both JV and XL requests: JV target count = %d, XL target count = %d",
            len(neither_skus),
            len(jv_targets),
            len(xl_targets),
        )
    else:
        jv_targets = jv_skus
        xl_targets = xl_skus

    if args.dry_run:
        logger.info("DRY-RUN completed. Exiting without modifying OTTO or DB.")
        return

    client = build_otto_client()

    # 1. Process JV
    jv_success, jv_failed, jv_failed_skus = await deactivate_controller_skus(
        client=client,
        controller="jv",
        skus=jv_targets,
        batch_size=args.batch_size,
        delay=args.delay,
    )

    # 2. Process XL
    xl_success, xl_failed, xl_failed_skus = await deactivate_controller_skus(
        client=client,
        controller="xl",
        skus=xl_targets,
        batch_size=args.batch_size,
        delay=args.delay,
    )

    # Close client
    await client._client.aclose()

    # 3. Save comprehensive report
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logs_dir = Path("/app/logs") if Path("/app/logs").exists() else (PROJECT_ROOT / "logs")

    report_data = {
        "timestamp": timestamp,
        "input_file": str(args.file),
        "total_unique_input_eans": len(items),
        "mapped_in_db": {
            "jv_count": len(jv_skus),
            "xl_count": len(xl_skus),
            "both_count": len(set(jv_skus) & set(xl_skus)),
            "neither_count": len(neither_skus),
        },
        "execution": {
            "jv": {
                "targeted": len(jv_targets),
                "success": jv_success,
                "failed": jv_failed,
                "failed_skus": jv_failed_skus,
            },
            "xl": {
                "targeted": len(xl_targets),
                "success": xl_success,
                "failed": xl_failed,
                "failed_skus": xl_failed_skus,
            },
        },
        "items": [
            {
                **it,
                "db_records": db_info.get(it["ean"], []),
            }
            for it in items
        ],
    }

    report_path = logs_dir / f"deactivated_eans_report_{timestamp}.json"
    latest_report_path = logs_dir / "deactivated_eans_report_latest.json"

    for p in [report_path, latest_report_path]:
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2)
            logger.info("Saved report to %s", p)
        except Exception as e:
            logger.warning("Could not write report to %s: %s", p, e)

    elapsed = time.time() - start_time
    logger.info("==================================================================")
    logger.info("  DEACTIVATION FINISHED in %.1f seconds (%.2f minutes)", elapsed, elapsed / 60.0)
    logger.info("  Total input EANs:       %d", len(items))
    logger.info("  JV: %d succeeded, %d failed", jv_success, jv_failed)
    logger.info("  XL: %d succeeded, %d failed", xl_success, xl_failed)
    logger.info("==================================================================")


if __name__ == "__main__":
    asyncio.run(main())
