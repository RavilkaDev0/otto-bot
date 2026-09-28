import asyncio
import sys
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("remove_single")

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.schemas.external_schemes.until_schemes import GetProductRequest

SKU = "4071489323306"
BAD_URL = "https://moebelchesterfield.de/otto-xl/NACHTTISCH/4071489323306/4071489323306_4.jpg"

async def main():
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
    )
    otto = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)

    log.info("Fetching SKU %s from OTTO XL...", SKU)
    res = await otto.get_products(GetProductRequest(sku=SKU), "xl")
    variations = res.get("productVariations", [])
    if not variations:
        log.error("SKU %s not found in OTTO XL!", SKU)
        return

    v = variations[0]
    title = v.get("productDescription", {}).get("productLine")
    log.info("Found product: %s", title)

    media_assets = v.get("mediaAssets", [])
    log.info("Current mediaAssets count: %d", len(media_assets))

    cleaned_assets = []
    removed_assets = []
    for a in media_assets:
        loc = a.get("location") or a.get("url")
        if loc == BAD_URL or "4071489323306_4.jpg" in str(loc):
            removed_assets.append(a)
            log.info("Removing asset: %s", loc)
        else:
            cleaned_assets.append(a)

    if not removed_assets:
        log.warning("Bad asset was not found in mediaAssets. Already removed?")
        return

    payload = dict(v)
    payload["mediaAssets"] = cleaned_assets

    log.info("Sending update payload to OTTO XL API...")
    update_res = await otto.create_or_update_product([payload], "xl")
    log.info("Update response: %s", update_res)

    log.info("Waiting 2s and verifying...")
    await asyncio.sleep(2)
    verify_res = await otto.get_products(GetProductRequest(sku=SKU), "xl")
    v_new = verify_res.get("productVariations", [])[0]
    remaining_imgs = [a.get("location") or a.get("url") for a in v_new.get("mediaAssets", []) if a.get("type") == "IMAGE"]
    log.info("Remaining images count: %d", len(remaining_imgs))
    for idx, u in enumerate(remaining_imgs, 1):
        log.info("  [%d] %s", idx, u)

    if BAD_URL not in remaining_imgs and not any("4071489323306_4.jpg" in str(u) for u in remaining_imgs):
        log.info("SUCCESS: Mirrored logo successfully deleted from OTTO API for SKU %s!", SKU)
    else:
        log.error("FAILURE: Logo is still present after update.")

if __name__ == "__main__":
    asyncio.run(main())
