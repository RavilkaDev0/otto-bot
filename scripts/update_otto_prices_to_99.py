#!/usr/bin/env python3
"""Script to update all product prices on OTTO JV & XL to end in ,99 EUR (e.g. 1999 -> 1999.99 EUR).

How the price is changed:
- OTTO's `POST /v5/products/prices` does NOT apply changes (returns dummy empty task with total=0).
- The working path is a full-variation upsert: GET /v5/products/{sku} -> change
  `pricing.standardPrice.amount` -> POST /v5/products (batch of 100).
- The new price is computed strictly from the LIVE OTTO price.

High-Performance & Correctness Pipeline:
- Exclusion list (--exclude-file): automatically skips deactivated low-performers
  (e.g. 12_moths_JV_OTTO.txt), saving 62,000+ unnecessary calls.
- Streaming Chunking (--chunk-size 1000): candidates are fetched and processed in chunks.
  Each chunk is read, sent in batches of 100, verified, and committed to the DB incrementally.
  If the process is interrupted, re-running instantly resumes from where it left off.
- Verification modes (--verify-mode canary | all | none):
  * canary (default): verifies 1 sample item per batch live in OTTO for safety and speed.
  * all: re-reads 100% of updated items from OTTO via GET.
  * none: relies purely on OTTO update-tasks /succeeded endpoint.
- Retry with exponential backoff on HTTP 429 (respecting Retry-After header) and 5xx.
- Dry-run mode (--dry-run): reads live data from OTTO, previews changes, touches nothing.
- Account filtering (--account jv | xl | both) and explicit SKU mode (--sku ...).
"""

import argparse
import asyncio
import copy
import json
import logging
import math
import os
import random
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SAWarning

warnings.filterwarnings("ignore", category=SAWarning)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, update, or_, func
from app.database import SessionLocal, engine
from app.dependencies import get_otto_client
from app.schemas.enums import Controller
from app.models.otto_xlsx_import import OttoXlsxImportRow
from app.models.product_variants import ProductVariant
from app.models.products import Product

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/update_otto_prices_to_99.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("price_to_99_updater")

BATCH_SIZE_DEFAULT = 100
CHUNK_SIZE_DEFAULT = 1000
DELAY_DEFAULT = 0.3
HTTP_CONCURRENCY = 8
RATE_LIMIT_DEFAULT = 10.0  # requests per second (OTTO API limit is 20/s)
TASK_POLL_INTERVAL = 3.0
TASK_TIMEOUT = 300.0
VERIFY_ATTEMPTS = 4
VERIFY_RETRY_DELAY = 10.0
REPORT_LIST_CAP = 200
DEFAULT_JV_EXCLUDE_FILE = "12_moths_JV_OTTO.txt"


class AsyncRateLimiter:
    """Token-bucket rate limiter to enforce max requests per second across coroutines."""

    def __init__(self, rate: float = 10.0, burst: float = 10.0):
        self.rate = max(1.0, float(rate))
        self.burst = max(1.0, float(burst))
        self.tokens = self.burst
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()
        self._pause_until = 0.0

    def pause(self, seconds: float) -> None:
        """Pause all requests across all workers when 429 is encountered."""
        now = time.monotonic()
        self._pause_until = max(self._pause_until, now + seconds)

    async def acquire(self) -> None:
        """Wait until a token is available, respecting pauses and rate limit."""
        while True:
            now = time.monotonic()
            if now < self._pause_until:
                await asyncio.sleep(self._pause_until - now + 0.1)
                continue

            async with self._lock:
                now = time.monotonic()
                if now < self._pause_until:
                    wait = self._pause_until - now + 0.1
                else:
                    elapsed = now - self.last_update
                    self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
                    self.last_update = now
                    if self.tokens >= 1.0:
                        self.tokens -= 1.0
                        return
                    wait = (1.0 - self.tokens) / self.rate
            await asyncio.sleep(max(0.01, wait))


def calculate_99_price(price: float | None) -> float | None:
    """Calculate price ending in .99 EUR using floor(price) + 0.99.

    Examples:
        1999.00 -> 1999.99
        1999.50 -> 1999.99
        1999.99 -> 1999.99
        235.00  -> 235.99
    """
    if price is None or price <= 0:
        return None
    base = math.floor(price)
    return round(base + 0.99, 2)


