import asyncio
import sys
import json
import logging
import time
import httpx
import hashlib

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("cleaner_v4")

sys.path.insert(0, "/app")
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.schemas.external_schemes.until_schemes import GetProductRequest

TARGET_MD5S = {"0afd3bdf6e522b09848f786d16912a99"}
try:
    with open("/app/temp/new_target_md5s.txt") as f:
        for line in f:
            m = line.strip()
            if m: TARGET_MD5S.add(m)
except: pass

with open("/app/temp/affected_xl_jv_logos_v4.json") as f:
    data = json.load(f)

pending_skus = [a["sku"] for a in data.get("affected", [])]
log.info("Found %d pending SKUs to process.", len(pending_skus))

async def process_image(url, client):
    try:
        resp = await client.get(url, timeout=10.0, follow_redirects=True)
        if resp.status_code != 200:
            return None
        return hashlib.md5(resp.content).hexdigest()
    except Exception as e:
        return None

async def main():
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id, client_secret=settings.otto_jv_client_secret,
        xl_client_id=settings.otto_xl_client_id, xl_client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url, scope=settings.otto_scope, timeout=settings.otto_timeout_seconds,
    )
    otto = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)
    
    async with httpx.AsyncClient() as http:
        for i, sku in enumerate(pending_skus):
            log.info("[%d/%d] Processing SKU=%s", i+1, len(pending_skus), sku)
            result = await otto.get_products(GetProductRequest(sku=sku), "xl")
            variation = None
            for v in result.get("productVariations", []):
                if str(v.get("sku")) == str(sku):
                    variation = v
                    break
            
            if not variation: continue
                
            media_assets = variation.get("mediaAssets", [])
            cleaned = []
            removed = []
            
            for asset in media_assets:
                if asset.get("type") != "IMAGE":
                    cleaned.append(asset)
                    continue
                    
                loc = asset.get("location") or asset.get("url")
                if not loc:
                    cleaned.append(asset)
                    continue
                    
                md5 = await process_image(loc, http)
                if not md5:
                    cleaned.append(asset)
                    continue
                    
                if md5 in TARGET_MD5S:
                    removed.append(asset)
                    log.info("  -> REMOVING: %s", loc)
                else:
                    cleaned.append(asset)
                    
            if not removed:
                log.info("SKU=%s: No bad images found to remove", sku)
                continue
                
            remaining_images = [a for a in cleaned if a.get("type") == "IMAGE"]
            if not remaining_images:
                log.warning("SKU=%s: No images would be left! Skipping.", sku)
                continue
                
            payload = dict(variation)
            payload["mediaAssets"] = cleaned
            
            try:
                await otto.create_or_update_product([payload], "xl")
                log.info("SKU=%s: SUCCESSFULLY UPDATED. Removed %d images, %d remaining.", sku, len(removed), len(remaining_images))
            except Exception as e:
                log.error("SKU=%s: Error updating product: %s", sku, e)
            
            time.sleep(1)

asyncio.run(main())
