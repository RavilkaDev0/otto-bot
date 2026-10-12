from app.schemas.external_schemes.until_schemes import (
    ActiveStatusByEanRequest,
    ActiveStatusByEanResponse,
    CreateOrUpdateProductVariationRequest,
    CreateOrUpdateProductResponse,
    JobStatusResponse,
    GetProductRequest,
    ShippingProfileResponse,
)
from app.schemas.product_query import CategoryQuery
from app.schemas.product_response import (
    ExternalCategoriesResponse,
    ExternalCategoryAttributesResponse,
)
from app.dependencies import (
    get_external_api_service,
    get_rabbitmq_publisher,
)
from app.database import get_db
from app.core.logger import format_log_payload
from app.services.extermal_service import ExternalService
from app.schemas.enums import Controller
from app.infrastructure.rabbitmq_publisher import RabbitMQPublisher

from fastapi import APIRouter, Query, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

import logging


logger = logging.getLogger("external_api")

router = APIRouter(prefix="/extermal", tags=["external"])


@router.get("/get_products")
async def get_products(
    sku: str | None = Query(
        default=None,
        min_length=1,
        max_length=50,
        description="Уникальный идентификатор продукта. Если указан, возвращается 1 продукт",
    ),
    product_reference: str | None = Query(
        default=None,
        alias="productReference",
        description="Идентификатор для вариации продукта",
    ),
    category: str | None = Query(default=None, description="Категория продукта"),
    ean: str | None = Query(
        default=None,
        min_length=8,
        max_length=13,
        description="EAN продукта",
    ),
    page: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1),
    controller: Controller = Query(
        default=Controller.JV,  
        description="На каком аккаунте операция будет происходить",
    ),
    service: ExternalService = Depends(get_external_api_service),
):
    logger.info("Endpoint accepted: GET /extermal/get_products")
    payload = GetProductRequest(
        sku=sku,
        productReference=product_reference,
        ean=ean,
        category=category,
        page=page,
        limit=limit,
    )
    
    logger.info("GET /extermal/get_products payload:\n%s", format_log_payload(payload))
    data = await service.get_products(payload, controller)

    logger.info("Endpoint completed: GET /extermal/get_products")
    
    return data


@router.post(
    "/create_or_update_product",
    response_model=CreateOrUpdateProductResponse,
    summary="Create or update product variations in OTTO",
    description="Submits product variations to OTTO. Returns job_id for asynchronous status tracking.",
)
async def create_or_update_product(
    payload: Annotated[CreateOrUpdateProductVariationRequest, Body()],
    controller: Annotated[Controller, Query(description="OTTO account (jv or xl)")] = Controller.JV,
    wait_for_completion: Annotated[bool, Query(description="Wait for OTTO processing completion")] = False,
    service: ExternalService = Depends(get_external_api_service),
    publisher: RabbitMQPublisher = Depends(get_rabbitmq_publisher),
):
    logger.info("Endpoint accepted: POST /extermal/create_or_update_product (wait=%s)", wait_for_completion)
    logger.info(
        "POST /extermal/create_or_update_product payload:\n%s",
        format_log_payload(payload),
    )
    data = await service.create_or_update_product(
        payload, controller, publisher, wait_for_completion=wait_for_completion
    )
    logger.info("Endpoint completed: POST /extermal/create_or_update_product")
    return data


@router.get(
    "/job_status/{job_id}",
    response_model=JobStatusResponse,
    summary="Get OTTO product upload job status",
    description="Fetches live processing status of a product upload task from OTTO by job_id, including failure details if any.",
)
@router.get("/job/{job_id}", response_model=JobStatusResponse, include_in_schema=False)
@router.get("/task_status/{job_id}", response_model=JobStatusResponse, include_in_schema=False)
async def get_job_status(
    job_id: str,
    controller: Annotated[Controller, Query(description="OTTO account (jv or xl)")] = Controller.JV,
    service: ExternalService = Depends(get_external_api_service),
):
    logger.info("Endpoint accepted: GET /extermal/job_status/%s controller=%s", job_id, controller)
    data = await service.get_job_status(job_id, controller)
    logger.info("Endpoint completed: GET /extermal/job_status/%s status=%s", job_id, data.get("state"))
    return data


@router.post("/activate", response_model=ActiveStatusByEanResponse)
async def activate_product_by_ean(
    payload: Annotated[ActiveStatusByEanRequest, Body()],
    service: ExternalService = Depends(get_external_api_service),
    db: AsyncSession | None = Depends(get_db),
):
    session = db if isinstance(db, AsyncSession) else None
    if session is not None:
        return await service.set_active_status_by_ean(payload, active=True, session=session)
    return await service.set_active_status_by_ean(payload, active=True)


@router.post("/deactivate", response_model=ActiveStatusByEanResponse)
async def deactivate_product_by_ean(
    payload: Annotated[ActiveStatusByEanRequest, Body()],
    service: ExternalService = Depends(get_external_api_service),
    db: AsyncSession | None = Depends(get_db),
):
    session = db if isinstance(db, AsyncSession) else None
    if session is not None:
        return await service.set_active_status_by_ean(payload, active=False, session=session)
    return await service.set_active_status_by_ean(payload, active=False)


@router.get("/shipping_profiles", response_model=ShippingProfileResponse)
async def get_shipping_profiles(
    controller: Annotated[str, Query()] = "jv",
    service: ExternalService = Depends(get_external_api_service),
):
    data = await service.get_shipping_profiles(controller)
    return data


@router.get("/categories", response_model=ExternalCategoriesResponse)
async def get_categories(
    page: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=0),
    category: str | None = Query(default=None),
    service: ExternalService = Depends(get_external_api_service),
    db: AsyncSession = Depends(get_db),
):
    payload = CategoryQuery(
        page=page,
        limit=limit,
        category=category,
    ).to_payload()
    data = await service.get_categories(payload, "jv", db)
    return data


@router.get("/attributes", response_model=ExternalCategoryAttributesResponse)
async def get_category_attributes(
    category_id: int = Query(alias="categoryId", ge=1),
    service: ExternalService = Depends(get_external_api_service),
    db: AsyncSession = Depends(get_db),
):
    data = await service.get_category_attributes(category_id, "jv", db)
    return data


@router.get(
    "/quantity/{sku}",
    summary="Get actual product quantity from OTTO availability",
    description="Fetches live stock quantity from OTTO for given SKU or EAN.",
)
async def get_quantity(
    sku: str,
    controller: Annotated[str, Query()] = "jv",
    service: ExternalService = Depends(get_external_api_service),
    db: AsyncSession | None = Depends(get_db),
):
    session = db if isinstance(db, AsyncSession) else None
    return await service.get_quantity(sku_or_ean=sku, controller=controller, session=session)

