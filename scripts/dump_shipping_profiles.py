import asyncio
import json
import sys
if "/app" not in sys.path:
    sys.path.insert(0, "/app")
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.clients.external_otto_client import ExternalOttoClient

async def check():
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
    for ctrl in ["xl", "jv"]:
        print(f"\n=================== PROFILES FOR {ctrl.upper()} ===================")
        profs = await otto.get_shipping_profiles(ctrl)
        print(json.dumps(profs, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(check())
