import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import httpx

from app.clients.external_otto_client import ExternalOttoClient, MAX_RETRIES
from app.core.otto_auth import OttoAuth
from app.core.otto_exceptions import (
    OttoApiException,
    OttoAuthenticationError,
    OttoNotFoundError,
    OttoRateLimitError,
)
from app.schemas.external_schemes.until_schemes import (
    DeliveryInformation,
    DeliveryInformationRequest,
    GetProductRequest,
    Quantity,
    QuantityRequest,
)


@pytest.fixture
def mock_auth():
    auth = MagicMock(spec=OttoAuth)
    auth.timeout = 10.0
    auth.get_token = AsyncMock(return_value="fake-token.header.signature")
    return auth


@pytest.fixture
def client(mock_auth):
    return ExternalOttoClient(auth=mock_auth, base_url="https://api.otto.market", timeout=10.0)


@pytest.mark.asyncio
async def test_persistent_client_lifecycle(client):
    c1 = await client._get_client()
    assert isinstance(c1, httpx.AsyncClient)
    assert not c1.is_closed

    c2 = await client._get_client()
    assert c1 is c2

    await client.close()
    assert client._client is None


@pytest.mark.asyncio
async def test_request_success_200(client):
    mock_response = httpx.Response(
        200,
        json={"data": "ok"},
        request=httpx.Request("GET", "https://api.otto.market/v5/products"),
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_response

        resp = await client._request("GET", "/v5/products", "jv", idempotent=True)
        assert resp.status_code == 200
        assert mock_req.call_count == 1

        mock_auth_token = await client.auth.get_token("jv")
        called_headers = mock_req.call_args[1]["headers"]
        assert called_headers["Authorization"] == f"Bearer {mock_auth_token}"


@pytest.mark.asyncio
async def test_request_raises_otto_not_found_error_404(client):
    mock_response = httpx.Response(
        404,
        json={"error": "Not Found"},
        request=httpx.Request("GET", "https://api.otto.market/v1/availability/quantities/UNKNOWN"),
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_response

        with pytest.raises(OttoNotFoundError) as exc_info:
            await client.get_quantity("UNKNOWN", "jv")

        assert exc_info.value.status_code == 404
        assert exc_info.value.endpoint == "/v1/availability/quantities/UNKNOWN"
        assert exc_info.value.controller == "jv"
        assert isinstance(exc_info.value, httpx.HTTPStatusError)
        assert isinstance(exc_info.value, OttoApiException)


@pytest.mark.asyncio
async def test_request_raises_otto_auth_error_401(client):
    mock_response = httpx.Response(
        401,
        json={"error": "unauthorized"},
        request=httpx.Request("GET", "https://api.otto.market/v5/products"),
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_response

        with pytest.raises(OttoAuthenticationError) as exc_info:
            await client._request("GET", "/v5/products", "jv")

        assert exc_info.value.status_code == 401
        assert isinstance(exc_info.value, OttoApiException)


@pytest.mark.asyncio
async def test_request_raises_otto_api_exception_400(client):
    mock_response = httpx.Response(
        400,
        json={"error": "bad request", "details": ["invalid field"]},
        request=httpx.Request("POST", "https://api.otto.market/v5/products"),
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_response

        with pytest.raises(OttoApiException) as exc_info:
            await client.create_or_update_product([{"sku": "SKU1"}], "jv")

        assert exc_info.value.status_code == 400
        assert exc_info.value.error_details == {"error": "bad request", "details": ["invalid field"]}


@pytest.mark.asyncio
async def test_retry_on_503_for_idempotent_operation(client):
    resp_503 = httpx.Response(
        503,
        json={"error": "service unavailable"},
        request=httpx.Request("GET", "https://api.otto.market/v1/shipping-profiles"),
    )
    resp_200 = httpx.Response(
        200,
        json={"results": [{"id": "std", "name": "Standard"}]},
        request=httpx.Request("GET", "https://api.otto.market/v1/shipping-profiles"),
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_req, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_req.side_effect = [resp_503, resp_200]

        result = await client.get_shipping_profiles("jv")

        assert result == {"results": [{"id": "std", "name": "Standard"}]}
        assert mock_req.call_count == 2
        mock_sleep.assert_called_once_with(1.0)


@pytest.mark.asyncio
async def test_no_retry_for_non_idempotent_operation(client):
    """create_or_update_product must not retry on 503 or 429 to avoid duplicate creation."""
    resp_503 = httpx.Response(
        503,
        json={"error": "service unavailable"},
        request=httpx.Request("POST", "https://api.otto.market/v5/products"),
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_req, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_req.return_value = resp_503

        with pytest.raises(OttoApiException) as exc_info:
            await client.create_or_update_product([{"sku": "SKU-NEW"}], "jv")

        assert exc_info.value.status_code == 503
        assert mock_req.call_count == 1
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_retry_with_retry_after_header(client):
    resp_429 = httpx.Response(
        429,
        headers={"retry-after": "2"},
        json={"error": "rate limited"},
        request=httpx.Request("GET", "https://api.otto.market/v5/products"),
    )
    resp_200 = httpx.Response(
        200,
        json={"resources": []},
        request=httpx.Request("GET", "https://api.otto.market/v5/products"),
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_req, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_req.side_effect = [resp_429, resp_200]

        payload = GetProductRequest(page=1)
        res = await client.get_products(payload, "jv")

        assert res == {"resources": []}
        assert mock_req.call_count == 2
        mock_sleep.assert_called_once_with(2.0)


@pytest.mark.asyncio
async def test_rate_limit_exhausted_raises_otto_rate_limit_error(client):
    resp_429 = httpx.Response(
        429,
        headers={"retry-after": "5"},
        json={"error": "rate limited"},
        request=httpx.Request("GET", "https://api.otto.market/v5/products"),
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_req, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_req.return_value = resp_429

        with pytest.raises(OttoRateLimitError) as exc_info:
            await client._request("GET", "/v5/products", "jv", idempotent=True)

        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 5.0
        assert mock_req.call_count == MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_update_active_status_does_not_raise_on_error(client):
    """update_active_status preserves contract of returning (status_code, parsed_response)."""
    resp_400 = httpx.Response(
        400,
        json={"message": "Invalid SKU"},
        request=httpx.Request("POST", "https://api.otto.market/v5/products/active-status"),
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = resp_400

        status_code, parsed = await client.update_active_status({"status": []}, "jv")
        assert status_code == 400
        assert parsed == {"message": "Invalid SKU"}


@pytest.mark.asyncio
async def test_public_methods_contracts(client):
    """Verify all public methods execute through _request without altering signatures."""
    job_id = uuid4()

    mock_200 = httpx.Response(
        200,
        json={"ok": True},
        request=httpx.Request("GET", "https://api.otto.market/test"),
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_200

        # 1. get_shipping_profile_for_product
        res = await client.get_shipping_profile_for_product("SKU1", "jv")
        assert res == {"ok": True}

        # 2. get_quantity
        res = await client.get_quantity("SKU1", "jv")
        assert res == {"ok": True}

        # 3. update_quantity
        res = await client.update_quantity(QuantityRequest([Quantity(sku="SKU1", quantity=5)]), "jv")
        assert res == {"ok": True}

        # 4. update_product_delivery
        res = await client.update_product_delivery(
            DeliveryInformationRequest([DeliveryInformation(sku="SKU1", shipping_profile_id="p1")]),
            "jv",
        )
        assert res == {"ok": True}

        # 5. get_categories
        res = await client.get_categories({"category": "test"}, "jv")
        assert res == {"ok": True}

        # 6. read_update_task_result
        res = await client.read_update_task_result(job_id, "jv")
        assert res == {"ok": True}
