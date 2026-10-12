import asyncio
from unittest.mock import AsyncMock

from app.api.routes.external_api import (
    activate_product_by_ean,
    deactivate_product_by_ean,
    get_categories as get_external_categories,
    get_category_attributes as get_external_category_attributes,
)
from app.api.routes.products import (
    _is_relevant_category_attribute,
    get_otto_v5_product_categories,
)
from app.main import app
from app.models.attributes import Attribute
from app.schemas.external_schemes.until_schemes import (
    ActiveStatusByEanRequest,
    CreateOrUpdateProductVariationRequest,
)
from app.schemas.enums import Controller
from app.services.extermal_service import (
    EXTERNAL_PRODUCT_BRAND_ID_BY_CONTROLLER,
    EXTERNAL_PRODUCT_COMPLIANCE_BY_CONTROLLER,
    ExternalService,
)


def test_direct_otto_categories_endpoint_is_exposed_in_openapi():
    operation = app.openapi()["paths"]["/v5/products/categories"]["get"]

    assert operation["summary"] == "Fetch category information directly from OTTO"
    assert {parameter["name"] for parameter in operation["parameters"]} >= {
        "page",
        "limit",
        "category",
        "controller",
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]


def test_direct_otto_categories_endpoint_forwards_controller():
    product_service = AsyncMock()
    product_service.get_categories.return_value = {"categoryGroups": []}

    response = asyncio.run(
        get_otto_v5_product_categories(
            page=0,
            limit=10,
            category=None,
            controller=Controller.XL,
            product_service=product_service,
        )
    )

    assert response == {"categoryGroups": []}
    product_service.get_categories.assert_awaited_once_with(
        {"page": 0, "limit": 10},
        controller=Controller.XL,
    )


def test_external_categories_endpoint_is_exposed_in_openapi():
    operation = app.openapi()["paths"]["/extermal/categories"]["get"]

    assert {parameter["name"] for parameter in operation["parameters"]} >= {
        "page",
        "limit",
        "category",
    }
    assert "controller" not in {
        parameter["name"] for parameter in operation["parameters"]
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]


def test_external_categories_endpoint_forwards_payload():
    service = AsyncMock()
    service.get_categories.return_value = {"categories": []}
    db = object()

    response = asyncio.run(
        get_external_categories(
            page=0,
            limit=10,
            category=None,
            service=service,
            db=db,
        )
    )

    assert response == {"categories": []}
    service.get_categories.assert_awaited_once_with(
        {"page": 0, "limit": 10},
        "jv",
        db,
    )


def test_external_attributes_endpoint_uses_category_id():
    operation = app.openapi()["paths"]["/extermal/attributes"]["get"]
    parameter_names = {parameter["name"] for parameter in operation["parameters"]}

    assert parameter_names == {"categoryId"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]


def test_external_attributes_endpoint_forwards_category_id():
    service = AsyncMock()
    service.get_category_attributes.return_value = {"attributes": []}
    db = object()

    response = asyncio.run(
        get_external_category_attributes(
            category_id=123,
            service=service,
            db=db,
        )
    )

    assert response == {"attributes": []}
    service.get_category_attributes.assert_awaited_once_with(123, "jv", db)


def test_external_active_status_endpoints_are_exposed_in_openapi():
    paths = app.openapi()["paths"]

    assert "/extermal/activate" in paths
    assert "/extermal/deactivate" in paths
    assert paths["/extermal/activate"]["post"]["requestBody"]
    assert paths["/extermal/deactivate"]["post"]["requestBody"]


def test_external_activate_endpoint_forwards_ean_payload():
    service = AsyncMock()
    service.set_active_status_by_ean.return_value = {
        "success": True,
        "ean": "4250123456789",
        "sku": "4250123456789",
        "active": True,
        "controller": Controller.JV,
        "status_code": 202,
        "message": "Active status update was accepted by OTTO",
    }
    payload = ActiveStatusByEanRequest(ean="4250123456789")

    response = asyncio.run(
        activate_product_by_ean(
            payload=payload,
            service=service,
        )
    )

    assert response["success"] is True
    service.set_active_status_by_ean.assert_awaited_once_with(payload, active=True)


def test_external_deactivate_endpoint_forwards_ean_payload():
    service = AsyncMock()
    service.set_active_status_by_ean.return_value = {
        "success": True,
        "ean": "4250123456789",
        "sku": "4250123456789",
        "active": False,
        "controller": Controller.JV,
        "status_code": 202,
        "message": "Active status update was accepted by OTTO",
    }
    payload = ActiveStatusByEanRequest(ean="4250123456789")

    response = asyncio.run(
        deactivate_product_by_ean(
            payload=payload,
            service=service,
        )
    )

    assert response["active"] is False
    service.set_active_status_by_ean.assert_awaited_once_with(payload, active=False)


