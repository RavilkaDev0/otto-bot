"""Thin service layer around OTTO client operations.

This class intentionally keeps business logic minimal. It exists to provide a
stable dependency boundary for routes and higher-level workflows while keeping
the HTTP implementation isolated inside `OttoClient`.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clients.otto_client import OttoClient
from app.core.configs import settings
from app.core.logger import logging
from app.models.attribute_allowed_values import AttributeAllowedValue
from app.models.attributes import Attribute
from app.models.categories import Category
from app.models.category_group import CategoryGroup
from app.models.variation_theme import VariationTheme
from app.normalize_product_to_schema import brand_id_for_controller
from app.schemas.enums import Controller

# Schemas
from app.schemas.product import (
    Availability,
    AvailabilityRequest,
    CreateProductRequest,
    Product,
    ProductBase,
    ProductClient,
    ProductResponse,
    UpdateProductDelivery,
    UpdateQuantity,
)
from app.schemas.product_response import (
    AvailabilityResponse,
    CategoryGroupSchema,
    DeleteProductResponse,
    OperationResult,
    OttoCategoryResponse,
    ProductCreateResponse,
    UpdateProductDeliveryResponse,
    UpdateQuantityResponse,
)
from app.services.translation_service import TranslationError, TranslationService
from app.services.utility_service import UtilityService
from app.utils.helpers import to_json


class ProductService:
    """Coordinate product-related calls to OTTO-facing client methods."""

    def __init__(self, client: OttoClient):
        self.client = client
        self.logger = logging.getLogger(__name__)

    async def get_product(self, sku: str):
        """Fetch one product from OTTO by SKU."""
        return await self.client.get_product(sku)

    async def get_product_with_status(self, sku: str):
        """Fetch one product from OTTO by SKU and include the upstream status code."""
        return await self.client.get_product_with_status(sku)

    async def get_products(
        self,
        payload: dict,
        controller: Controller = Controller.JV,
    ) -> ProductResponse:
        """Fetch paginated products from OTTO using query payload filters."""
        return await self.client.get_products(payload, controller=controller)

    async def get_active_products(
        self,
        payload: dict,
        controller: Controller = Controller.JV,
    ):
        """Fetch active-status listing from OTTO."""
        return await self.client.get_active_products(payload, controller=controller)

    async def update_tasks(self, pid: str, controller: Controller = Controller.JV):
        """Trigger backend update tasks for a given OTTO product id."""
        return await self.client.update_tasks(pid, controller=controller)

    async def failed_tasks(self, pid: str, controller: Controller = Controller.JV):
        return await self.client.failed_tasks(pid, controller=controller)

    async def succeeded_tasks(self, pid: str, controller: Controller = Controller.JV):
        return await self.client.succeeded_tasks(pid, controller=controller)

    async def unchanged_tasks(self, pid: str, controller: Controller = Controller.JV):
        return await self.client.unchanged_tasks(pid, controller=controller)

    async def get_marketplace_status(
        self,
        payload: dict,
        controller: Controller = Controller.JV,
    ):
        """Fetch marketplace status information for products from OTTO."""
        return await self.client.get_marketplace_status(payload, controller=controller)

    async def create_or_update_products(
        self, payload: CreateProductRequest
    ) -> ProductCreateResponse:
        """Create or upsert products in OTTO with normalized payload bodies."""
        compliance = settings.compliance.get(payload.controller)

        products = []
        brand_id = brand_id_for_controller(payload.controller)

        for item in payload.products:
            product_data = item.model_dump()
            product_description = dict(product_data.get("productDescription") or {})
            product_description["brandId"] = brand_id
            product_data["productDescription"] = product_description
            product = ProductClient(**product_data, compliance=compliance)
            products.append(product)

            self.logger.debug(
                "Подготовлен продукт для отправки в OTTO: sku=%s controller=%s payload=%s",
                getattr(product, "sku", None),
                (
                    payload.controller.value
                    if isinstance(payload.controller, Controller)
                    else payload.controller
                ),
                product.model_dump(mode="json", by_alias=True),
            )

        otto_payload = ProductBase(products)

        return await self.client.create_or_update_products(
            otto_payload, controller=payload.controller
        )

    async def update_status(
        self,
        payload: dict,
        controller: Controller = Controller.JV,
    ):
        """Update active flags/status for one or more products in OTTO."""
        return await self.client.update_status(payload, controller=controller)

    async def update_status_with_status(
        self,
        payload: dict,
        controller: Controller = Controller.JV,
    ):
        """Update active flags/status and return the upstream status code."""
        return await self.client.update_status_with_status(
            payload, controller=controller
        )

    async def update_prices_with_status(
        self,
        payload: list[dict],
        controller: Controller = Controller.JV,
    ):
        """Update product prices in OTTO and return the upstream status code."""
        return await self.client.update_prices_with_status(
            payload, controller=controller
        )

    async def get_categories(
        self,
        payload: dict,
        controller: Controller = Controller.JV,
    ):
        """Fetch category information from OTTO, normalized by the client."""

        return await self.client.get_categories(payload, controller=controller)

    async def update_quantity(
        self, payload: Optional[dict | list], controller: Controller = Controller.JV
    ):
        """Upload or create quantity for sku(product)"""
        return await self.client.update_quantity(payload, controller=controller)

    async def update_quantity_with_status(
        self, payload: Optional[dict | list], controller: Controller = Controller.JV
    ):
        """Upload quantity and return the upstream status code."""
        return await self.client.update_quantity_with_status(
            payload, controller=controller
        )

    async def update_product_delivery_information(
        self, payload: dict, controller: Controller = Controller.JV
    ):
        """Create or update shipping profile for products"""
        return await self.client.update_product_delivery_information(
            payload, controller=controller
        )

    async def get_shipping_profiles(self, controller: Controller = Controller.JV):
        """Fetch all product delivary info from partner(us)"""
        return await self.client.get_shipping_profiles(controller=controller)

    # Post creation of product data process
    async def create_availability(self, payload: Availability) -> AvailabilityResponse:
        self.logger.info(
            "step=create_availability_start sku=%s quantity=%s shipping_profile_id=%s processing_time=%s controller=%s",
            payload.sku,
            payload.quantity or "20",
            payload.shippingProfileID,
            payload.processingTime or "DEFAULT",
            (
                payload.controller.value
                if isinstance(payload.controller, Controller)
                else payload.controller
            ),
        )

        quantity_payload = UpdateQuantity(
            sku=payload.sku, quantity=payload.quantity or "20"
        )

        delivery_payload = UpdateProductDelivery(
            sku=payload.sku,
            processingTime=payload.processingTime or "DEFAULT",
            shippingProfileId=payload.shippingProfileID,
        )

        try:
            self.logger.info(
                "step=create_availability_quantity_request sku=%s payload=%s",
                payload.sku,
                quantity_payload.model_dump(mode="json", by_alias=True),
            )
            await self.update_quantity(
                quantity_payload.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                controller=payload.controller,
            )
            quantity_result: OperationResult | Exception = OperationResult(success=True)
            self.logger.info(
                "step=create_availability_quantity_success sku=%s",
                payload.sku,
            )
        except Exception as exc:
            quantity_result = exc
            self.logger.exception(
                "step=create_availability_quantity_failed sku=%s error=%s",
                payload.sku,
                exc,
            )

        try:
            self.logger.info(
                "step=create_availability_delivery_request sku=%s payload=%s",
                payload.sku,
                delivery_payload.model_dump(mode="json", by_alias=True),
            )
            await self.update_product_delivery_information(
                delivery_payload.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                controller=payload.controller,
            )
            delivery_result: OperationResult | Exception = OperationResult(success=True)
            self.logger.info(
                "step=create_availability_delivery_success sku=%s shipping_profile_id=%s",
                payload.sku,
                payload.shippingProfileID,
            )
        except Exception as exc:
            delivery_result = exc
            self.logger.exception(
                "step=create_availability_delivery_failed sku=%s shipping_profile_id=%s error=%s",
                payload.sku,
                payload.shippingProfileID,
                exc,
            )

        if isinstance(quantity_result, Exception):
            quantity_result = OperationResult(
                success=False, errors=str(quantity_result)
            )

        if isinstance(delivery_result, Exception):
            delivery_result = OperationResult(
                success=False, errors=str(delivery_result)
            )

        response = AvailabilityResponse(
            update_quantity=quantity_result, update_delivery=delivery_result
        )
        self.logger.info(
            "step=create_availability_done sku=%s quantity_success=%s delivery_success=%s quantity_errors=%s delivery_errors=%s",
            payload.sku,
            response.update_quantity.success if response.update_quantity else None,
            response.update_delivery.success if response.update_delivery else None,
            response.update_quantity.errors if response.update_quantity else None,
            response.update_delivery.errors if response.update_delivery else None,
        )
        return response

    async def delete_product_from_file(
        self, skus: list[str], controller: Controller
    ) -> DeleteProductResponse:
        """
        Reference:
            https://www.otto.de/p/jvmoebel-weinschrank-weinschrank-mit-kuehlschrankfunktion-und-flaschenregal-1-st-made-in-europe-S0CFH0N8/
        """
        # Reading a file

        date = datetime.now().strftime("%d-%m-%Y")

        util = UtilityService()
        quantity_payload: list[UpdateQuantity] = []
        products: list[Product] = []

        for sku in skus:
            quantity_payload.append(UpdateQuantity(sku=sku, quantity="0"))

            # Get product payload
            product_variation_response: ProductResponse = await self.get_products(
                {"sku": sku}
            )
            product_variation = product_variation_response.productVariations[0]
            product_variation.productDescription.productLine = f"DELETED_{date}"
            products.append(
                Product.model_validate(**product_variation.model_dump(mode="json"))
            )

        # Requests
        product_create_payload = CreateProductRequest(
            controller=controller, products=products
        )
        self.logger.info(
            "Запуск удаления товаров через обнуление остатков: sku_count=%s controller=%s",
            len(skus),
            controller.value if isinstance(controller, Controller) else controller,
        )
        self.logger.debug(
            "Подготовлен payload для удаления товаров: skus=%s products=%s",
            skus,
            [product.model_dump(mode="json", by_alias=True) for product in products],
        )
        product_task = self.create_or_update_products(product_create_payload)
        quantity_task = self.client.update_quantity(
            payload=[item.model_dump(mode="json") for item in quantity_payload],
            controller=controller,
        )

        product_result, quantity_result = await asyncio.gather(
            product_task,
            quantity_task,
            return_exceptions=True,
        )

        product_operation = OperationResult()
        quantity_operation = OperationResult()

        if isinstance(product_result, Exception):
            product_operation.errors = str(product_result)
            return DeleteProductResponse(product_operation=product_operation)

        else:
            product_operation.success = True

        if isinstance(quantity_result, Exception):
            quantity_operation.errors = str(quantity_result)
            return DeleteProductResponse(
                product_operation=product_operation,
                quantity_operation=quantity_operation,
            )

        else:
            quantity_operation.success = True

        return DeleteProductResponse(
            product_operation=product_operation, quantity_operation=quantity_operation
        )

    async def fetch_all_categories_to_db(
        self,
        session: AsyncSession,
        controller: Controller = Controller.JV,
    ) -> dict[str, int]:
        """Atomically replace the local category cache with the current OTTO data."""

        self.logger.info("Синхронизация категорий OTTO запущена")
        page_size = 100
        page = 0
        fetched_groups: list[CategoryGroupSchema] = []

        # Fetch everything before deleting the current cache. A transient OTTO
        # failure must never leave the application with a half-filled taxonomy.
        while True:
            self.logger.info("Загрузка страницы категорий OTTO: page=%s", page)
            response = await self.client.get_categories(
                {"page": page, "limit": page_size},
                controller=controller,
            )
            groups = response.categoryGroups
            if not groups:
                break
            fetched_groups.extend(groups)
            if len(groups) < page_size:
                break
            page += 1

        if not fetched_groups:
            raise RuntimeError("OTTO returned an empty category catalog")

        group_translations = dict(
            (
                await session.execute(
                    select(CategoryGroup.name, CategoryGroup.name_ru).where(
                        CategoryGroup.name_ru.is_not(None)
                    )
                )
            ).all()
        )
        category_translations = dict(
            (
                await session.execute(
                    select(Category.name, Category.name_ru).where(
                        Category.name_ru.is_not(None)
                    )
                )
            ).all()
        )
        attribute_translations = {
            (group_name, name): (name_ru, description_ru)
            for group_name, name, name_ru, description_ru in (
                await session.execute(
                    select(
                        CategoryGroup.name,
                        Attribute.name,
                        Attribute.name_ru,
                        Attribute.description_ru,
                    )
                    .join(Attribute, Attribute.group_id == CategoryGroup.id)
                    .where(
                        (Attribute.name_ru.is_not(None))
                        | (Attribute.description_ru.is_not(None))
                    )
                )
            ).all()
        }
        value_translations = {
            (group_name, attribute_name, value): value_ru
            for group_name, attribute_name, value, value_ru in (
                await session.execute(
                    select(
                        CategoryGroup.name,
                        Attribute.name,
                        AttributeAllowedValue.value,
                        AttributeAllowedValue.value_ru,
                    )
                    .join(Attribute, Attribute.group_id == CategoryGroup.id)
                    .join(
                        AttributeAllowedValue,
                        AttributeAllowedValue.attribute_id == Attribute.id,
                    )
                    .where(AttributeAllowedValue.value_ru.is_not(None))
                )
            ).all()
        }

        await session.execute(delete(CategoryGroup))
        await session.flush()

        category_count = 0
        attribute_count = 0
        allowed_value_count = 0
        variation_theme_count = 0
        seen_groups: set[str] = set()
        seen_categories: set[str] = set()

        for group_item in fetched_groups:
            group_name = group_item.categoryGroup.strip()
            if not group_name or group_name in seen_groups:
                continue
            seen_groups.add(group_name)
            group = CategoryGroup(
                name=group_name,
                name_ru=group_translations.get(group_name),
            )
            session.add(group)

            for raw_category_name in group_item.categories:
                category_name = str(raw_category_name or "").strip()
                if not category_name or category_name in seen_categories:
                    continue
                seen_categories.add(category_name)
                group.categories.append(
                    Category(
                        name=category_name,
                        name_ru=category_translations.get(category_name),
                    )
                )
                category_count += 1

            variation_names = set(group_item.variationThemes)
            seen_attributes: set[str] = set()
            for attr_item in group_item.attributes:
                attribute_name = attr_item.name.strip()
                if not attribute_name or attribute_name in seen_attributes:
                    continue
                seen_attributes.add(attribute_name)
                name_ru, description_ru = attribute_translations.get(
                    (group_name, attribute_name),
                    (None, None),
                )
                attribute = Attribute(
                    name=attribute_name,
                    name_ru=name_ru,
                    attribute_group=attr_item.attributeGroup,
                    feature_relevance=list(attr_item.featureRelevance),
                    type=attr_item.type,
                    description=attr_item.description,
                    description_ru=description_ru,
                    relevance=(
                        attr_item.relevance.value if attr_item.relevance else None
                    ),
                    multi_value=attr_item.multiValue,
                    unit=attr_item.unit,
                    unit_display_name=attr_item.unitDisplayName,
                )
                group.attributes.append(attribute)
                attribute_count += 1

                seen_values: set[str] = set()
                for raw_value in attr_item.allowedValues:
                    value = str(raw_value or "").strip()
                    if not value or value in seen_values:
                        continue
                    seen_values.add(value)
                    attribute.allowed_values.append(
                        AttributeAllowedValue(
                            value=value,
                            value_ru=value_translations.get(
                                (group_name, attribute_name, value)
                            ),
                        )
                    )
                    allowed_value_count += 1

                if attribute_name in variation_names:
                    session.add(VariationTheme(group=group, attribute=attribute))
                    variation_theme_count += 1

        await session.commit()
        result = {
            "groups": len(seen_groups),
            "categories": category_count,
            "attributes": attribute_count,
            "allowedValues": allowed_value_count,
            "variationThemes": variation_theme_count,
        }
        self.logger.info("Синхронизация категорий OTTO завершена: %s", result)
        return result

    async def ensure_category_translations(
        self,
        session: AsyncSession,
        *,
        groups: list[CategoryGroup] | None = None,
    ) -> None:
        """Fill missing Russian labels for OTTO category groups and categories."""

        if not str(settings.deepl_api_key_test or "").strip():
            self.logger.warning(
                "DeepL API key is not configured; category translations were skipped."
            )
            return

        if groups is None:
            result = await session.execute(
                select(CategoryGroup)
                .options(selectinload(CategoryGroup.categories))
                .order_by(CategoryGroup.name.asc())
            )
            groups = list(result.scalars().unique().all())

        translator = TranslationService(session)
        translated_groups = 0
        translated_categories = 0

        for group in groups:
            group_name = str(group.name or "").strip()
            if group_name and not str(group.name_ru or "").strip():
                try:
                    group.name_ru = await translator.translate(
                        group_name,
                        source_lang="DE",
                        target_lang="RU",
                        context="otto_category_group",
                    )
                    translated_groups += 1
                except TranslationError:
                    self.logger.exception(
                        "Не удалось перевести группу категории: group=%s",
                        group_name,
                    )

            for category in group.categories:
                category_name = str(category.name or "").strip()
                if not category_name or str(category.name_ru or "").strip():
                    continue
                try:
                    category.name_ru = await translator.translate(
                        category_name,
                        source_lang="DE",
                        target_lang="RU",
                        context=f"otto_category:{group_name}",
                    )
                    translated_categories += 1
                except TranslationError:
                    self.logger.exception(
                        "Не удалось перевести категорию: group=%s category=%s",
                        group_name,
                        category_name,
                    )

        if translated_groups or translated_categories:
            await session.commit()
            self.logger.info(
                "Переводы категорий сохранены: groups=%s categories=%s",
                translated_groups,
                translated_categories,
            )


async def main(): ...
