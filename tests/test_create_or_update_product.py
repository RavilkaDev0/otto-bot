import pytest
from uuid import UUID
from unittest.mock import AsyncMock, MagicMock
from pydantic import ValidationError
from app.services.extermal_service import ExternalService
import httpx
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.dependencies import get_external_api_service, get_rabbitmq_publisher
from app.core.otto_exceptions import OttoRateLimitError, OttoApiException
from app.schemas.external_schemes.until_schemes import (
    CreateOrUpdateProductVariationRequest,
    Controller,
    UpdateTaskResponse,
    GetProductRequest,
    Quantity,
    DeliveryInformation,
    CreateDeliveryJob,
    AvailabilityRequest,
    QuantityRequest,
    DeliveryInformationRequest,
)


def _sample_variation(sku: str, ean: str, shipping_profile_id: str = "sp-123"):
    return {
        "productReference": "REF-1",
        "sku": sku,
        "ean": ean,
        "shippingProfileId": shipping_profile_id,
        "quantity": 15,
        "productDescription": {
            "category": "Sofa",
            "productLine": "Modern",
            "bulletPoints": [],
            "attributes": [],
        },
        "mediaAssets": [
            {
                "type": "IMAGE",
                "location": "https://example.com/img.jpg",
            }
        ],
        "delivery": {
            "type": "PARCEL",
            "deliveryTime": 2,
        },
        "pricing": {
            "standardPrice": {"amount": 299.0, "currency": "EUR"},
            "vat": "FULL",
        },
    }


@pytest.mark.asyncio
async def test_create_or_update_product_multiple_variations():
    mock_client = MagicMock()
    mock_client.create_or_update_product = AsyncMock(
        return_value={
            "links": [{"href": "/v5/products/update-tasks/ec956c9d-4141-4c48-8df0-e04f4c20f188"}]
        }
    )
    mock_publisher = MagicMock()
    mock_publisher.publish = AsyncMock()

    service = ExternalService(client=mock_client)
    payload = CreateOrUpdateProductVariationRequest.model_validate(
        [
            _sample_variation("SKU-1", "407148900001", "sp-1"),
            _sample_variation("SKU-2", "407148900002", "sp-2"),
        ]
    )

    result = await service.create_or_update_product(
        payload=payload,
        controller=Controller.JV,
        publisher=mock_publisher,
    )

    assert result["status"] == "accepted"
    assert result["job_id"] == "ec956c9d-4141-4c48-8df0-e04f4c20f188"
    assert result["marketplace_job_id"] == "ec956c9d-4141-4c48-8df0-e04f4c20f188"
    assert result["skus"] == ["SKU-1", "SKU-2"]

    # Verify RabbitMQ job contains all variations
    mock_publisher.publish.assert_awaited_once()
    published_job = mock_publisher.publish.await_args.args[0]
    assert published_job.marketplace_job_id == UUID("ec956c9d-4141-4c48-8df0-e04f4c20f188")
    assert len(published_job.availability_data.quantities.root) == 2
    assert len(published_job.availability_data.delivery_information.root) == 2
    assert published_job.availability_data.quantities.root[0].sku == "SKU-1"
    assert published_job.availability_data.quantities.root[1].sku == "SKU-2"


def test_extract_marketplace_job_id_success():
    service = ExternalService(client=MagicMock())
    response = {
        "links": [{"href": "/v5/products/update-tasks/12345678-1234-5678-1234-567812345678"}]
    }
    job_id = service._extract_marketplace_job_id(response)
    assert job_id == UUID("12345678-1234-5678-1234-567812345678")


def test_extract_marketplace_job_id_missing_links():
    service = ExternalService(client=MagicMock())
    with pytest.raises(RuntimeError, match="does not contain valid 'links'"):
        service._extract_marketplace_job_id({"error": "Bad request"})


@pytest.mark.asyncio
async def test_wait_for_update_task_done():
    mock_client = MagicMock()
    mock_client.read_update_task_result = AsyncMock(
        return_value={"state": "done", "total": 1, "succeeded": 1}
    )
    service = ExternalService(client=mock_client)
    res = await service._wait_for_update_task(UUID("12345678-1234-5678-1234-567812345678"), Controller.JV)
    assert res.state == "done"
    assert res.succeeded == 1


@pytest.mark.asyncio
async def test_wait_for_update_task_failed_state():
    mock_client = MagicMock()
    mock_client.read_update_task_result = AsyncMock(
        return_value={"state": "failed", "message": "Schema error", "total": 1, "failed": 1}
    )
    service = ExternalService(client=mock_client)
    res = await service._wait_for_update_task(UUID("12345678-1234-5678-1234-567812345678"), Controller.JV)
    assert res.state == "failed"
    assert res.message == "Schema error"