def test_external_create_adds_backend_owned_account_fields():
    client = AsyncMock()
    client.create_or_update_product.return_value = {
        "links": [{"href": "/v5/products/update-tasks/ec956c9d-4141-4c48-8df0-e04f4c20f188"}]
    }
    service = ExternalService(client=client)
    payload = CreateOrUpdateProductVariationRequest.model_validate(
        [
            {
                "productReference": "4250123456789",
                "sku": "4250123456789",
                "ean": "4250123456789",
                "shippingProfileId": "c5f87b2e-0a56-429f-a63e-324c0d1645e7",
                "productDescription": {
                    "brandId": "WRONG_BRAND",
                    "category": "Sessel",
                    "productLine": "Kotak plus",
                    "bulletPoints": [],
                    "attributes": [],
                },
                "mediaAssets": [
                    {
                        "type": "IMAGE",
                        "location": "https://example.com/image.jpg",
                    }
                ],
                "delivery": {
                    "type": "PARCEL",
                    "deliveryTime": 3,
                },
                "pricing": {
                    "standardPrice": {"amount": 149.99, "currency": "EUR"},
                    "msrp": {"amount": 1, "currency": "EUR"},
                    "vat": "FULL",
                },
            }
        ]
    )

    response = asyncio.run(
        service.create_or_update_product(payload=payload, controller="xl")
    )

    assert response["status"] == "accepted"
    assert response["marketplace_job_id"] == "ec956c9d-4141-4c48-8df0-e04f4c20f188"
    assert response["skus"] == ["4250123456789"]
    prepared_payload = client.create_or_update_product.await_args.args[0]
    assert (
        prepared_payload[0]["productDescription"]["brandId"]
        == EXTERNAL_PRODUCT_BRAND_ID_BY_CONTROLLER[Controller.XL]
    )
    assert (
        prepared_payload[0]["compliance"]
        == EXTERNAL_PRODUCT_COMPLIANCE_BY_CONTROLLER[Controller.XL]
    )
    assert prepared_payload[0]["pricing"]["msrp"] == {
        "amount": 202.49,
        "currency": "EUR",
    }
    assert "brand" not in prepared_payload[0]["productDescription"]
    client.create_or_update_product.assert_awaited_once()


def test_external_create_accepts_standard_otto_description_fields():
    payload = CreateOrUpdateProductVariationRequest.model_validate(
        [
            {
                "productReference": "4250123456789",
                "sku": "4250123456789",
                "ean": "4250123456789",
                "shippingProfileId": "c5f87b2e-0a56-429f-a63e-324c0d1645e7",
                "productDescription": {
                    "brandId": "WRONG_BRAND",
                    "bundle": False,
                    "category": "Sessel",
                    "disposal": False,
                    "multiPack": False,
                    "fscCertified": False,
                    "productLine": "Kotak plus",
                    "productUrl": "https://example.com/product",
                    "bulletPoints": [],
                    "attributes": [],
                    "description": "Text",
                },
                "pricing": {
                    "standardPrice": {"amount": 149.99, "currency": "EUR"},
                    "vat": "FULL",
                },
                "mediaAssets": [
                    {
                        "type": "IMAGE",
                        "location": "https://example.com/image.jpg",
                    }
                ],
                "delivery": {
                    "type": "PARCEL",
                    "deliveryTime": 3,
                },
                "compliance": {"productSafety": {"addresses": []}},
            }
        ]
    )

    product = payload.model_dump(mode="json", by_alias=True)[0]

    assert product["productDescription"]["multiPack"] is False
    assert product["productDescription"]["fscCertified"] is False
    assert product["pricing"]["standardPrice"]["amount"] == 149.99


def test_only_important_legal_or_variation_attributes_are_relevant_by_default():
    low = Attribute(name="Optional", type="STRING", relevance="LOW")
    high = Attribute(name="Important", type="STRING", relevance="HIGH")
    medium = Attribute(name="Useful", type="STRING", relevance="MEDIUM")
    legal = Attribute(
        name="Mandatory",
        type="STRING",
        relevance="LOW",
        feature_relevance=["LEGAL"],
    )
    variation = Attribute(
        name="Color",
        type="STRING",
        relevance="LOW",
        feature_relevance=["VARIATION_THEME"],
    )
    component_color = Attribute(
        name="Farbe Rückenlehne",
        type="STRING",
        relevance="LOW",
    )

    assert not _is_relevant_category_attribute(low)
    assert _is_relevant_category_attribute(high)
    assert _is_relevant_category_attribute(medium)
    assert _is_relevant_category_attribute(legal)
    assert _is_relevant_category_attribute(variation)
    assert _is_relevant_category_attribute(component_color)
