import asyncio
import sys
import os
import urllib.request
sys.path.insert(0, "/app")
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.schemas.external_schemes.until_schemes import GetProductRequest

async def main():
    auth = OttoAuth(client_id=settings.otto_jv_client_id, client_secret=settings.otto_jv_client_secret, xl_client_id=settings.otto_xl_client_id, xl_client_secret=settings.otto_xl_client_secret, base_url=settings.otto_base_url, scope=settings.otto_scope, timeout=settings.otto_timeout_seconds)
    otto = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)
    res = await otto.get_products(GetProductRequest(productReference="S05K107B"), "xl")
    
    out_dir = "/app/temp/s05k107b_current"
    os.makedirs(out_dir, exist_ok=True)
    
    for v in res.get("productVariations", []):
        sku = v.get("sku")
        imgs = [a.get("location") or a.get("url") for a in v.get("mediaAssets", []) if a.get("type") == "IMAGE"]
        for i, img in enumerate(imgs):
            if "otto-xl" in img:
                out_path = f"{out_dir}/{sku}_{i}.jpg"
                try:
                    urllib.request.urlretrieve(img, out_path)
                    print(f"Downloaded {sku}_{i}.jpg")
                except Exception as e:
                    print(f"Failed {img}: {e}")
asyncio.run(main())
