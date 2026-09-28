import asyncio
import json
import logging
import os
import sys
import time

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from sqlalchemy import select, update
from app.database import SessionLocal, engine
from app.dependencies import get_otto_client
from app.schemas.enums import Controller
from app.models.product_variants import ProductVariant
from app.models.products import Product
from app.models.otto_xlsx_import import OttoXlsxImportRow

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("price_updater")

PRICES_FILE = "/app/logs/aftercool_factory_prices.json"
RESULTS_FILE = "/app/logs/price_update_result_3_factories.json"
BATCH_SIZE = 100

async def main():
    logger.info("Loading Aftercool prices from %s", PRICES_FILE)
    with open(PRICES_FILE, "r", encoding="utf-8") as f:
        factories_data = json.load(f)

    # Flatten all items by EAN
    items_by_ean = {}
    for fact_id, fact_info in factories_data.items():
        for ean, item in fact_info.get("items", {}).items():
            items_by_ean[ean] = item

    logger.info("Loaded %d unique items from Aftercool", len(items_by_ean))

    # Match with DB to get accurate SKU and local IDs
    logger.info("Matching items with local database...")
    matched_items = []
    
    # Process DB matching in chunks of 500 to avoid large SQL parameter sets
    eans_list = list(items_by_ean.keys())
    db_sku_map = {}
    
    async with SessionLocal() as db:
        for i in range(0, len(eans_list), 500):
            chunk = eans_list[i:i + 500]
            stmt = select(OttoXlsxImportRow.ean, OttoXlsxImportRow.sku).where(
                OttoXlsxImportRow.account == "jv",
                OttoXlsxImportRow.ean.in_(chunk)
            )
            res = await db.execute(stmt)
            for row in res.fetchall():
                db_sku_map[str(row[0])] = str(row[1])

    logger.info("Found %d EANs in local otto_xlsx_import_rows for JV", len(db_sku_map))

    # Build payload items
    payload_items = []
    for ean, item in items_by_ean.items():
        sku = db_sku_map.get(ean, ean) # fallback to ean if not in DB
        price = item["price"]
        uvp = item["uvp"]
        
        payload_items.append({
            "sku": sku,
            "ean": ean,
            "price": price,
            "uvp": uvp,
            "factory_id": item["factory_id"],
            "factory_name": item["factory_name"],
            "otto_payload": {
                "sku": sku,
                "standardPrice": {
                    "amount": price,
                    "currency": "EUR"
                },
                "vat": "FULL",
                "msrp": {
                    "amount": uvp,
                    "currency": "EUR"
                }
            }
        })

    logger.info("Prepared %d price update payloads", len(payload_items))

    # Send to OTTO JV in batches
    client = get_otto_client()
    batches = [payload_items[i:i + BATCH_SIZE] for i in range(0, len(payload_items), BATCH_SIZE)]
    logger.info("Total batches to send to OTTO: %d (batch size = %d)", len(batches), BATCH_SIZE)

    batch_results = []
    task_ids = []

    for b_idx, batch in enumerate(batches, 1):
        otto_batch_payload = [it["otto_payload"] for it in batch]
        try:
            status_code, resp = await client.update_prices_with_status(
                otto_batch_payload,
                controller=Controller.JV
            )
            
            task_id = None
            if isinstance(resp, dict) and resp.get("links"):
                for l in resp["links"]:
                    if l.get("rel") == "self":
                        task_id = l.get("href", "").split("/")[-1]
                        break

            if status_code == 202:
                logger.info(
                    "Batch %d/%d (items %d-%d) ACCEPTED (202), task_id=%s",
                    b_idx, len(batches), (b_idx - 1) * BATCH_SIZE + 1, min(b_idx * BATCH_SIZE, len(payload_items)), task_id
                )
                if task_id:
                    task_ids.append(task_id)
            else:
                logger.warning(
                    "Batch %d/%d returned status %d: %s",
                    b_idx, len(batches), status_code, resp
                )

            batch_results.append({
                "batch_index": b_idx,
                "count": len(batch),
                "status_code": status_code,
                "task_id": task_id,
                "response": resp
            })
        except Exception as e:
            logger.exception("Error sending batch %d/%d: %s", b_idx, len(batches), e)
            batch_results.append({
                "batch_index": b_idx,
                "count": len(batch),
                "error": str(e)
            })

        await asyncio.sleep(0.3)

    logger.info("All %d batches sent to OTTO. Waiting for tasks to complete...", len(batches))
    await asyncio.sleep(5)

    # Check task progress
    task_summaries = {"succeeded": 0, "failed": 0, "unchanged": 0, "pending": 0}
    for t_idx, tid in enumerate(task_ids, 1):
        try:
            code, t_resp = await client._request_with_status(
                "GET", f"/v5/products/update-tasks/{tid}", controller=Controller.JV
            )
            if isinstance(t_resp, dict):
                task_summaries["succeeded"] += t_resp.get("succeeded", 0)
                task_summaries["failed"] += t_resp.get("failed", 0)
                task_summaries["unchanged"] += t_resp.get("unchanged", 0)
                if t_resp.get("state") != "done":
                    task_summaries["pending"] += 1
            if t_idx % 10 == 0 or t_idx == len(task_ids):
                logger.info("Checked task status %d/%d: current totals = %s", t_idx, len(task_ids), task_summaries)
        except Exception as e:
            logger.warning("Failed to check task %s: %s", tid, e)
        await asyncio.sleep(0.1)

    logger.info("================ FINAL SUMMARY ================")
    logger.info("Total items processed: %d", len(payload_items))
    logger.info("OTTO task results: %s", task_summaries)
    logger.info("===============================================")

    # Update local DB rows
    logger.info("Updating local database otto_xlsx_import_rows with new prices and UVP...")
    updated_db_count = 0
    async with SessionLocal() as db:
        for it in payload_items:
            if it["ean"] in db_sku_map:
                await db.execute(
                    update(OttoXlsxImportRow)
                    .where(
                        OttoXlsxImportRow.account == "jv",
                        OttoXlsxImportRow.ean == it["ean"]
                    )
                    .values(
                        price=it["price"],
                    )
                )
                updated_db_count += 1
        await db.commit()
    logger.info("Updated %d rows in local DB", updated_db_count)

    # Save output report
    final_report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_items": len(payload_items),
        "total_batches": len(batches),
        "task_summaries": task_summaries,
        "db_updated_count": updated_db_count,
        "batch_results": batch_results,
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(final_report, f, ensure_ascii=False, indent=2)
    logger.info("Saved final execution report to %s", RESULTS_FILE)

    await client.close()
    await engine.dispose()
    logger.info("Price update completed successfully!")

if __name__ == "__main__":
    asyncio.run(main())
