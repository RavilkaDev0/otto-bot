import asyncio
import hashlib
import logging
from uuid import UUID
from typing import Any
from copy import deepcopy
from httpx import HTTPStatusError
from pydantic import ValidationError
from datetime import datetime, timezone

from app.core.otto_exceptions import (
    OttoApiException,
    OttoNotFoundError,
    OttoRateLimitError,
)

from sqlalchemy import or_, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.products import Product
from app.models.product_variants import ProductVariant
from app.models.otto_xlsx_import import OttoXlsxImportRow


from app.clients.external_otto_client import ExternalOttoClient
from app.core.logger import format_log_payload

from app.infrastructure.rabbitmq_publisher import RabbitMQPublisher

from app.models.attribute_allowed_values import AttributeAllowedValue
from app.models.attributes import Attribute
from app.models.categories import Category
from app.models.category_group import CategoryGroup

from app.repository.external_api_repository import ExternalApiRepository

from app.schemas.product_response import (
    ExternalCategoryAttributesResponse,
    ExternalCategoriesResponse,
    ExternalCategoryItem,
    OttoCategoryResponse,
    AttributeSchema,
)
from app.schemas.external_schemes.until_schemes import (
    CreateOrUpdateProductVariationRequest,
    ProductVariationRequest,
    ActiveStatusByEanResponse,
    ActiveStatusByEanRequest,
    DeliveryInformationRequest,
    ShippingProfileResponse,
    AvailabilityRequest,
    DeliveryInformation,
    GetProductRequest,
    CreateDeliveryJob,
    DeliveryRequest,
    QuantityRequest,
    Controller,
    Quantity,
    UpdateTaskResponse,
)


logger = logging.getLogger("external_api")

EXTERNAL_PRODUCT_BRAND_ID_BY_CONTROLLER: dict[str, str] = {
    "jv": "UO4EGHSX",
    "xl": "6HMOZBOU",
}


EXTERNAL_PRODUCT_COMPLIANCE_BY_CONTROLLER: dict[str, dict[str, Any]] = {
    "jv": {
        "productSafety": {
            "addresses": [
                {
                    "name": "AEA GmbH & Co. KG",
                    "address": "Am Flugplatz 28, 88483 Burgrieden",
                    "regionCode": "DE",
                    "roles": ["DISTRIBUTOR"],
                    "email": "info@jvmoebel.de",
                    "phone": "07392 - 93 78 44 0",
                    "url": "https://www.jvmoebel.de/Infos/Kontakt.htm",
                    "components": [],
                }
            ]
        }
    },
    "xl": {
        "productSafety": {
            "addresses": [
                {
                    "name": "XL MOEBEL GmbH",
                    "address": "Am Flugplatz 26, 88483 Burgrieden",
                    "regionCode": "DE",
                    "roles": ["DISTRIBUTOR"],
                    "email": "info@xlmoebel.de",
                    "phone": "07392 - 93 78 188",
                    "url": "https://www.xlmoebel.de/kontakt",
                    "components": [],
                }
            ]
        }
    },
}


def _process_uvp(price: float) -> float:
    if price > 5000:
        value = price * 1.10
    elif 2500 <= price <= 4999:
        value = price * 1.18
    elif 1000 <= price <= 2499:
        value = price * 1.25
    else:
        value = price * 1.35
    return round(value, 2)


