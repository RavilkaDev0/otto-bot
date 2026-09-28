import asyncio
import os
import sys

# Настраиваем пути, чтобы можно было импортировать модули из app/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.configs import settings
from app.core.user_auth import UserAuth
from app.database import SessionLocal
from app.repository.user_repository import UserRepository
from app.schemas.enums import RoleEnum

async def main():
    email = "atambekshamilov07@gmail.com"
    password = "ata123456"

    print(f"Попытка создания SEO пользователя: {email} / {password}")

    async with SessionLocal() as session:
        repo = UserRepository(session)
        auth = UserAuth(
            user_repository=repo,
            secret_key=settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
            access_token_expire_minutes=settings.jwt_access_token_expire_minutes
        )

        try:
            user = await auth._create_user_account(
                name="Atambek",
                last_name="Shamilov",
                email=email,
                password=password,
                role=RoleEnum.SEO
            )
            await session.commit()
            print("Успешно создано!")
            print(f"Email: {user.email}")
            print(f"Role: {user.role}")
        except Exception as e:
            print(f"Ошибка при создании: {e}")

if __name__ == "__main__":
    asyncio.run(main())
