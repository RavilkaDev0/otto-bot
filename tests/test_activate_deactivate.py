import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.extermal_service import ExternalService
from app.schemas.external_schemes.until_schemes import (
    ActiveStatusByEanRequest,
    Controller,
)


def test_sku_for_ean_matching():
    data = {
        "productVariations": [
            {"sku": "SKU_001", "ean": "4000000000001"},
            {"sku": "SKU_002", "ean": "4000000000002"},
        ]
    }
    assert ExternalService._sku_for_ean(data, "4000000000002") == "SKU_002"
    assert ExternalService._sku_for_ean(data, "4000000000001") == "SKU_001"


def test_sku_for_ean_not_found_returns_none():
    """Verify that when EAN does not match, it returns None and DOES NOT fall back to variations[0]"""
    data = {
        "productVariations": [
            {"sku": "RANDOM_FIRST_SKU", "ean": "4000000000001"},
        ]
    }
    # Before the fix, searching for 9999999999999 would return 'RANDOM_FIRST_SKU'
    assert ExternalService._sku_for_ean(data, "9999999999999") is None


@pytest.mark.asyncio
async def test_set_active_status_product_not_found():
    mock_client = MagicMock()
    mock_client.get_products = AsyncMock(return_value={"productVariations": []})
    mock_client.update_active_status = AsyncMock()

    service = ExternalService(client=mock_client)
    payload = ActiveStatusByEanRequest(ean="1234567890123", controller=Controller.JV)

    response = await service.set_active_status_by_ean(payload, active=False, session=None)
    assert response.success is False
    assert response.status_code == 404
    assert "not found" in response.message.lower()
    mock_client.update_active_status.assert_not_called()


@pytest.mark.asyncio
async def test_deactivate_calls_quantity_zero_first():
    mock_client = MagicMock()
    mock_client.get_products = AsyncMock(return_value={
        "productVariations": [{"sku": "SKU_123", "ean": "1234567890123"}]
    })
    mock_client.update_quantity = AsyncMock(return_value={"status": "ok"})
    mock_client.update_active_status = AsyncMock(return_value=(202, {"status": "ACCEPTED"}))

    service = ExternalService(client=mock_client)
    payload = ActiveStatusByEanRequest(ean="1234567890123", controller=Controller.JV)

    response = await service.set_active_status_by_ean(payload, active=False, session=None)
    assert response.success is True
    assert response.status_code == 202
    assert response.sku == "SKU_123"
    assert response.active is False

    # Check that update_quantity was called with quantity=0
    mock_client.update_quantity.assert_called_once()
    qty_call_args = mock_client.update_quantity.call_args[0][0]
    assert qty_call_args.root[0].sku == "SKU_123"
    assert qty_call_args.root[0].quantity == 0

    # Check that update_active_status was called with active=False
    mock_client.update_active_status.assert_called_once_with(
        {"status": [{"sku": "SKU_123", "active": False}]}, "jv"
    )


@pytest.mark.asyncio
async def test_activate_does_not_zero_quantity():
    mock_client = MagicMock()
    mock_client.get_products = AsyncMock(return_value={
        "productVariations": [{"sku": "SKU_123", "ean": "1234567890123"}]
    })
    mock_client.update_quantity = AsyncMock()
    mock_client.update_active_status = AsyncMock(return_value=(202, {"status": "ACCEPTED"}))

    service = ExternalService(client=mock_client)
    payload = ActiveStatusByEanRequest(ean="1234567890123", controller=Controller.JV)

    response = await service.set_active_status_by_ean(payload, active=True, session=None)
    assert response.success is True
    assert response.status_code == 202
    assert response.sku == "SKU_123"
    assert response.active is True

    # Check update_quantity was NOT called on activate
    mock_client.update_quantity.assert_not_called()
    mock_client.update_active_status.assert_called_once_with(
        {"status": [{"sku": "SKU_123", "active": True}]}, "jv"
    )
