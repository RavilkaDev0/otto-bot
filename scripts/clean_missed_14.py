import asyncio
import sys
import json
import logging
import time
import httpx
import hashlib
from io import BytesIO
from PIL import Image
import imagehash
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("cleaner_missed")

sys.path.insert(0, "/app")
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.schemas.external_schemes.until_schemes import GetProductRequest

KNOWN_LOGO_MD5 = "0afd3bdf6e522b09848f786d16912a99"

TARGET_PHASHES = []
target_dir = "/app/temp/targets/"
for fname in os.listdir(target_dir):
    if fname.endswith(".png"):
        path = os.path.join(target_dir, fname)
        img = Image.open(path)
        h = imagehash.phash(img)
        TARGET_PHASHES.append((fname, h))
        log.info("Target %s loaded, phash: %s", fname, h)

pending_skus = [
    "4071489324143",
    "4071489327595", "4071489327601", "4071489327588", "4071489327571", 
    "4071489327618", "4071489327632", "4071489327625", "4071489327656", 
    "4071489327687", "4071489327649", "4071489327670", "4071489327694", 
    "4071489327663"
]

async def process_image(url, client):
    try:
        resp = await client.get(url, timeout=15.0, follow_redirects=True)
        if resp.status_code != 200: return None, None
        content = resp.content
        md5 = hashlib.md5(content).hexdigest()
        img = Image.open(BytesIO(content))
        h = imagehash.phash(img)
        return md5, h
    except Exception as e:
        return None, None

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
                    
                md5, phash = await process_image(loc, http)
                if not md5:
                    cleaned.append(asset)
                    continue
                    
                should_remove = False
                if md5 == KNOWN_LOGO_MD5:
                    should_remove = True
                    log.info("  -> REMOVING (MD5): %s", loc)
                
                if not should_remove:
                    for t_name, t_hash in TARGET_PHASHES:
                        dist = phash - t_hash
                        if dist <= 12:
                            should_remove = True
                            log.info("  -> REMOVING (pHash dist=%d to %s): %s", dist, t_name, loc)
                            break
                            
                if should_remove:
                    removed.append(asset)
                else:
                    cleaned.append(asset)
                    
            if not removed:
                log.info("SKU=%s: No bad images found", sku)
                continue
                
            remaining_images = [a for a in cleaned if a.get("type") == "IMAGE"]
            if not remaining_images:
                log.warning("SKU=%s: No images left! Skipping.", sku)
                continue
                
            payload = dict(variation)
            payload["mediaAssets"] = cleaned
            
            try:
                await otto.create_or_update_product([payload], "xl")
                log.info("SKU=%s: SUCCESSFULLY UPDATED. Removed %d images, %d remaining.", sku, len(removed), len(remaining_images))
            except Exception as e:
                log.error("SKU=%s: Error updating: %s", sku, e)
            
            time.sleep(1)

asyncio.run(main())
