"""HTTP client for OTTO product endpoints with token-based authentication.

The client centralizes request headers, response parsing, and endpoint paths so
service classes can focus on orchestration instead of transport details.
"""

from datetime import datetime
from typing import Any, Optional

import httpx

from app.core.logger import logging
from app.core.otto_auth import OttoAuth
from app.schemas.enums import Controller

# Schemas
from app.schemas.product import CreateProductRequest, ProductBase, ProductResponse
from app.schemas.product_response import ProductCreateResponse, OttoCategoryResponse

# Helper
from app.utils.helpers import to_json, parse

LOGGER = logging.getLogger(__name__)


class OttoClient:
    """Low-level async client for calling OTTO product APIs."""

    def __init__(self, auth: OttoAuth, base_url: str, timeout: float):
        """Store auth provider and HTTP connection settings."""
        self.auth = auth
        self.base_url = base_url
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Return a reusable HTTP client so concurrent requests share connections."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        """Close the reusable HTTP client when the application lifecycle supports it."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def _header(self, controller: Controller | str = Controller.JV):
        """Build authenticated headers required by OTTO endpoints."""
        token = await self.auth.get_token(
            controller.value if isinstance(controller, Controller) else str(controller)
        )
        request_timestamp = (
            datetime.now().astimezone().isoformat(timespec="milliseconds")
        )
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Request-Timestamp": request_timestamp,
        }

    @staticmethod
    def _parse_response(response: httpx.Response):
        """Normalize HTTP responses to a consistent Python shape.

        JSON responses are returned as decoded objects, while empty/non-JSON
        responses are wrapped into explicit dictionaries to avoid downstream
        ambiguity.
        """
        if response.status_code == 204 or not response.content:
            return {"status_code": response.status_code, "message": "No content"}

        content_type = response.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            return response.json()

        return {
            "status_code": response.status_code,
            "content_type": content_type or None,
            "body": response.text,
        }

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        controller: Controller | str = Controller.JV,
    ) -> httpx.Response:
        """Execute an authenticated HTTP request and return the raw response."""
        return await self._get_client().request(
            method,
            f"{self.base_url}{path}",
            headers=await self._header(controller),
            params=params,
            json=json,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        controller: Controller | str = Controller.JV,
    ):
        """Execute an authenticated HTTP request and raise on non-2xx responses."""
        response = await self._send(
            method, path, params=params, json=json, controller=controller
        )
        response.raise_for_status()
        return self._parse_response(response)

    async def _request_with_status(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        controller: Controller | str = Controller.JV,
    ) -> tuple[int, Any]:
        """Execute an authenticated request and return both status and parsed body."""
        response = await self._send(
            method, path, params=params, json=json, controller=controller
        )
        return response.status_code, self._parse_response(response)

    async def update_status(
        self,
        payload: dict,
        controller: Controller | str = Controller.JV,
    ):
        """POST active status changes for products."""
        return await self._request(
            "POST",
            "/v5/products/active-status",
            json=payload,
            controller=controller,
        )

    async def update_status_with_status(
        self,
        payload: dict,
        controller: Controller | str = Controller.JV,
    ) -> tuple[int, Any]:
        """POST active status changes and keep the upstream status code."""
        return await self._request_with_status(
            "POST",
            "/v5/products/active-status",
            json=payload,
            controller=controller,
        )

    async def update_prices_with_status(
        self,
        payload: list[dict[str, Any]],
        controller: Controller | str = Controller.JV,
    ) -> tuple[int, Any]:
        """POST product variation price changes and keep the upstream status code."""
        return await self._request_with_status(
            "POST",
            "/v5/products/prices",
            json=payload,
            controller=controller,
        )

    async def get_product(self, sku: str) -> ProductResponse:
        """GET a single product by SKU."""
        response = await self._request("GET", f"/v5/products/{sku}")
        LOGGER.debug("Получен продукт OTTO: sku=%s response=%s", sku, response)
        return ProductResponse.model_validate(response)

    async def get_shipping_profiles(self, controller: Controller | str = Controller.JV):
        """GET all shipment profiles"""
        return await self._request(
            "GET", "/v1/shipping-profiles", controller=controller
        )

    async def get_product_with_status(self, sku: str) -> tuple[int, Any]:
        """GET a single product by SKU without raising, returning status and body."""
        return await self._request_with_status("GET", f"/v5/products/{sku}")

    async def get_products(
        self,
        payload: dict | None = None,
        controller: Controller | str = Controller.JV,
    ) -> ProductResponse:
        """GET paginated products list."""
        response = await self._request(
            "GET",
            "/v5/products",
            params=payload,
            controller=controller,
        )
        return ProductResponse.model_validate(response)

    async def get_products_raw(
        self,
        payload: dict | None = None,
        controller: Controller | str = Controller.JV,
    ) -> Any:
        """GET paginated products list without schema validation."""
        return await self._request(
            "GET",
            "/v5/products",
            params=payload,
            controller=controller,
        )

    async def get_active_products(
        self,
        payload: dict | None = None,
        controller: Controller | str = Controller.JV,
    ):
        """GET active-status list."""
        return await self._request(
            "GET",
            "/v5/products/active-status",
            params=payload,
            controller=controller,
        )

    async def update_tasks(
        self, pid: str, controller: Controller | str = Controller.JV
    ):
        """Trigger update tasks for a product-processing identifier."""
        return await self._request(
            "GET", f"/v5/products/update-tasks/{pid}", controller=controller
        )

    async def failed_tasks(
        self, pid: str, controller: Controller | str = Controller.JV
    ):
        return await self._request(
            "GET", f"/v5/products/update-tasks/{pid}/failed", controller=controller
        )

    async def succeeded_tasks(
        self, pid: str, controller: Controller | str = Controller.JV
    ):
        return await self._request(
            "GET", f"/v5/products/update-tasks/{pid}/succeeded", controller=controller
        )

    async def unchanged_tasks(
        self, pid: str, controller: Controller | str = Controller.JV
    ):
        return await self._request(
            "GET", f"/v5/products/update-tasks/{pid}/unchanged", controller=controller
        )

    async def get_marketplace_status(
        self,
        payload: dict | None = None,
        controller: Controller | str = Controller.JV,
    ):
        """GET marketplace status information with optional filters."""
        return await self._request(
            "GET",
            "/v5/products/marketplace-status",
            params=payload,
            controller=controller,
        )

    async def create_or_update_products_raw(
        self,
        products: list[dict[str, Any]],
        controller: Controller | str = Controller.JV,
    ) -> Any:
        """POST raw product payloads to OTTO without local Pydantic coercion."""
        return await self._request(
            "POST",
            "/v5/products",
            json=products,
            controller=controller,
        )

    async def update_quantity(
        self,
        payload: Optional[dict | list] = None,
        controller: Controller | str = Controller.JV,
    ):
        """POST marketplace quantity for given sku(product)"""
        request_payload = payload if isinstance(payload, list) else [payload]
        LOGGER.info(
            "step=otto_update_quantity_request controller=%s payload=%s",
            controller.value if isinstance(controller, Controller) else controller,
            request_payload,
        )
        if isinstance(payload, list):
            return await self._request(
                "POST",
                "/v1/availability/quantities",
                json=payload,
                controller=controller,
            )

        return await self._request(
            "POST",
            "/v1/availability/quantities",
            json=[payload],
            controller=controller,
        )

    async def update_quantity_with_status(
        self,
        payload: Optional[dict | list] = None,
        controller: Controller | str = Controller.JV,
    ) -> tuple[int, Any]:
        """POST marketplace quantity and keep 207/422 details for callers."""
        request_payload = payload if isinstance(payload, list) else [payload]
        LOGGER.info(
            "step=otto_update_quantity_request controller=%s payload=%s",
            controller.value if isinstance(controller, Controller) else controller,
            request_payload,
        )
        return await self._request_with_status(
            "POST",
            "/v1/availability/quantities",
            json=request_payload,
            controller=controller,
        )

    async def update_product_delivery_information(
        self,
        payload: dict | None = None,
        controller: Controller | str = Controller.JV,
    ):
        """POST product shippinig profile with given SKU"""
        LOGGER.info(
            "step=otto_update_delivery_request controller=%s payload=%s",
            controller.value if isinstance(controller, Controller) else controller,
            [payload],
        )
        return await self._request(
            "POST",
            "/v1/availability/product-delivery-information",
            json=[payload],
            controller=controller,
        )

    async def create_or_update_products(
        self,
        payload: ProductBase,
        controller: Controller | str = Controller.JV,
    ) -> ProductCreateResponse:
        """POST product payloads for create/upsert operations."""
        json_payload = to_json(payload)
        response_data = await self._request(
            "POST", "/v5/products", json=json_payload, controller=controller
        )

        return parse(ProductCreateResponse, response_data)

    async def get_categories(
        self,
        payload: dict,
        controller: Controller | str = Controller.JV,
    ) -> OttoCategoryResponse:
        """Fetch category values from OTTO category-group responses."""

        response_data = await self._request(
            "GET",
            "/v5/products/categories",
            params=payload,
            controller=controller,
        )
        if isinstance(response_data, list):
            response_data = {"categoryGroups": response_data}

        return parse(OttoCategoryResponse, response_data)
