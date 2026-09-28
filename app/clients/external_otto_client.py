import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx

from app.core.logger import format_log_payload
from app.core.otto_auth import OttoAuth
from app.core.otto_exceptions import (
    OttoApiException,
    OttoAuthenticationError,
    OttoNotFoundError,
    OttoRateLimitError,
)
from app.schemas.enums import Controller
from app.schemas.external_schemes.until_schemes import (
    CreateOrUpdateProductVariationRequest,
    DeliveryInformationRequest,
    GetProductRequest,
    QuantityRequest,
)

logger = logging.getLogger("external_api")

RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
MAX_RETRIES = 2


class ExternalOttoClient:
    """Client for OTTO Market API interactions."""

    def __init__(self, auth: OttoAuth, base_url: str, timeout: float = 30.0):
        self.auth = auth
        self.base_url = base_url.rstrip("/")
        self.timeout = float(getattr(auth, "timeout", timeout) or timeout)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or initialize persistent HTTP client with connection pooling."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        """Close persistent HTTP client connection pool."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _build_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        normalized = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}{normalized}"

    @staticmethod
    def _parse_retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                val = float(retry_after)
                if 0 < val <= 60:
                    return val
            except ValueError:
                pass
        return 1.0 * (3.0 ** (attempt - 1))

    @staticmethod
    def _controller_value(controller: Controller | str) -> str:
        return (
            controller.value if isinstance(controller, Controller) else str(controller)
        )

    @staticmethod
    def _decode_token_metadata(token: str | None) -> dict[str, Any]:
        if not token:
            return {"token_present": False}

        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(payload))
        except Exception as exc:
            return {"token_present": True, "decode_error": type(exc).__name__}

        exp = decoded.get("exp")
        iat = decoded.get("iat")
        return {
            "token_present": True,
            "client_id": decoded.get("client_id"),
            "partner_id": decoded.get("partner_id"),
            "partner_name": decoded.get("partner_name"),
            "scope": decoded.get("scope"),
            "roles": decoded.get("realm_access", {}).get("roles"),
            "iat": (
                datetime.fromtimestamp(iat, tz=timezone.utc).isoformat()
                if iat
                else None
            ),
            "exp": (
                datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
                if exp
                else None
            ),
        }

    @staticmethod
    def _safe_response_headers(response: httpx.Response) -> dict[str, str]:
        useful_headers = {
            "content-type",
            "date",
            "server",
            "www-authenticate",
            "x-request-id",
            "x-correlation-id",
            "x-trace-id",
            "traceparent",
            "ratelimit-limit",
            "ratelimit-remaining",
            "ratelimit-reset",
            "retry-after",
        }
        return {
            key: value
            for key, value in response.headers.items()
            if key.lower() in useful_headers
        }

    def _log_http_error(
        self,
        response: httpx.Response,
        *,
        endpoint: str,
        controller: Controller | str,
        params: dict[str, Any] | None = None,
    ) -> None:
        www_authenticate = response.headers.get("www-authenticate", "")
        auth_hint = None
        if "invalid_token" in www_authenticate and "Invalid issuer" in www_authenticate:
            auth_hint = (
                "OTTO rejected the token issuer for this endpoint. "
                "Check whether this API path requires a different OTTO auth flow "
                "or app/installation access token."
            )

        logger.error(
            "OTTO request failed: %s %s controller=%s status=%s\nurl: %s\nparams:\n%s\nheaders:\n%s\nresponse:\n%s\nauth_hint: %s",
            response.request.method,
            endpoint,
            self._controller_value(controller),
            response.status_code,
            str(response.request.url),
            format_log_payload(params or {}),
            format_log_payload(self._safe_response_headers(response)),
            format_log_payload(self._parse_response(response)),
            auth_hint,
        )

    def _build_otto_exception(
        self,
        response: httpx.Response,
        endpoint: str,
        controller: str,
        parsed_error: Any,
    ) -> OttoApiException:
        status_code = response.status_code
        message = (
            f"OTTO API request failed: {response.request.method} {endpoint} "
            f"controller={controller} status={status_code}"
        )
        if status_code == 404:
            return OttoNotFoundError(
                message,
                request=response.request,
                response=response,
                endpoint=endpoint,
                controller=controller,
                error_details=parsed_error,
            )
        if status_code in (401, 403):
            return OttoAuthenticationError(
                message,
                request=response.request,
                response=response,
                endpoint=endpoint,
                controller=controller,
                error_details=parsed_error,
            )
        if status_code == 429:
            retry_after = None
            retry_after_str = response.headers.get("retry-after")
            if retry_after_str:
                try:
                    retry_after = float(retry_after_str)
                except ValueError:
                    pass
            return OttoRateLimitError(
                message,
                request=response.request,
                response=response,
                endpoint=endpoint,
                controller=controller,
                error_details=parsed_error,
                retry_after=retry_after,
            )
        return OttoApiException(
            message,
            request=response.request,
            response=response,
            endpoint=endpoint,
            controller=controller,
            error_details=parsed_error,
        )

    async def _header(self, controller: Controller | str) -> dict[str, str]:
        controller_value = self._controller_value(controller)
        token = await self.auth.get_token(controller_value)
        logger.info(
            "Got OTTO token metadata controller=%s\n%s",
            controller_value,
            format_log_payload(self._decode_token_metadata(token)),
        )
        request_timestamp = (
            datetime.now().astimezone().isoformat(timespec="milliseconds")
        )

        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Request-Timestamp": request_timestamp,
        }

    @staticmethod
    def _parse_response(response: httpx.Response) -> Any:
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

    async def _request(
        self,
        method: str,
        path: str,
        controller: Controller | str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: Any = None,
        idempotent: bool = True,
        raise_for_status: bool = True,
    ) -> httpx.Response:
        """Centralized HTTP request engine with persistent client, retries, and unified logging.

        Retry logic is explicitly governed by the `idempotent` flag and operation semantics,
        not automatically inferred solely from HTTP methods.
        """
        controller_value = self._controller_value(controller)
        header = await self._header(controller)
        client = await self._get_client()
        url = self._build_url(path)

        max_attempts = (MAX_RETRIES + 1) if idempotent else 1

        for attempt in range(1, max_attempts + 1):
            start_time = time.monotonic()
            logger.info(
                "[OTTO REQ] %s %s controller=%s",
                method,
                path,
                controller_value,
            )
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "[OTTO REQ DETAIL] %s %s params:\n%s\npayload:\n%s",
                    method,
                    path,
                    format_log_payload(params or {}),
                    format_log_payload(json_payload or {}),
                )

            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=header,
                    params=params,
                    json=json_payload,
                )
            except httpx.TimeoutException as exc:
                duration_ms = (time.monotonic() - start_time) * 1000
                logger.error(
                    "[OTTO TIMEOUT] %s %s controller=%s duration=%.0fms: %s",
                    method,
                    path,
                    controller_value,
                    duration_ms,
                    exc,
                )
                raise
            except httpx.RequestError as exc:
                duration_ms = (time.monotonic() - start_time) * 1000
                logger.error(
                    "[OTTO REQUEST ERROR] %s %s controller=%s duration=%.0fms: %s",
                    method,
                    path,
                    controller_value,
                    duration_ms,
                    exc,
                )
                raise

            duration_ms = (time.monotonic() - start_time) * 1000

            if (
                idempotent
                and response.status_code in RETRYABLE_STATUS_CODES
                and attempt < max_attempts
            ):
                delay = self._parse_retry_delay(response, attempt)
                logger.warning(
                    "[OTTO RETRY] %s %s controller=%s status=%s attempt=%d/%d delay=%.1fs",
                    method,
                    path,
                    controller_value,
                    response.status_code,
                    attempt,
                    MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            break

        if response.status_code >= 400:
            logger.error(
                "[OTTO ERROR] %s %s controller=%s status=%s duration=%.0fms",
                method,
                path,
                controller_value,
                response.status_code,
                duration_ms,
            )
            self._log_http_error(
                response,
                endpoint=path,
                controller=controller,
                params=params or (json_payload if isinstance(json_payload, dict) else None),
            )
            if raise_for_status:
                parsed_error = self._parse_response(response)
                raise self._build_otto_exception(
                    response=response,
                    endpoint=path,
                    controller=controller_value,
                    parsed_error=parsed_error,
                )
        else:
            logger.info(
                "[OTTO RESP] %s %s controller=%s status=%s duration=%.0fms",
                method,
                path,
                controller_value,
                response.status_code,
                duration_ms,
            )

        return response

    async def get_products(
        self, payload: GetProductRequest, controller: Controller | str
    ):
        params = payload.model_dump(mode="json", exclude_none=True)
        response = await self._request(
            "GET",
            "/v5/products",
            controller,
            params=params,
            idempotent=True,
        )
        return self._parse_response(response)

    async def get_shipping_profile_for_product(
        self, sku: str, controller: Controller | str
    ):
        response = await self._request(
            "GET",
            f"/v1/availability/product-delivery-information/{sku}",
            controller,
            idempotent=True,
        )
        return self._parse_response(response)

    async def update_active_status(self, payload: dict, controller: str):
        response = await self._request(
            "POST",
            "/v5/products/active-status",
            controller,
            json_payload=payload,
            idempotent=True,
            raise_for_status=False,
        )
        return response.status_code, self._parse_response(response)

    async def create_or_update_product(
        self,
        payload: CreateOrUpdateProductVariationRequest | list[dict[str, Any]],
        controller: str,
    ) -> dict[str, Any]:
        json_payload = (
            payload.model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(payload, CreateOrUpdateProductVariationRequest)
            else payload
        )
        response = await self._request(
            "POST",
            "/v5/products",
            controller,
            json_payload=json_payload,
            idempotent=False,
        )
        parsed = self._parse_response(response)
        logger.info("RESPONSE: %s", parsed)
        return parsed

    async def get_quantity(self, sku: str, controller: Controller | str) -> dict[str, Any]:
        response = await self._request(
            "GET",
            f"/v1/availability/quantities/{sku}",
            controller,
            idempotent=True,
        )
        return self._parse_response(response)

    async def update_quantity(self, payload: QuantityRequest, controller: str):
        response = await self._request(
            "POST",
            "/v1/availability/quantities",
            controller,
            json_payload=payload.model_dump(mode="json", by_alias=True),
            idempotent=True,
        )
        return self._parse_response(response)

    async def update_product_delivery(
        self, payload: DeliveryInformationRequest, controller
    ):
        response = await self._request(
            "POST",
            "/v1/availability/product-delivery-information",
            controller,
            json_payload=payload.model_dump(mode="json", by_alias=True),
            idempotent=True,
        )
        return self._parse_response(response)

    async def get_shipping_profiles(self, controller: str):
        response = await self._request(
            "GET",
            "/v1/shipping-profiles",
            controller,
            idempotent=True,
        )
        return self._parse_response(response)

    async def get_categories(self, payload: dict, controller: str):
        response = await self._request(
            "GET",
            "/v5/products/categories",
            controller,
            params=payload,
            idempotent=True,
        )
        return self._parse_response(response)

    async def read_update_task_result(self, job_id: UUID, controller: str):
        response = await self._request(
            "GET",
            f"/v5/products/update-tasks/{job_id}",
            controller,
            idempotent=True,
        )
        return self._parse_response(response)