class ExternalService:

    def __init__(self, client: ExternalOttoClient):
        self.client = client

    async def get_products(self, payload: GetProductRequest, controller: str):
        logger.info(
            "Service accepted get_products request\npayload:\n%s",
            format_log_payload(payload),
        )

        data = await self.client.get_products(payload, controller)
        
        if payload.sku is None:
            return data
        logger.info(f"Sending request to get_shipping_profile_for_product")
        try: 
            data2 = await self.client.get_shipping_profile_for_product(payload.sku, controller)

        except Exception:
            data2 = None

        if isinstance(data2, dict) and data2.get("shippingProfileId", None):
            shipping_profile_id = data2["shippingProfileId"]
            
            logger.info(
                "OTTO get_products response:\n%s\n and shipping_profile_id:%s",
                format_log_payload(data),
                shipping_profile_id
            )
            
            response = {**data, "shipping_profile_id": shipping_profile_id}

            return response
            
        return {**data, "shipping_profile_id": data2}
            
    async def create_or_update_product(
        self,
        payload: CreateOrUpdateProductVariationRequest,
        controller: Controller,
        publisher: RabbitMQPublisher | None = None,
        wait_for_completion: bool = False,
        wait_timeout: float = 15.0,
    ):
        logger.info(
            "Service accepted create_or_update_product request (wait=%s)\npayload:\n%s",
            wait_for_completion,
            format_log_payload(payload),
        )

        skus = [v.sku for v in payload.root]
        logger.info("Создание или обновление товаров в OTTO: skus=%s", skus)

        prepared_payload = self._prepare_create_or_update_payload(payload, controller)

        logger.debug("Prepared OTTO payload:\n%s", format_log_payload(prepared_payload))

        try:
            response = await self.client.create_or_update_product(prepared_payload, controller)
        except OttoRateLimitError as exc:
            logger.warning(
                "OTTO rate limit при создании/обновлении товаров: skus=%s controller=%s retry_after=%s",
                skus,
                controller,
                exc.retry_after,
            )
            raise
        except Exception:
            logger.exception("Ошибка создания или обновления товаров: skus=%s", skus)
            raise

        marketplace_job_id = self._extract_marketplace_job_id(response)

        job = self._build_delivery_job(
            marketplace_job_id=marketplace_job_id,
            variations=payload.root,
            controller=controller,
        )

        if publisher is not None:
            try:
                await publisher.publish(job)
            except Exception:
                logger.exception(
                    "Товары отправлены в OTTO, но задача не опубликована: "
                    "marketplace_job_id=%s skus=%s",
                    marketplace_job_id,
                    skus,
                )
                raise

            logger.info(
                "Задача опубликована: task_id=%s marketplace_job_id=%s skus=%s",
                job.task_id,
                marketplace_job_id,
                skus,
            )
        else:
            logger.warning(
                "RabbitMQ publisher не передан, availability не поставлен в очередь: "
                "marketplace_job_id=%s skus=%s",
                marketplace_job_id,
                skus,
            )

        controller_name = controller.value if hasattr(controller, "value") else str(controller)

        if wait_for_completion:
            try:
                interval = 2.0
                max_attempts = max(1, int(wait_timeout / interval))
                task_res = await self._wait_for_update_task(
                    marketplace_job_id,
                    controller_name,
                    max_attempts=max_attempts,
                    interval_seconds=interval,
                )
                status_dict = await self.get_job_status(marketplace_job_id, controller)
                is_done = task_res.state.lower() in ("done", "finished")
                return {
                    "status": "done" if is_done else "in_progress",
                    "job_id": str(marketplace_job_id),
                    "marketplace_job_id": str(marketplace_job_id),
                    "task_id": str(job.task_id),
                    "skus": skus,
                    "controller": controller_name,
                    "state": task_res.state,
                    "total": status_dict.get("total", 0),
                    "succeeded": status_dict.get("succeeded", 0),
                    "failed": status_dict.get("failed", 0),
                    "unchanged": status_dict.get("unchanged", 0),
                    "failures": status_dict.get("failures", []),
                    "message": (
                        "Products processed by OTTO"
                        if is_done
                        else "Product processing in progress"
                    ),
                    "raw_response": response,
                }
            except Exception as e:
                logger.warning(
                    "Timed out or error waiting for OTTO job status (returning accepted): job_id=%s error=%s",
                    marketplace_job_id,
                    e,
                )

        return {
            "status": "accepted",
            "job_id": str(marketplace_job_id),
            "marketplace_job_id": str(marketplace_job_id),
            "task_id": str(job.task_id),
            "skus": skus,
            "controller": controller_name,
            "message": "Product variations accepted by OTTO. Use GET /extermal/job_status/{job_id} to track processing status.",
            "raw_response": response,
        }

    def _build_delivery_job(
        self,
        marketplace_job_id: UUID,
        variations: list[ProductVariationRequest],
        controller: Controller,
    ) -> CreateDeliveryJob:
        delivery_list = [
            DeliveryInformation(
                shipping_profile_id=var.shipping_profile_id,
                sku=var.sku,
            )
            for var in variations
        ]
        quantity_list = [
            Quantity(
                quantity=var.quantity,
                sku=var.sku,
            )
            for var in variations
        ]

        quantity_data = QuantityRequest.model_validate(quantity_list)
        delivery_data = DeliveryInformationRequest.model_validate(delivery_list)

        availability_data = AvailabilityRequest(
            quantities=quantity_data,
            delivery_information=delivery_data,
        )

        job = CreateDeliveryJob(
            marketplace_job_id=marketplace_job_id,
            availability_data=availability_data,
            controller=controller,
        )

        return job

    def _prepare_create_or_update_payload(
        self,
        payload: CreateOrUpdateProductVariationRequest,
        controller: str,
    ) -> list[dict[str, Any]]:
        brand_id = EXTERNAL_PRODUCT_BRAND_ID_BY_CONTROLLER[controller]
        compliance_payload = EXTERNAL_PRODUCT_COMPLIANCE_BY_CONTROLLER[controller]

        products = payload.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        for product in products:
            product_description = dict(product.get("productDescription") or {})
            product_description["brandId"] = brand_id
            product["productDescription"] = product_description
            product["compliance"] = deepcopy(compliance_payload)
            self._apply_msrp(product)

        return products


    def _extract_marketplace_job_id(self, payload: dict[str, Any]) -> UUID:
        links = payload.get("links")
        if not links or not isinstance(links, list):
            logger.error("OTTO не вернул links: response=\n%s", format_log_payload(payload))
            raise RuntimeError(f"OTTO response does not contain valid 'links': {payload}")

        href = links[0].get("href")
        if not href or not isinstance(href, str):
            logger.error("OTTO link missing href: response=\n%s", format_log_payload(payload))
            raise RuntimeError(f"OTTO link missing 'href': {payload}")

        job_id_str = href.rstrip("/").rsplit("/", 1)[-1]
        try:
            return UUID(job_id_str)
        except ValueError as exc:
            raise RuntimeError(f"Failed to parse UUID from OTTO href '{href}': {exc}") from exc


    async def create_or_update_availability(self, job: CreateDeliveryJob) -> None:
        update_status = await self._wait_for_update_task(
            job.marketplace_job_id,
            job.controller,
        )

        has_success_or_unchanged = (update_status.succeeded + update_status.unchanged) > 0
        if not has_success_or_unchanged:
            logger.warning(
                "Availability не запущен: OTTO update-task не succeeded/unchanged: "
                "job_id=%s succeeded=%s unchanged=%s failed=%s state=%s message=%s",
                job.marketplace_job_id,
                update_status.succeeded,
                update_status.unchanged,
                update_status.failed,
                update_status.state,
                update_status.message,
            )
            return

        logger.info(
            "OTTO product update завершен, запускаю availability: job_id=%s succeeded=%s unchanged=%s failed=%s",
            job.marketplace_job_id,
            update_status.succeeded,
            update_status.unchanged,
            update_status.failed,
        )

        availability_data = job.availability_data

        controller = job.controller

        quantity_data = availability_data.quantities
        delivery_information_data = availability_data.delivery_information
        try:
            quantity_result = await self.client.update_quantity(quantity_data, controller)
            delivery_information_result = await self.client.update_product_delivery(
                delivery_information_data, controller
            )
        except Exception:
            logger.exception(
                "Ошибка в оркестраторе запроса"
            )
            raise

        logger.info(
            "Успешно обработал оркестратор запросов: method=%s marketplace_job_id=%s",
            "create_or_update_availability",
            job.marketplace_job_id
        )
        logger.info(
            "Ответы от оркестратора:\nquantity:\n%s\ndelivery:\n%s",
            format_log_payload(quantity_result),
            format_log_payload(delivery_information_result),
        )


    async def read_update_task_result(self, job_id: UUID | str, controller: Controller | str) -> UpdateTaskResponse:
        controller_name = controller.value if hasattr(controller, "value") else str(controller)
        response = await self.client.read_update_task_result(job_id, controller_name)

        try:
            data = UpdateTaskResponse.model_validate(response)

        except ValidationError:
            logger.exception(
                "Произошла ошибка валидации. function=%s\n job_id=%s",
                "read_update_task_result",
                job_id
            )
            raise

        return data

    async def get_job_status(
        self, job_id: UUID | str, controller: Controller | str
    ) -> dict[str, Any]:
        """Fetch real-time status of an OTTO update task, including failure details if any."""
        controller_name = controller.value if hasattr(controller, "value") else str(controller)
        task_data = await self.client.read_update_task_result(job_id, controller_name)

        if hasattr(task_data, "model_dump"):
            task_dict = task_data.model_dump()
        elif isinstance(task_data, dict):
            task_dict = task_data
        else:
            task_dict = {}

        state = task_dict.get("state", "UNKNOWN")
        total = task_dict.get("total", 0)
        progress = task_dict.get("progress", 0)
        succeeded = task_dict.get("succeeded", 0)
        failed = task_dict.get("failed", 0)
        unchanged = task_dict.get("unchanged", 0)

        result: dict[str, Any] = {
            "job_id": str(job_id),
            "controller": controller_name,
            "state": state,
            "total": total,
            "progress": progress,
            "succeeded": succeeded,
            "failed": failed,
            "unchanged": unchanged,
            "failures": [],
            "succeeded_items": [],
        }

        if failed > 0:
            failed_resp = await self.client.get_update_task_failed(job_id, controller_name)
            if failed_resp:
                result["failures"] = failed_resp.get("results", failed_resp)

        if succeeded > 0:
            succ_resp = await self.client.get_update_task_succeeded(job_id, controller_name)
            if succ_resp:
                result["succeeded_items"] = succ_resp.get("results", succ_resp)

        return result


    async def _wait_for_update_task(
        self,
        job_id: UUID,
        controller: str,
        max_attempts: int = 120,
        interval_seconds: float = 3.0,
    ) -> UpdateTaskResponse:
        for attempt in range(1, max_attempts + 1):
            logger.info("Ожидается статус задачи из OTTO (попытка %d/%d)...", attempt, max_attempts)
            try:
                update_status = await self.read_update_task_result(job_id, controller)
            except OttoNotFoundError:
                if attempt <= 5:
                    logger.warning(
                        "Update task %s еще не найдена в OTTO (попытка %d/%d), повтор через %.1fs...",
                        job_id, attempt, max_attempts, interval_seconds,
                    )
                    await asyncio.sleep(interval_seconds)
                    continue
                raise

            if update_status.state.lower() in ("done", "finished"):
                logger.info(
                    "Статус задачи получен: job_id=%s attempt=%d",
                    job_id,
                    attempt,
                )
                return update_status

            if update_status.state.lower() in ("failed", "error", "cancelled"):
                logger.error(
                    "Задача OTTO завершилась со статусом %s: job_id=%s message=%s",
                    update_status.state,
                    job_id,
                    update_status.message,
                )
                return update_status

            delay = interval_seconds
            if update_status.ping_after is not None:
                now = datetime.now(timezone.utc)
                if update_status.ping_after > now:
                    delay = max((update_status.ping_after - now).total_seconds(), 1.0)
                    delay = min(delay, 30.0)

            logger.info(
                "Задача все еще %s, retry: job_id=%s sleep_seconds=%.2f",
                update_status.state,
                job_id,
                delay,
            )

            await asyncio.sleep(delay)

        raise TimeoutError(
            f"OTTO update-task {job_id} did not finish within {max_attempts * interval_seconds:.0f}s"
        )


    @staticmethod
    def _apply_msrp(product: dict[str, Any]) -> None:
        pricing = product.get("pricing")
        if not isinstance(pricing, dict):
            return

        standard_price = pricing.get("standardPrice")
        if not isinstance(standard_price, dict):
            return

        amount = standard_price.get("amount")
        if amount is None:
            return

        price = float(amount)
        pricing["msrp"] = {
            "amount": _process_uvp(price),
            "currency": standard_price.get("currency") or "EUR",
        }

    async def resolve_sku_for_ean(
        self,
        ean: str,
        controller: str,
        session: AsyncSession | None = None,
    ) -> str | None:
        """Find SKU corresponding to EAN via local database or OTTO API."""
        # 1. Search in local database (first OttoXlsxImportRow filtered by account, then Product)
        if session is not None:
            try:
                stmt_import = select(OttoXlsxImportRow.sku).where(
                    OttoXlsxImportRow.account == controller.lower(),
                    OttoXlsxImportRow.ean == ean,
                    OttoXlsxImportRow.sku.isnot(None),
                ).limit(1)
                sku_row = (await session.execute(stmt_import)).scalar_one_or_none()
                if sku_row and str(sku_row).strip():
                    return str(sku_row).strip()

                stmt_prod = select(Product.sku).where(
                    Product.ean == ean,
                    Product.sku.isnot(None),
                ).limit(1)
                prod_sku = (await session.execute(stmt_prod)).scalar_one_or_none()
                if prod_sku and str(prod_sku).strip():
                    return str(prod_sku).strip()
            except Exception as e:
                logger.warning("DB lookup failed for EAN %s: %s", ean, e)

        # 2. Try querying OTTO with sku=ean (in OTTO catalog, SKU often equals EAN)
        try:
            products_by_sku = await self.client.get_products(
                GetProductRequest(sku=ean, page=0, limit=1),
                controller,
            )
            variations = self._product_variations(products_by_sku)
            for item in variations:
                item_sku = str(item.get("sku") or item.get("SKU") or "").strip()
                item_ean = str(item.get("ean") or item.get("EAN") or "").strip()
                if item_sku == ean or item_ean == ean:
                    return item_sku
        except Exception as e:
            logger.debug("OTTO lookup by sku=%s returned error: %s", ean, e)

        # 3. Try querying OTTO with productReference=ean
        try:
            products_by_ref = await self.client.get_products(
                GetProductRequest(productReference=ean, page=0, limit=10),
                controller,
            )
            variations = self._product_variations(products_by_ref)
            for item in variations:
                item_sku = str(item.get("sku") or item.get("SKU") or "").strip()
                item_ean = str(item.get("ean") or item.get("EAN") or "").strip()
                if item_ean == ean and item_sku:
                    return item_sku
            if len(variations) == 1:
                first_sku = str(variations[0].get("sku") or "").strip()
                if first_sku:
                    return first_sku
        except Exception as e:
            logger.debug("OTTO lookup by productReference=%s returned error: %s", ean, e)

        return None

    async def set_active_status_by_ean(
        self,
        payload: ActiveStatusByEanRequest,
        active: bool,
        session: AsyncSession | None = None,
    ):
        ean = payload.ean.strip()
        controller = payload.controller.value

        sku = await self.resolve_sku_for_ean(ean, controller, session=session)
        if not sku:
            return ActiveStatusByEanResponse(
                success=False,
                ean=ean,
                active=active,
                controller=payload.controller,
                status_code=404,
                message=f"Product with EAN '{ean}' was not found on {controller.upper()}",
                response=None,
            )

        # If deactivating, OTTO requires setting quantity to 0 so the product is completely pulled from the marketplace
        if not active:
            try:
                quantity_req = QuantityRequest(root=[Quantity(sku=sku, quantity=0)])
                await self.client.update_quantity(quantity_req, controller)
                logger.info(
                    "Set quantity=0 before deactivation for SKU %s on %s",
                    sku, controller.upper(),
                )
            except Exception as e:
                logger.warning(
                    "Failed to set quantity=0 for SKU %s on %s: %s",
                    sku, controller.upper(), e,
                )

        status_code, response = await self.client.update_active_status(
            {"status": [{"sku": sku, "active": active}]},
            controller,
        )
        success = status_code == 202

        # Update local DB if session is provided and request was accepted
        if success and session is not None:
            try:
                new_status = "Aktiv" if active else "Inaktiv"
                await session.execute(
                    update(Product)
                    .where(or_(Product.sku == sku, Product.ean == ean))
                    .values(active_status=new_status, marketplace_status=new_status)
                )
                await session.execute(
                    update(OttoXlsxImportRow)
                    .where(
                        OttoXlsxImportRow.account == controller.lower(),
                        or_(OttoXlsxImportRow.sku == sku, OttoXlsxImportRow.ean == ean),
                    )
                    .values(active_status=new_status, marketplace_status=new_status)
                )
                await session.commit()
            except Exception as e:
                logger.warning("Failed to update status in DB for SKU %s: %s", sku, e)

        return ActiveStatusByEanResponse(
            success=success,
            ean=ean,
            sku=sku,
            active=active,
            controller=payload.controller,
            status_code=status_code,
            message=(
                f"Active status update ({'activate' if active else 'deactivate'}) was accepted by OTTO"
                if success
                else f"Active status update was rejected by OTTO (status {status_code})"
            ),
            response=response,
        )

    async def get_quantity(
        self,
        sku_or_ean: str,
        controller: str = "jv",
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        identifier = str(sku_or_ean or "").strip()
        sku = await self.resolve_sku_for_ean(identifier, controller, session=session)
        if not sku:
            sku = identifier
        return await self.client.get_quantity(sku, controller)

    async def get_shipping_profiles(
        self,
        controller: str,
        # repository: ExternalApiRepository
    ):

        # NOTE: Сдесь получает с самого ОТТО
        data = await self.client.get_shipping_profiles(controller)
        shipping_profiles = ShippingProfileResponse.model_validate(data["results"])

        # NOTE: Сдесь запрос для записи в базу
        # await repository.create_shipping_profile(shipping_profiles)

        return shipping_profiles

    async def get_categories(
        self,
        payload: dict,
        controller: str,
        session: AsyncSession | None = None,
    ):
        if session is not None:
            local_categories = await self._get_local_categories(payload, session)
            if local_categories.categories:
                return local_categories

        category_response = await self._get_category_response(payload, controller)
        categories: list[ExternalCategoryItem] = []
        seen_categories: set[str] = set()
        category_ids_by_name = {}
        for group in category_response.categoryGroups:
            category_group_id = self._stable_int_id(
                "category_group",
                group.categoryGroup,
            )
            for category in group.categories:
                normalized = str(category or "").strip()
                if normalized and normalized not in seen_categories:
                    seen_categories.add(normalized)
                    category_id = category_ids_by_name.get(
                        normalized.casefold()
                    ) or self._stable_int_id("category", normalized)
                    categories.append(
                        ExternalCategoryItem(
                            category_group=group.categoryGroup,
                            category_group_id=category_group_id,
                            categoryId=category_id,
                            name=normalized,
                        )
                    )

        return ExternalCategoriesResponse(categories=categories)

    async def get_category_attributes(
        self,
        category_id: int,
        controller: str,
        session: AsyncSession | None = None,
    ):
        if session is not None and self._is_db_int_id(category_id):
            local_attributes = await self._get_local_category_attributes_by_id(
                category_id, session
            )
            if local_attributes.attributes:
                return local_attributes

        category_response = await self._get_category_response(
            {"page": 0, "limit": 2000}, controller
        )
        category = self._category_name_for_id(category_response, category_id)
        if not category:
            return ExternalCategoryAttributesResponse(attributes=[])

        normalized_category = category.strip().casefold()
        attributes: list[AttributeSchema] = []
        seen_attributes: set[str] = set()
        matching_group_names: list[str] = []

        for group in category_response.categoryGroups:
            group_categories = {
                str(item or "").strip().casefold()
                for item in group.categories
                if str(item or "").strip()
            }
            if normalized_category not in group_categories:
                continue
            matching_group_names.append(group.categoryGroup)

        attribute_ids_by_group_and_name = {}

        for group in category_response.categoryGroups:
            if group.categoryGroup not in matching_group_names:
                continue
            for attribute in group.attributes:
                normalized_name = attribute.name.strip().casefold()
                if normalized_name and normalized_name not in seen_attributes:
                    seen_attributes.add(normalized_name)
                    attribute_id = attribute_ids_by_group_and_name.get(
                        (group.categoryGroup.casefold(), normalized_name)
                    ) or self._stable_int_id(
                        "attribute",
                        group.categoryGroup,
                        attribute.name,
                    )
                    attributes.append(
                        attribute.model_copy(
                            update={
                                "id": attribute_id,
                                "attributeId": attribute_id,
                                "attributeKey": (
                                    str(attribute_id)
                                    if attribute_id is not None
                                    else None
                                ),
                            }
                        )
                    )

        return ExternalCategoryAttributesResponse(attributes=attributes)

    async def _get_category_response(
        self,
        payload: dict,
        controller: str,
    ):
        data = await self.client.get_categories(payload, controller)
        if isinstance(data, list):
            data = {"categoryGroups": data}
        return OttoCategoryResponse.model_validate(data)

    async def _get_local_categories(
        self,
        payload: dict,
        session: AsyncSession,
    ):
        page = max(0, int(payload.get("page") or 0))
        limit = max(0, int(payload.get("limit") or 10))
        category = str(payload.get("category") or "").strip()
        stmt = (
            select(Category)
            .options(selectinload(Category.group))
            .order_by(Category.name.asc())
        )
        if category:
            stmt = stmt.where(Category.name.ilike(f"%{category}%"))
        if limit:
            stmt = stmt.offset(page * limit).limit(limit)

        rows = (await session.scalars(stmt)).all()
        return ExternalCategoriesResponse(
            categories=[
                ExternalCategoryItem(
                    category_group=item.group.name if item.group else None,
                    category_group_id=item.group_id,
                    categoryId=item.id,
                    name=item.name,
                )
                for item in rows
                if item.name
            ]
        )

    async def _get_local_category_attributes_by_id(
        self,
        category_id: int,
        session: AsyncSession,
    ):
        if not category_id:
            return ExternalCategoryAttributesResponse(attributes=[])

        stmt = (
            select(CategoryGroup)
            .join(Category)
            .options(
                selectinload(CategoryGroup.attributes).selectinload(
                    Attribute.allowed_values
                )
            )
            .where(Category.id == category_id)
            .order_by(CategoryGroup.name.asc())
        )
        group = (await session.scalars(stmt)).unique().first()
        if group is None:
            return ExternalCategoryAttributesResponse(attributes=[])

        attributes = [
            AttributeSchema(
                attributeId=attr.id,
                attributeKey=str(attr.id),
                name=attr.name,
                type=attr.type,
                attributeGroup=attr.attribute_group,
                description=attr.description,
                relevance=attr.relevance,
                featureRelevance=list(attr.feature_relevance or []),
                multiValue=attr.multi_value,
                unit=attr.unit or "",
                unitDisplayName=attr.unit_display_name,
                allowedValues=sorted(
                    {item.value for item in attr.allowed_values if item.value},
                    key=str.casefold,
                ),
            )
            for attr in sorted(group.attributes, key=lambda item: item.name.casefold())
            if attr.name
        ]
        return ExternalCategoryAttributesResponse(attributes=attributes)

    def _category_name_for_id(
        self,
        category_response: OttoCategoryResponse,
        category_id: int,
    ) -> str | None:
        for group in category_response.categoryGroups:
            for category in group.categories:
                normalized = str(category or "").strip()
                if not normalized:
                    continue
                if self._stable_int_id("category", normalized) == category_id:
                    return normalized
        return None

    @staticmethod
    def _stable_int_id(*parts: str) -> int:
        payload = ":".join(str(part or "").strip().casefold() for part in parts)
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        return int(digest[:12], 16)

    @staticmethod
    def _is_db_int_id(value: int) -> bool:
        return 0 < value <= 2_147_483_647

    @staticmethod
    def _product_variations(data: object) -> list[dict]:
        if not isinstance(data, dict):
            return []

        variations = data.get("productVariations")
        if isinstance(variations, list):
            return [item for item in variations if isinstance(item, dict)]

        results = data.get("results")
        if isinstance(results, dict):
            variations = results.get("productVariations")
            if isinstance(variations, list):
                return [item for item in variations if isinstance(item, dict)]
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]

        return []

    @classmethod
    def _sku_for_ean(cls, data: object, ean: str) -> str | None:
        variations = cls._product_variations(data)
        if not variations:
            return None

        for item in variations:
            item_ean = str(item.get("ean") or item.get("EAN") or "").strip()
            sku = str(item.get("sku") or item.get("SKU") or "").strip()
            if sku and item_ean == ean:
                return sku

        return None
