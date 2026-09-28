import asyncio
import random
from app.database import SessionLocal
from sqlalchemy import select
from app.models.models import OttoXlsxImportRow, Factories
from app.schemas.enums import Controller

async def main():
    async with SessionLocal() as db:
        # Get all TR factories
        factories_result = await db.execute(select(Factories).where(Factories.name.like("%(TR)%")))
        factories = factories_result.scalars().all()
        
        # Pick 10 random TR factories
        random_factories = random.sample(factories, min(10, len(factories)))
        
        from app.clients.afterbuy_client import AfterbuyClient
        from app.core.configs import settings
        from app.core.afterbuy_auth import AfterbuyAuth
        from app.services.afterbuy_service import AfterbuyService
        
        auth = AfterbuyAuth(username=settings.afterbuy_username, password=settings.afterbuy_password)
        client = AfterbuyClient(auth=auth, base_url=settings.afterbuy_base_url)
        afterbuy = AfterbuyService(auth=auth, client=client)
        
        print("Fetching data...")
        for factory in random_factories:
            try:
                resp = await afterbuy.get_products_by_factory_id(controller=Controller.JV, factory_id=int(factory.factory_id))
                
                # Get EANs for these products
                eans = []
                for item in resp.products:
                    ean = getattr(item, 'EAN', None) or item.model_dump().get('EAN')
                    if ean:
                        eans.append(str(ean).strip())
                
                # Check if they are online
                online_eans = []
                if eans:
                    rows = await db.execute(select(OttoXlsxImportRow.ean).where(
                        OttoXlsxImportRow.account == "jv",
                        OttoXlsxImportRow.marketplace_status == "Online",
                        OttoXlsxImportRow.ean.in_(eans)
                    ))
                    online_eans = [r[0] for r in rows.all() if r[0]]
                
                if len(online_eans) >= 3:
                    selected = random.sample(online_eans, 3)
                    print(f"\nФабрика: {factory.name}")
                    for e in selected:
                        print(f"  - {e}")
                elif online_eans:
                    print(f"\nФабрика: {factory.name} (найдено меньше 3)")
                    for e in online_eans:
                        print(f"  - {e}")
                        
            except Exception as e:
                pass

if __name__ == "__main__":
    asyncio.run(main())
