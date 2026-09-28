"""Dependency factories shared by FastAPI route handlers.

`lru_cache` ensures these lightweight service objects are reused across requests
instead of being recreated for each injection.
"""

from functools import lru_cache

from fastapi import Request
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.afterbuy_client import AfterbuyClient
from app.clients.otto_client import OttoClient
from app.core.afterbuy_auth import AfterbuyAuth
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.core.user_auth import UserAuth
from app.database import get_db
from app.repository.user_repository import UserRepository
from app.schemas.enums import RoleEnum
from app.schemas.userDTO import UserDTO
from app.services.afterbuy_service import AfterbuyService
from app.services.product_service import ProductService
from app.services.extermal_service import ExternalService
from app.clients.external_otto_client import ExternalOttoClient
from app.repository.external_api_repository import ExternalApiRepository
from app.infrastructure.rabbitmq_publisher import RabbitMQPublisher

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login")


@lru_cache
def get_otto_auth() -> OttoAuth:
    """Create a cached OTTO auth helper configured from environment settings."""
    return OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
    )


@lru_cache
def get_otto_client() -> OttoClient:
    """Create a cached OTTO HTTP client instance."""
    return OttoClient(
        auth=get_otto_auth(),
        base_url=settings.otto_base_url,
        timeout=settings.otto_timeout_seconds,
    )


@lru_cache
def get_external_otto_client() -> ExternalOttoClient:

    return ExternalOttoClient(auth=get_otto_auth(), base_url=settings.otto_base_url)


@lru_cache
def get_product_service() -> ProductService:
    """Create a cached product service wrapper."""
    return ProductService(client=get_otto_client())


@lru_cache
def get_external_api_service() -> ExternalService:

    return ExternalService(client=get_external_otto_client())


async def get_external_repository(
    session: AsyncSession = Depends(get_db),
) -> ExternalApiRepository:
    return ExternalApiRepository(session)


@lru_cache
def get_afterbuy_auth() -> AfterbuyAuth:
    """Create a cached Afterbuy auth/client helper."""
    return AfterbuyAuth(
        username=settings.afterbuy_username,
        password=settings.afterbuy_password,
        base_url=settings.afterbuy_base_url,
        timeout=settings.afterbuy_timeout_seconds,
    )


@lru_cache
def get_afterbuy_client() -> AfterbuyClient:
    """Create a cached Afterbuy HTTP client instance."""
    return AfterbuyClient(
        base_url=settings.afterbuy_base_url,
        timeout=settings.afterbuy_timeout_seconds,
    )


def get_user_repository(
    db: AsyncSession = Depends(get_db),
) -> UserRepository:
    return UserRepository(db=db)


def get_user_auth(
    user_repository: UserRepository = Depends(get_user_repository),
) -> UserAuth:
    return UserAuth(
        user_repository=user_repository,
        secret_key=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        access_token_expire_minutes=settings.jwt_access_token_expire_minutes,
    )


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    auth_service: UserAuth = Depends(get_user_auth),
) -> UserDTO:
    """Resolve the current authenticated user from the bearer token."""
    return await auth_service.get_current_user(token)


def require_role(allowed_roles: list[str] | list[RoleEnum]):
    allowed_role_values = {
        role.value if isinstance(role, RoleEnum) else role for role in allowed_roles
    }

    async def role_checker(
        current_user: UserDTO = Depends(get_current_user),
    ) -> UserDTO:
        user_role = current_user.role.value if current_user.role else None
        if user_role not in allowed_role_values:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource",
            )
        return current_user

    return role_checker


@lru_cache
def get_afterbuy_service() -> AfterbuyService:
    """Create a cached Afterbuy service wrapper."""
    return AfterbuyService(auth=get_afterbuy_auth(), client=get_afterbuy_client())


def get_afterbuy_login() -> AfterbuyService:
    """Backward-compatible dependency name for Afterbuy access."""
    return get_afterbuy_service()


def get_rabbitmq_publisher(request: Request) -> RabbitMQPublisher:
    return request.app.state.rabbitmq_publisher