@pytest.mark.asyncio
async def test_wait_for_update_task_timeout():
    mock_client = MagicMock()
    mock_client.read_update_task_result = AsyncMock(
        return_value={"state": "pending"}
    )
    service = ExternalService(client=mock_client)
    with pytest.raises(TimeoutError, match="did not finish"):
        await service._wait_for_update_task(
            UUID("12345678-1234-5678-1234-567812345678"),
            Controller.JV,
            max_attempts=2,
            interval_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_product_variation_quantity_and_stock_preservation():
    """Verify that custom quantity/stock is preserved and not overwritten by default 20."""
    # 1. Custom stock=30 is preserved
    var1 = _sample_variation("SKU-1", "407148900001")
    del var1["quantity"]
    var1["stock"] = 30
    req1 = CreateOrUpdateProductVariationRequest.model_validate([var1])
    assert req1.root[0].quantity == 30

    # 2. Custom quantity=50 is preserved
    var2 = _sample_variation("SKU-2", "407148900002")
    var2["quantity"] = 50
    req2 = CreateOrUpdateProductVariationRequest.model_validate([var2])
    assert req2.root[0].quantity == 50

    # 3. Missing quantity and stock falls back to 20
    var3 = _sample_variation("SKU-3", "407148900003")
    del var3["quantity"]
    req3 = CreateOrUpdateProductVariationRequest.model_validate([var3])
    assert req3.root[0].quantity == 20

    # 4. Explicit None falls back to 20
    var4 = _sample_variation("SKU-4", "407148900004")
    var4["quantity"] = None
    req4 = CreateOrUpdateProductVariationRequest.model_validate([var4])
    assert req4.root[0].quantity == 20

    # 5. Full flow publishes correct quantity to RabbitMQ
    mock_client = MagicMock()
    mock_client.create_or_update_product = AsyncMock(
        return_value={"links": [{"href": "/v5/products/update-tasks/12345678-1234-5678-1234-567812345678"}]}
    )
    mock_publisher = MagicMock()
    mock_publisher.publish = AsyncMock()

    service = ExternalService(client=mock_client)
    await service.create_or_update_product(
        payload=req1,
        controller=Controller.JV,
        publisher=mock_publisher,
    )
    published_job = mock_publisher.publish.await_args.args[0]
    assert published_job.availability_data.quantities.root[0].quantity == 30


def test_sku_length_validation_success():
    """Verify SKU lengths from 1 to 50 are accepted across schemas."""
    skus = [
        "1",
        "4003002005002",
        "JVM4062292005176",
        "4062292005176_SOFORT_JV",
        "A" * 50,
    ]
    for s in skus:
        # GetProductRequest
        req = GetProductRequest(sku=s)
        assert req.sku == s

        # Quantity
        q = Quantity(sku=s, quantity=10)
        assert q.sku == s

        # DeliveryInformation
        d = DeliveryInformation(sku=s, shipping_profile_id="sp-1")
        assert d.sku == s

        # ProductVariationRequest
        var = _sample_variation(sku=s, ean="407148900001")
        pvr = CreateOrUpdateProductVariationRequest.model_validate([var])
        assert pvr.root[0].sku == s


def test_sku_length_validation_failure():
    """Verify SKU exceeding 50 chars is rejected."""
    too_long_sku = "A" * 51
    with pytest.raises(ValidationError):
        GetProductRequest(sku=too_long_sku)

    with pytest.raises(ValidationError):
        Quantity(sku=too_long_sku, quantity=5)

    with pytest.raises(ValidationError):
        DeliveryInformation(sku=too_long_sku, shipping_profile_id="sp-1")

    var = _sample_variation(sku=too_long_sku, ean="407148900001")
    with pytest.raises(ValidationError):
        CreateOrUpdateProductVariationRequest.model_validate([var])


@pytest.mark.asyncio
async def test_create_or_update_availability_when_unchanged():
    """When OTTO returns succeeded=0, unchanged=1, failed=0, availability should still run."""
    mock_client = MagicMock()
    mock_client.read_update_task_result = AsyncMock(
        return_value={"state": "done", "total": 1, "succeeded": 0, "unchanged": 1, "failed": 0}
    )
    mock_client.update_quantity = AsyncMock(return_value={"status": "ok"})
    mock_client.update_product_delivery = AsyncMock(return_value={"status": "ok"})

    service = ExternalService(client=mock_client)

    job = CreateDeliveryJob(
        marketplace_job_id=UUID("12345678-1234-5678-1234-567812345678"),
        availability_data=AvailabilityRequest(
            quantities=QuantityRequest.model_validate([Quantity(sku="JVM4062292005176", quantity=25)]),
            delivery_information=DeliveryInformationRequest.model_validate(
                [DeliveryInformation(sku="JVM4062292005176", shipping_profile_id="sp-test")]
            ),
        ),
        controller=Controller.JV,
    )

    await service.create_or_update_availability(job)

    mock_client.update_quantity.assert_awaited_once()
    mock_client.update_product_delivery.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_or_update_availability_aborts_when_failed():
    """When OTTO returns succeeded=0, unchanged=0, failed=1, availability should NOT run."""
    mock_client = MagicMock()
    mock_client.read_update_task_result = AsyncMock(
        return_value={"state": "done", "total": 1, "succeeded": 0, "unchanged": 0, "failed": 1}
    )
    mock_client.update_quantity = AsyncMock()
    mock_client.update_product_delivery = AsyncMock()

    service = ExternalService(client=mock_client)

    job = CreateDeliveryJob(
        marketplace_job_id=UUID("12345678-1234-5678-1234-567812345678"),
        availability_data=AvailabilityRequest(
            quantities=QuantityRequest.model_validate([Quantity(sku="SKU-FAIL", quantity=25)]),
            delivery_information=DeliveryInformationRequest.model_validate(
                [DeliveryInformation(sku="SKU-FAIL", shipping_profile_id="sp-test")]
            ),
        ),
        controller=Controller.JV,
    )

    await service.create_or_update_availability(job)

    mock_client.update_quantity.assert_not_awaited()
    mock_client.update_product_delivery.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_or_update_product_otto_rate_limit_error_bubbles_up():
    """Verify ExternalService logs warning and re-raises OttoRateLimitError."""
    mock_client = MagicMock()
    req = httpx.Request("POST", "https://api.otto.market/v5/products")
    resp = httpx.Response(429, headers={"retry-after": "3"}, request=req)
    mock_client.create_or_update_product = AsyncMock(
        side_effect=OttoRateLimitError(
            "rate limit",
            request=req,
            response=resp,
            endpoint="/v5/products",
            controller="jv",
            retry_after=3.0,
        )
    )
    service = ExternalService(client=mock_client)
    payload = CreateOrUpdateProductVariationRequest.model_validate(
        [_sample_variation("SKU-1", "407148900001", "sp-1")]
    )

    with pytest.raises(OttoRateLimitError) as exc_info:
        await service.create_or_update_product(
            payload=payload,
            controller=Controller.JV,
            publisher=MagicMock(),
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after == 3.0


@pytest.mark.asyncio
async def test_create_or_update_product_endpoint_returns_429_on_rate_limit():
    """Verify that when OttoRateLimitError is raised, the endpoint returns HTTP 429 with Retry-After header."""
    mock_service = MagicMock()
    req = httpx.Request("POST", "https://api.otto.market/v5/products")
    resp = httpx.Response(429, headers={"retry-after": "4"}, request=req)
    mock_service.create_or_update_product = AsyncMock(
        side_effect=OttoRateLimitError(
            "rate limit",
            request=req,
            response=resp,
            endpoint="/v5/products",
            controller="jv",
            retry_after=4.0,
        )
    )

    app.dependency_overrides[get_external_api_service] = lambda: mock_service
    app.dependency_overrides[get_rabbitmq_publisher] = lambda: MagicMock()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/extermal/create_or_update_product",
                params={"controller": "jv"},
                json=[_sample_variation("SKU-1", "407148900001", "sp-1")],
            )

            assert response.status_code == 429
            assert response.headers.get("retry-after") == "4"
            data = response.json()
            assert data["error"] == "rate_limit_exceeded"
            assert "OTTO API rate limit exceeded" in data["detail"]
            assert data["retry_after"] == 4.0
            assert data["controller"] == "jv"
    finally:
        app.dependency_overrides.pop(get_external_api_service, None)
        app.dependency_overrides.pop(get_rabbitmq_publisher, None)


@pytest.mark.asyncio
async def test_create_or_update_product_endpoint_returns_upstream_status_on_otto_api_exception():
    """Verify that when generic OttoApiException (e.g. 400 Bad Request) is raised, it returns appropriate status code."""
    mock_service = MagicMock()
    req = httpx.Request("POST", "https://api.otto.market/v5/products")
    resp = httpx.Response(400, request=req)
    mock_service.create_or_update_product = AsyncMock(
        side_effect=OttoApiException(
            "OTTO API request failed: POST /v5/products controller=jv status=400",
            request=req,
            response=resp,
            endpoint="/v5/products",
            controller="jv",
        )
    )

    app.dependency_overrides[get_external_api_service] = lambda: mock_service
    app.dependency_overrides[get_rabbitmq_publisher] = lambda: MagicMock()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/extermal/create_or_update_product",
                params={"controller": "jv"},
                json=[_sample_variation("SKU-1", "407148900001", "sp-1")],
            )

            assert response.status_code == 400
            data = response.json()
            assert "OTTO API error" in data["detail"]
            assert data["otto_status_code"] == 400
            assert data["controller"] == "jv"
    finally:
        app.dependency_overrides.pop(get_external_api_service, None)
        app.dependency_overrides.pop(get_rabbitmq_publisher, None)


