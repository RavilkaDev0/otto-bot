import asyncio
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
    
    for ctrl in ["jv", "xl"]:
        print(f"--- Shipping Profiles for {ctrl.upper()} ---")
        try:
            profs = await otto.get_shipping_profiles(ctrl)
            items = profs.get("results", []) if isinstance(profs, dict) else profs
            for p in items:
                if isinstance(p, dict):
                    print(f"  ID: {p.get('shippingProfileId')} | Name: {p.get('name')} | Default: {p.get('defaultProfile')}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  Raw profs output: {profs if 'profs' in locals() else 'N/A'}")

if __name__ == "__main__":
    asyncio.run(check())
