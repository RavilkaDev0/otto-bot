"""FastAPI application entrypoint and top-level route registration."""

import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.otto_exceptions import (
    OttoApiException,
    OttoRateLimitError,
)

from app.core.configs import settings
from app.api.routes.afterbuy import router as afterbuy_router
from app.api.routes.auth import router as auth_router
from app.api.routes.products import otto_v5_router, router as products_router
from app.api.routes.uploads import router as uploads_router
from app.api.routes.external_api import router as external_api_router
from app.core.configs import settings
from app.core.logger import logging
from app.core.sentry import init_sentry
from app.infrastructure.rabbitmq_publisher import RabbitMQPublisher

logger = logging.getLogger(__name__)

init_sentry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    publisher = RabbitMQPublisher(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        login=settings.rabbitmq_app_user,
        password=settings.rabbitmq_app_password,
        virtual_host=settings.rabbitmq_virtual_host,
        queue="otto.jobs"
    )
    
    await publisher.connect()
    app.state.rabbitmq_publisher = publisher

    try:
        yield
    finally:
        await publisher.close()
        try:
            from app.dependencies import get_external_otto_client
            await get_external_otto_client().close()
        except Exception:
            logger.exception("Error closing external OTTO client")

app = FastAPI(title="FastAPI Template", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    logger.warning(
        "Request validation failed: method=%s path=%s errors=%s",
        request.method,
        request.url.path,
        exc.errors(),
    )
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(OttoRateLimitError)
async def otto_rate_limit_exception_handler(
    request: Request,
    exc: OttoRateLimitError,
) -> JSONResponse:
    headers: dict[str, str] = {}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(int(exc.retry_after))
    logger.warning(
        "OTTO Rate limit exceeded: method=%s path=%s endpoint=%s controller=%s retry_after=%s",
        request.method,
        request.url.path,
        exc.endpoint,
        exc.controller,
        exc.retry_after,
    )
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "detail": "OTTO API rate limit exceeded (HTTP 429). Please retry later.",
            "error": "rate_limit_exceeded",
            "controller": exc.controller,
            "endpoint": exc.endpoint,
            "retry_after": exc.retry_after,
        },
        headers=headers if headers else None,
    )


@app.exception_handler(OttoApiException)
async def otto_api_exception_handler(
    request: Request,
    exc: OttoApiException,
) -> JSONResponse:
    logger.error(
        "OTTO API error: method=%s path=%s status=%s endpoint=%s controller=%s error=%s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.endpoint,
        exc.controller,
        str(exc),
    )
    status_code = (
        exc.status_code
        if exc.status_code in (400, 404, 409, 422, 429)
        else status.HTTP_502_BAD_GATEWAY
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": f"OTTO API error: {str(exc)}",
            "endpoint": exc.endpoint,
            "controller": exc.controller,
            "otto_status_code": exc.status_code,
        },
    )


app.include_router(external_api_router)
app.include_router(auth_router)
app.include_router(afterbuy_router)
app.include_router(products_router)
app.include_router(otto_v5_router)
app.include_router(uploads_router)

uploads_dir = Path(os.getenv("UPLOADS_DIR", "storage/uploads"))
uploads_dir.mkdir(parents=True, exist_ok=True)

app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

generated_media_dir = Path(settings.generated_media_root)
generated_media_dir.mkdir(parents=True, exist_ok=True)
app.mount(
    settings.generated_media_url_prefix,
    StaticFiles(directory=generated_media_dir),
    name="generated-media",
)


@app.get("/health")
async def health_check():
    """Simple liveness endpoint used by uptime checks and deployments."""
    return {"status": "ok"}