def is_already_99(price: float | None) -> bool:
    """Check if price already ends in .99 (within floating point precision)."""
    if price is None or price <= 0:
        return False
    target = round(math.floor(price) + 0.99, 2)
    return abs(price - target) < 0.005


def load_exclude_skus(file_path: str | None, account: str) -> set[str]:
    """Load SKUs to exclude from price update."""
    target_path = file_path
    if not target_path and account.lower() == "jv" and os.path.exists(DEFAULT_JV_EXCLUDE_FILE):
        target_path = DEFAULT_JV_EXCLUDE_FILE

    if not target_path or not os.path.exists(target_path):
        return set()

    with open(target_path, "r", encoding="utf-8") as f:
        skus = {line.strip() for line in f if line.strip() and not line.startswith("#")}

    logger.info("[%s] Loaded %d SKUs to exclude from %s", account.upper(), len(skus), target_path)
    return skus


async def fetch_candidates(
    account: str,
    active_only: bool = True,
    limit: int | None = None,
    exclude_skus: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch products from database that need price update for the given account."""
    exclude = exclude_skus or set()
    async with SessionLocal() as db:
        query = select(
            OttoXlsxImportRow.id,
            OttoXlsxImportRow.account,
            OttoXlsxImportRow.sku,
            OttoXlsxImportRow.ean,
            OttoXlsxImportRow.price,
            OttoXlsxImportRow.product_name,
            OttoXlsxImportRow.active_status,
            OttoXlsxImportRow.marketplace_status,
        ).where(
            func.lower(OttoXlsxImportRow.account) == account.lower(),
            OttoXlsxImportRow.sku.isnot(None),
            OttoXlsxImportRow.sku != "",
            OttoXlsxImportRow.price.isnot(None),
            OttoXlsxImportRow.price > 0,
        )

        if active_only:
            query = query.where(
                or_(
                    OttoXlsxImportRow.active_status == "Aktiv",
                    OttoXlsxImportRow.active_status == "Aktiv (Bestand 0)",
                    OttoXlsxImportRow.marketplace_status == "ONLINE",
                )
            )

        query = query.order_by(OttoXlsxImportRow.id.asc())
        result = await db.execute(query)
        rows = result.all()

    # Deduplicate by SKU (take the most recent row)
    candidates_by_sku: dict[str, dict[str, Any]] = {}
    already_99_count = 0
    excluded_count = 0

    for r in rows:
        sku = str(r.sku).strip()
        if not sku:
            continue
        if sku in exclude:
            excluded_count += 1
            continue
        price = float(r.price)
        if is_already_99(price):
            already_99_count += 1
            continue

        candidates_by_sku[sku] = {
            "db_id": r.id,
            "account": str(r.account).lower(),
            "sku": sku,
            "ean": str(r.ean).strip() if r.ean else "",
            "db_price": price,
            "name": str(r.product_name or "")[:80],
            "active_status": str(r.active_status or ""),
            "marketplace_status": str(r.marketplace_status or ""),
        }

    items = list(candidates_by_sku.values())
    if limit is not None and limit > 0:
        items = items[:limit]

    logger.info(
        "[%s] Found %d candidate SKUs in DB (already .99 in DB: %d, excluded: %d, total matching rows: %d)",
        account.upper(),
        len(items),
        already_99_count,
        excluded_count,
        len(rows),
    )
    return items


async def _call_with_retry(
    call,
    what: str,
    max_retries: int = 5,
    rate_limiter: AsyncRateLimiter | None = None,
) -> tuple[int, Any]:
    """Run `call()` -> (status, body); retry on 429 / 5xx / network errors with backoff and rate limiter."""
    delays = [2, 5, 15, 30, 45]
    status, body = 0, None
    for attempt in range(max_retries):
        if rate_limiter:
            await rate_limiter.acquire()
        try:
            status, body = await call()
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            wait = delays[min(attempt, len(delays) - 1)]
            logger.warning("%s: network error %s. Retry in %ds", what, exc, wait)
            if rate_limiter:
                rate_limiter.pause(wait)
            await asyncio.sleep(wait)
            continue
        if status == 429:
            wait = 25 * (attempt + 1)
            logger.warning("%s: rate limited (429). Pausing all workers for %ds", what, wait)
            if rate_limiter:
                rate_limiter.pause(wait)
            await asyncio.sleep(wait)
            continue
        if status in (502, 503, 504):
            wait = delays[min(attempt, len(delays) - 1)]
            logger.warning("%s: server error %d. Retry in %ds", what, status, wait)
            await asyncio.sleep(wait)
            continue
        return status, body
    return status, body


async def fetch_live_variation(
    client: Any,
    ctrl: Controller,
    sku: str,
    rate_limiter: AsyncRateLimiter | None = None,
) -> dict[str, Any] | None:
    """GET the live variation from OTTO. Returns None when the SKU does not exist on this account."""
    status, body = await _call_with_retry(
        lambda: client._request_with_status("GET", f"/v5/products/{sku}", controller=ctrl),
        f"GET {sku}",
        rate_limiter=rate_limiter,
    )
    if status == 404:
        return None
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"GET /v5/products/{sku} -> {status}: {str(body)[:200]}")
    return body


def _live_price(variation: dict[str, Any]) -> float | None:
    try:
        return float(variation["pricing"]["standardPrice"]["amount"])
    except (KeyError, TypeError, ValueError):
        return None


async def prepare_items(
    client: Any,
    ctrl: Controller,
    account: str,
    candidates: list[dict[str, Any]],
    concurrency: int = HTTP_CONCURRENCY,
    rate_limiter: AsyncRateLimiter | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[Any]]]:
    """Read live variations from OTTO and build upsert payloads with new .99 prices."""
    sem = asyncio.Semaphore(concurrency)
    skipped: dict[str, list[Any]] = {"not_found": [], "already_99": [], "no_price": [], "errors": []}
    ready: list[dict[str, Any]] = []

    async def work(cand: dict[str, Any]) -> None:
        sku = cand["sku"]
        async with sem:
            try:
                variation = await fetch_live_variation(client, ctrl, sku, rate_limiter=rate_limiter)
            except Exception as exc:
                skipped["errors"].append({"sku": sku, "error": str(exc)[:300]})
                return
        if variation is None:
            skipped["not_found"].append(sku)
            return
        live = _live_price(variation)
        if live is None or live <= 0:
            skipped["no_price"].append(sku)
            return
        if is_already_99(live):
            skipped["already_99"].append(sku)
            return
        new_price = calculate_99_price(live)
        variation["pricing"]["standardPrice"]["amount"] = new_price
        ready.append(
            {
                "sku": sku,
                "account": account,
                "old_price": live,
                "new_price": new_price,
                "name": str((variation.get("productDescription") or {}).get("productLine") or cand.get("name") or ""),
                "variation": variation,
            }
        )

    await asyncio.gather(*(work(c) for c in candidates))
    ready.sort(key=lambda it: it["sku"])
    return ready, skipped


def _task_id_from(resp: Any) -> str | None:
    if isinstance(resp, dict):
        for link in resp.get("links") or []:
            if link.get("rel") == "self":
                return str(link.get("href", "")).rstrip("/").split("/")[-1] or None
    return None


async def wait_for_task(
    client: Any,
    ctrl: Controller,
    task_id: str,
    rate_limiter: AsyncRateLimiter | None = None,
) -> dict[str, Any] | None:
    """Poll the update task until state == done. Returns the task dict or None on timeout."""
    deadline = time.monotonic() + TASK_TIMEOUT
    while time.monotonic() < deadline:
        await asyncio.sleep(TASK_POLL_INTERVAL)
        status, body = await _call_with_retry(
            lambda: client._request_with_status("GET", f"/v5/products/update-tasks/{task_id}", controller=ctrl),
            f"task {task_id}",
            rate_limiter=rate_limiter,
        )
        if status == 200 and isinstance(body, dict) and body.get("state") == "done":
            return body
    return None


async def read_task_results(
    client: Any,
    ctrl: Controller,
    task_id: str,
    rate_limiter: AsyncRateLimiter | None = None,
) -> tuple[set[str], dict[str, Any]]:
    """Return (succeeded SKUs, {failed SKU: errors}) from the task result endpoints."""
    succeeded: set[str] = set()
    failed: dict[str, Any] = {}
    status, body = await _call_with_retry(
        lambda: client._request_with_status(
            "GET", f"/v5/products/update-tasks/{task_id}/succeeded", controller=ctrl
        ),
        f"task {task_id} succeeded",
        rate_limiter=rate_limiter,
    )
    if status == 200 and isinstance(body, dict):
        for r in body.get("results") or []:
            succeeded.add(str(r.get("variation", "")).rstrip("/").split("/")[-1])
    status, body = await _call_with_retry(
        lambda: client._request_with_status(
            "GET", f"/v5/products/update-tasks/{task_id}/failed", controller=ctrl
        ),
        f"task {task_id} failed",
        rate_limiter=rate_limiter,
    )
    if status == 200 and isinstance(body, dict):
        for r in body.get("results") or []:
            sku = str(r.get("variation", "")).rstrip("/").split("/")[-1]
            failed[sku] = [
                {"code": e.get("code"), "title": e.get("title")} for e in (r.get("errors") or [])
            ]
    return succeeded, failed


async def submit_batch(
    client: Any,
    ctrl: Controller,
    batch: list[dict[str, Any]],
    label: str,
    rate_limiter: AsyncRateLimiter | None = None,
) -> dict[str, Any]:
    """Upsert one batch via POST /v5/products and return per-SKU outcome based on the OTTO task result."""
    payload = [it["variation"] for it in batch]
    status, resp = await _call_with_retry(
        lambda: client._request_with_status("POST", "/v5/products", json=payload, controller=ctrl),
        f"POST /v5/products {label}",
        rate_limiter=rate_limiter,
    )
    task_id = _task_id_from(resp)
    result: dict[str, Any] = {
        "label": label,
        "count": len(batch),
        "status_code": status,
        "task_id": task_id,
        "accepted": [],
        "failed": {},
        "unconfirmed": [],
    }
    skus = [it["sku"] for it in batch]
    if status not in (200, 202) or not task_id:
        logger.error("%s: POST rejected (%s): %s", label, status, str(resp)[:500])
        result["unconfirmed"] = skus
        result["error"] = f"POST status {status}: {str(resp)[:300]}"
        return result

    task = await wait_for_task(client, ctrl, task_id, rate_limiter=rate_limiter)
    if task is None:
        logger.error("%s: task %s did not finish within %ds", label, task_id, TASK_TIMEOUT)
        result["unconfirmed"] = skus
        result["error"] = "task timeout"
        return result

    if int(task.get("total", 0)) == 0:
        logger.error("%s: task %s returned total=0 (empty task / not processed)", label, task_id)
        result["unconfirmed"] = skus
        result["error"] = "task total is 0"
        return result

    succeeded, failed = await read_task_results(client, ctrl, task_id, rate_limiter=rate_limiter)
    result["accepted"] = [s for s in skus if s in succeeded]
    result["failed"] = {s: failed[s] for s in skus if s in failed}
    result["unconfirmed"] = [s for s in skus if s not in succeeded and s not in failed]
    logger.info(
        "%s: task %s done: succeeded=%d failed=%d unconfirmed=%d",
        label, task_id, len(result["accepted"]), len(result["failed"]), len(result["unconfirmed"]),
    )
    for sku, errs in list(result["failed"].items())[:3]:
        logger.warning("%s: FAILED %s: %s", label, sku, errs)
    return result


async def verify_canary_prices(
    client: Any,
    ctrl: Controller,
    sample_items: list[dict[str, Any]],
    rate_limiter: AsyncRateLimiter | None = None,
) -> bool:
    """Verify sample canary SKU(s) to confirm live OTTO price applied."""
    all_ok = True
    for it in sample_items:
        try:
            variation = await fetch_live_variation(client, ctrl, it["sku"], rate_limiter=rate_limiter)
            live = _live_price(variation) if variation else None
            if live is not None and abs(live - it["new_price"]) < 0.005:
                logger.info("Canary verification PASS: %s -> %.2f EUR", it["sku"], live)
            else:
                logger.error("Canary verification FAIL: %s expected %.2f EUR, got %s", it["sku"], it["new_price"], live)
                all_ok = False
        except Exception as exc:
            logger.warning("Canary check exception %s: %s", it["sku"], exc)
            all_ok = False
    return all_ok


async def verify_prices_all(
    client: Any,
    ctrl: Controller,
    items: list[dict[str, Any]],
    concurrency: int = HTTP_CONCURRENCY,
    rate_limiter: AsyncRateLimiter | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Re-read each SKU from OTTO and compare the live price with the expected one."""
    sem = asyncio.Semaphore(concurrency)
    verified: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []

    async def check(it: dict[str, Any]) -> None:
        live: float | None = None
        for attempt in range(VERIFY_ATTEMPTS):
            async with sem:
                try:
                    variation = await fetch_live_variation(client, ctrl, it["sku"], rate_limiter=rate_limiter)
                except Exception as exc:
                    logger.warning("verify %s: %s", it["sku"], exc)
                    variation = None
            live = _live_price(variation) if variation else None
            if live is not None and abs(live - it["new_price"]) < 0.005:
                verified.append(it)
                return
            if attempt < VERIFY_ATTEMPTS - 1:
                await asyncio.sleep(VERIFY_RETRY_DELAY)
        mismatched.append({"sku": it["sku"], "expected": it["new_price"], "live": live})

    await asyncio.gather(*(check(it) for it in items))
    return verified, mismatched


async def update_local_db_prices(account: str, items: list[dict[str, Any]]) -> int:
    """Update prices in PostgreSQL database for verified items."""
    if not items:
        return 0

    async with SessionLocal() as db:
        updated_import_rows = 0
        for it in items:
            res = await db.execute(
                update(OttoXlsxImportRow)
                .where(
                    func.lower(OttoXlsxImportRow.account) == account.lower(),
                    OttoXlsxImportRow.sku == it["sku"],
                )
                .values(price=it["new_price"])
            )
            updated_import_rows += res.rowcount or 0

            await db.execute(
                update(ProductVariant)
                .where(ProductVariant.sku == it["sku"])
                .values(price=it["new_price"])
            )
            await db.execute(
                update(Product)
                .where(Product.sku == it["sku"])
                .values(price=it["new_price"])
            )

        await db.commit()

    logger.info("[%s] Local DB updated: %d otto_xlsx_import_rows updated", account.upper(), updated_import_rows)
    return updated_import_rows


def _cap(values: list[Any]) -> list[Any]:
    return values[:REPORT_LIST_CAP]


async def process_account(
    account: str,
    args: argparse.Namespace,
    client: Any,
) -> dict[str, Any]:
    """Execute streaming price update pipeline for JV or XL."""
    ctrl = Controller.JV if account.lower() == "jv" else Controller.XL
    logger.info("================================================================")
    logger.info("  PROCESSING ACCOUNT: %s", account.upper())
    logger.info("================================================================")

    logger.info("[%s] Pre-warming OAuth token...", account.upper())
    await client.auth.get_token(ctrl.value)

    rate_limiter = AsyncRateLimiter(rate=args.rate_limit, burst=args.rate_limit)

    exclude_skus = load_exclude_skus(args.exclude_file, account)

    if args.sku:
        candidates = [{"sku": s, "name": ""} for s in dict.fromkeys(args.sku) if s not in exclude_skus]
        logger.info("[%s] Explicit SKU mode: %d SKU(s)", account.upper(), len(candidates))
    else:
        candidates = await fetch_candidates(
            account=account,
            active_only=not args.all_statuses,
            limit=args.limit,
            exclude_skus=exclude_skus,
        )

    if not candidates:
        logger.info("[%s] No items need price update.", account.upper())
        return {"account": account, "total_items": 0, "status": "no_items"}

    # Process in chunks (Streaming Chunking Pipeline)
    chunk_size = args.chunk_size
    total_candidates = len(candidates)
    chunks = [candidates[i : i + chunk_size] for i in range(0, total_candidates, chunk_size)]
    logger.info(
        "[%s] Pipeline: %d candidates in %d chunk(s) (chunk size: %d, batch size: %d, verify: %s, rate limit: %.1f/s)",
        account.upper(), total_candidates, len(chunks), chunk_size, args.batch_size, args.verify_mode, args.rate_limit,
    )

    total_verified = 0
    total_failed_by_otto = 0
    total_db_updated = 0
    total_mismatched = 0
    all_failed_details: dict[str, Any] = {}
    all_batches_summary: list[dict[str, Any]] = []

    for c_idx, chunk in enumerate(chunks, 1):
        chunk_label = f"[{account.upper()}] Chunk {c_idx}/{len(chunks)}"
        logger.info("%s: Reading %d live variations from OTTO...", chunk_label, len(chunk))

        items, skipped = await prepare_items(
            client=client,
            ctrl=ctrl,
            account=account,
            candidates=chunk,
            concurrency=args.concurrency,
            rate_limiter=rate_limiter,
        )

        logger.info(
            "%s: Live check -> to_update=%d | already_99=%d | not_found=%d | no_price=%d | errors=%d",
            chunk_label, len(items), len(skipped["already_99"]), len(skipped["not_found"]),
            len(skipped["no_price"]), len(skipped["errors"]),
        )

        if c_idx == 1 and items:
            logger.info("[%s] Preview of first price changes:", account.upper())
            for it in items[:10]:
                logger.info(
                    "  SKU: %-15s | LIVE old: %9.2f EUR -> New: %9.2f EUR | %s",
                    it["sku"], it["old_price"], it["new_price"], it["name"][:45],
                )

        if args.dry_run:
            logger.info("%s: DRY-RUN mode. Skipping OTTO writes.", chunk_label)
            total_verified += len(items)
            continue

        if not items:
            logger.info("%s: Nothing to update in this chunk.", chunk_label)
            continue

        # Split chunk items into batches
        batches = [items[i : i + args.batch_size] for i in range(0, len(items), args.batch_size)]
        by_sku = {it["sku"]: it for it in items}

        chunk_accepted: list[dict[str, Any]] = []
        chunk_failed: dict[str, Any] = {}

        for b_idx, batch in enumerate(batches, 1):
            batch_label = f"{chunk_label} Batch {b_idx}/{len(batches)}"
            try:
                res = await submit_batch(client, ctrl, batch, batch_label, rate_limiter=rate_limiter)
            except Exception as exc:
                logger.exception("%s: error %s", batch_label, exc)
                res = {"accepted": [], "failed": {}, "unconfirmed": [it["sku"] for it in batch]}

            chunk_accepted.extend(by_sku[s] for s in res["accepted"])
            chunk_failed.update(res["failed"])
            all_batches_summary.append({
                "label": batch_label, "count": len(batch),
                "accepted": len(res["accepted"]), "failed": len(res["failed"]),
            })
            if args.delay > 0:
                await asyncio.sleep(args.delay)

        # Verification step
        verified_items: list[dict[str, Any]] = []
        if chunk_accepted:
            if args.verify_mode == "canary":
                # Verify 1 random sample item per batch
                canary_samples = [batch[0] for batch in batches if batch and batch[0]["sku"] in [it["sku"] for it in chunk_accepted]]
                if canary_samples:
                    logger.info("%s: Running canary verification on %d sample SKU(s)...", chunk_label, len(canary_samples))
                    await asyncio.sleep(3)
                    await verify_canary_prices(client, ctrl, canary_samples, rate_limiter=rate_limiter)
                verified_items = chunk_accepted
            elif args.verify_mode == "all":
                logger.info("%s: Verifying all %d accepted items via live GET...", chunk_label, len(chunk_accepted))
                await asyncio.sleep(5)
                v_ok, v_mismatch = await verify_prices_all(client, ctrl, chunk_accepted, concurrency=args.concurrency, rate_limiter=rate_limiter)
                verified_items = v_ok
                total_mismatched += len(v_mismatch)
            else:  # none
                verified_items = chunk_accepted

        # Incrementally commit to local DB
        db_count = 0
        if not args.skip_db_update and verified_items:
            db_count = await update_local_db_prices(account, verified_items)
            total_db_updated += db_count

        total_verified += len(verified_items)
        total_failed_by_otto += len(chunk_failed)
        all_failed_details.update(chunk_failed)

        pct = (total_verified / total_candidates) * 100
        logger.info(
            "%s COMPLETE: updated=%d | failed=%d | db_saved=%d | Total Progress: %d/%d (%.1f%%)",
            chunk_label, len(verified_items), len(chunk_failed), db_count,
            total_verified, total_candidates, pct,
        )

    logger.info(
        "[%s] FINAL RESULT: updated_and_verified=%d | failed_by_otto=%d | mismatched=%d | db_updated=%d",
        account.upper(), total_verified, total_failed_by_otto, total_mismatched, total_db_updated,
    )

    return {
        "account": account,
        "candidates": total_candidates,
        "verified": total_verified,
        "failed_by_otto": total_failed_by_otto,
        "failed_details": dict(list(all_failed_details.items())[:REPORT_LIST_CAP]),
        "mismatched": total_mismatched,
        "db_updated": total_db_updated,
        "batches_processed": len(all_batches_summary),
        "dry_run": args.dry_run,
    }


async def main():
    parser = argparse.ArgumentParser(
        description="Update all OTTO product prices to end in ,99 EUR (e.g. 1999 -> 1999.99)"
    )
    parser.add_argument(
        "--account",
        choices=["jv", "xl", "both"],
        default="both",
        help="Account to update: jv, xl, or both (default: both)",
    )
    parser.add_argument(
        "--sku",
        nargs="+",
        default=None,
        help="Update only these SKUs (live price is read from OTTO; DB candidates are not used)",
    )
    parser.add_argument(
        "--exclude-file",
        type=str,
        default=None,
        help=f"Path to file with SKUs to exclude (auto-uses {DEFAULT_JV_EXCLUDE_FILE} for JV)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read live data from OTTO but send nothing and do not modify the DB",
    )
    parser.add_argument(
        "--all-statuses",
        action="store_true",
        help="Update products of all statuses (default: only active / online products)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE_DEFAULT,
        help=f"Variations per POST /v5/products batch (default: {BATCH_SIZE_DEFAULT})",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=CHUNK_SIZE_DEFAULT,
        help=f"Candidates per pipeline chunk (default: {CHUNK_SIZE_DEFAULT})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=HTTP_CONCURRENCY,
        help=f"Max concurrent GET requests (default: {HTTP_CONCURRENCY})",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=RATE_LIMIT_DEFAULT,
        help=f"Max HTTP requests per second to OTTO API (default: {RATE_LIMIT_DEFAULT} req/s, OTTO limit is 20/s)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DELAY_DEFAULT,
        help="Delay in seconds between batch requests (default: 0.3s)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of DB candidates per account (useful for initial test)",
    )
    parser.add_argument(
        "--verify-mode",
        choices=["canary", "all", "none"],
        default="canary",
        help="Verification mode: canary (1 per batch), all (100%%), or none (default: canary)",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Alias for --verify-mode none (skip live GET verification)",
    )
    parser.add_argument(
        "--skip-db-update",
        action="store_true",
        help="Skip updating local PostgreSQL database prices",
    )

    args = parser.parse_args()
    if args.no_verify:
        args.verify_mode = "none"

    accounts_to_run = ["jv", "xl"] if args.account == "both" else [args.account]

    log_banner = [
        "================================================================",
        "  STARTING OTTO PRICES UPDATE TO ,99 EUR",
        f"  Accounts: {', '.join(a.upper() for a in accounts_to_run)}",
        f"  Mode: {'DRY-RUN (live reads only)' if args.dry_run else 'LIVE STREAMING PIPELINE'}",
        f"  Scope: {'SKUs ' + ', '.join(args.sku) if args.sku else ('ALL statuses' if args.all_statuses else 'Active / Online only')}",
        f"  Batch size: {args.batch_size} | Chunk size: {args.chunk_size} | Concurrency: {args.concurrency} | Rate limit: {args.rate_limit}/s",
        f"  Verification: {args.verify_mode.upper()} | Limit: {args.limit or 'None'}",
        "================================================================",
    ]
    for line in log_banner:
        logger.info(line)

    client = get_otto_client()
    overall_report: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "accounts": accounts_to_run,
        "results": {},
    }

    try:
        for acc in accounts_to_run:
            res = await process_account(account=acc, args=args, client=client)
            overall_report["results"][acc] = res

        # Save report
        report_file = f"logs/price_update_99_report_{int(time.time())}.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(overall_report, f, ensure_ascii=False, indent=2, default=str)
        logger.info("Saved final execution report to %s", report_file)

    finally:
        if hasattr(client, "aclose"):
            await client.aclose()
        elif hasattr(client, "close"):
            await client.close()
        await engine.dispose()
        logger.info("Price update process completed.")


if __name__ == "__main__":
    asyncio.run(main())
