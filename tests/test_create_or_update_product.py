import pytest
from uuid import UUID
from unittest.mock import AsyncMock, MagicMock
from pydantic import ValidationError
from app.services.extermal_service import ExternalService
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