@pytest.mark.asyncio
async def test_create_or_update_product_response_contains_job_id():
    """Verify endpoint POST /extermal/create_or_update_product returns job_id."""
    mock_service = MagicMock()
    mock_service.create_or_update_product = AsyncMock(
        return_value={
            "status": "accepted",
            "job_id": "ec956c9d-4141-4c48-8df0-e04f4c20f188",
            "marketplace_job_id": "ec956c9d-4141-4c48-8df0-e04f4c20f188",
            "task_id": "70d24e9f-93d5-45ec-8fcf-6d9b4db99480",
            "skus": ["SKU-1"],
            "controller": "jv",
            "message": "Product variations accepted by OTTO. Use GET /extermal/job_status/{job_id} to track processing status.",
        }
    )

    app.dependency_overrides[get_external_api_service] = lambda: mock_service
    app.dependency_overrides[get_rabbitmq_publisher] = lambda: MagicMock()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/extermal/create_or_update_product",
                params={"controller": "jv"},
                json=[_sample_variation("SKU-1", "407148900001", "sp-1")],
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "accepted"
            assert data["job_id"] == "ec956c9d-4141-4c48-8df0-e04f4c20f188"
            assert data["marketplace_job_id"] == "ec956c9d-4141-4c48-8df0-e04f4c20f188"
            assert data["skus"] == ["SKU-1"]
            assert data["controller"] == "jv"
            assert "job_status" in data["message"]
    finally:
        app.dependency_overrides.pop(get_external_api_service, None)
        app.dependency_overrides.pop(get_rabbitmq_publisher, None)


