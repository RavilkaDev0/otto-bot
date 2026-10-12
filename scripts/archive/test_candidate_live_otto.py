import asyncio
import json
import sys
import os

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.clients.external_otto_client import ExternalOttoClient
from app.schemas.external_schemes.until_schemes import GetProductRequest

async def test_folders():
    with open("remaining_unupdated_tr_all.json") as f:
        data = json.load(f)

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

    # Check top 20 candidate folders
    folders_to_test = [
        "RomanDariya", "NEW_Person_4_Xenia", "NEW_Person_015_Karina", "NEW_Person_021_Darya_Topor",
        "NEW_Person_046_Nurai", "NEW_FAVORY_SPECIAL", "Brezza_(TR)", "Sandalie_(TR)",
        "NEW_DEPOTUM", "NEW_Person_013_Danil_B", "NEW_SERHATHOME_SPECIAL_AINUR", "NEW_Kirill_Melnikov"
    ]

    for d in data:
        fname = d["factory_name"]
        if fname in folders_to_test:
            sample = d["all_items"][:4]
            found = 0
            found_eans = []
            for it in sample:
                sku = it["ean"]
                try:
                    res = await otto.get_products(GetProductRequest(sku=sku), "jv")
                    if res.get("productVariations"):
                        found += 1
                        found_eans.append(sku)
                except Exception:
                    pass
            print(f"Folder: {fname} (total {d['count']}) -> Found on OTTO JV: {found}/{len(sample)} | EANs: {found_eans}")

if __name__ == "__main__":
    asyncio.run(test_folders())