@pytest.mark.asyncio
async def test_get_job_status_service():
    """Verify get_job_status queries OTTO and returns progress with failures."""
    mock_client = MagicMock()
    mock_client.read_update_task_result = AsyncMock(
        return_value={
            "state": "DONE",
            "total": 2,
            "progress": 2,
            "succeeded": 1,
            "failed": 1,
            "unchanged": 0,
        }
    )
    mock_client.get_update_task_failed = AsyncMock(
        return_value={"results": [{"sku": "SKU-FAIL", "errors": ["Invalid color"]}]}
    )
    mock_client.get_update_task_succeeded = AsyncMock(
        return_value={"results": [{"sku": "SKU-OK"}]}
    )

    service = ExternalService(client=mock_client)
    res = await service.get_job_status("ec956c9d-4141-4c48-8df0-e04f4c20f188", Controller.JV)

    assert res["job_id"] == "ec956c9d-4141-4c48-8df0-e04f4c20f188"
    assert res["state"] == "DONE"
    assert res["succeeded"] == 1
    assert res["failed"] == 1
    assert len(res["failures"]) == 1
    assert res["failures"][0]["sku"] == "SKU-FAIL"
    assert len(res["succeeded_items"]) == 1


@pytest.mark.asyncio
async def test_get_job_status_endpoint():
    """Verify endpoint GET /extermal/job_status/{job_id} works."""
    mock_service = MagicMock()
    mock_service.get_job_status = AsyncMock(
        return_value={
            "job_id": "ec956c9d-4141-4c48-8df0-e04f4c20f188",
            "controller": "jv",
            "state": "DONE",
            "total": 1,
            "progress": 1,
            "succeeded": 1,
            "failed": 0,
            "unchanged": 0,
            "failures": [],
            "succeeded_items": [],
        }
    )

    app.dependency_overrides[get_external_api_service] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/extermal/job_status/ec956c9d-4141-4c48-8df0-e04f4c20f188",
                params={"controller": "jv"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] == "ec956c9d-4141-4c48-8df0-e04f4c20f188"
            assert data["state"] == "DONE"
            assert data["succeeded"] == 1
            assert data["failed"] == 0
    finally:
        app.dependency_overrides.pop(get_external_api_service, None)





