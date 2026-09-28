from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, RootModel, field_validator, model_validator

from app.schemas.enums import Controller


class GetProductRequest(BaseModel):
    sku: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        title="SKU продукта(уникальное)",
        description="Уникальный идентификатор продукта. Если указан, возвращается 1 продукт",
        examples=["4003002005002"],
    )
    productReference: str | None = Field(
        default=None,
        title="Product Reference продуктов",
        description="Идентификатор для вариации продукта. Если указан, возвращает продукт с их вариациаими(sku1, sku2...)",
        examples=["4069943386158_SOFORT_JV"],
    )
    ean: str | None = Field(
        default=None,
        min_length=8,
        max_length=13,
        title="EAN продукта",
        description="Возвращает продукты по EAN",
        examples=["4250123456789"],
    )
    category: str | None = Field(
        default=None,
        title="Категория продукта",
        description="Возвращает продукты с этой категорией",
        examples=["Polsterbett"],
    )

    """pagination"""
    page: int = Field(ge=0, description="Число продуктов за один запрос", default=0)
    limit: int = Field(ge=0, description="Пагинация", default=10)
    # controller: Controller = Field(default=Controller.JV, description="Аккаунт для операции")

    model_config = ConfigDict(extra="ignore")


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


class OttoBaseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="ignore"
    )


# ─────────────────────────────────────────────
# Product description
# ─────────────────────────────────────────────


class OttoAttributeRequest(OttoBaseModel):
    name: str = Field(
        min_length=0, description="Otto attribute name", examples=["Farbe"]
    )
    values: list[str] = Field(
        min_length=1, description="Attribute values", examples=[["Schwartz"]]
    )
    additional: bool | None = Field(
        default=None, description="Whether this is an additional attribute"
    )


class ProductDescriptionRequest(OttoBaseModel):
    category: str = Field(
        min_length=1, description="OTTO category name", examples=["Sessel"]
    )
    brand_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("brandId", "brand_id", "brand"),
        serialization_alias="brandId",
        min_length=1,
        description="Brand registered in OTTO(account based)",
        examples=["UO4EGHSX"],
    )
    product_line: str | None = Field(
        default=None,
        max_length=100,
        description="Product line or product name",
        examples=["Kotak plus"],
    )
    manufacturer: str | None = Field(default=None, description="Product manufacturer")
    production_date: datetime | None = None
    bundle: bool | None = None
    multi_pack: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("multiPack", "multi_pack", "multipack"),
        serialization_alias="multiPack",
    )
    fsc_certified: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "fscCertified",
            "fsc_certified",
            "fscCertificate",
            "fsc_certificate",
        ),
        serialization_alias="fscCertified",
    )
    disposal: bool | None = None
    product_url: HttpUrl | None = Field(default=None, alias="productUrl")
    description: str | None = Field(
        default=None, description="Product description in German"
    )
    bullet_points: list[str] = Field(
        max_length=5,
        default_factory=list,
        description="Product bullet points in German",
    )
    attributes: list[OttoAttributeRequest] = Field(default_factory=list)


# ─────────────────────────────────────────────
# Media
# ─────────────────────────────────────────────
MediaAssetType = Literal[
    "IMAGE",
    "DIMENIONAL_DRAWING",
    "COLOR_VARIANT",
    "ENERGY_EFFICIENCY_LABEL",
    "MATERIAL_SAMPLE",
    "PRODUCT_DATASHEET",
    "USER_MANUAL",
    "MANUFACTURER_WARRANTY",
    "SAFETY_DATASHEET",
    "ASSEMBLY_INSTRUCTIONS",
    "WARNING_LABEL",
]


class MediaAssetRequest(OttoBaseModel):
    type: MediaAssetType
    location: HttpUrl | None = Field(
        default=None,
        description="Publicly accessible media URL",
    )
    filename: str | None = None

    @model_validator(mode="after")
    def require_location_for_images(self):
        if self.type == "IMAGE" and self.location is None:
            raise ValueError("location is required for IMAGE media assets")
        return self


# ─────────────────────────────────────────────
# Delivery
# ─────────────────────────────────────────────
DeliveryType = Literal[
    "PARCEL",
    "FORWARDER_PREFERREDLOCATION",
    "FORWARDER_CURBSIDE",
    "FORWARDER_HEAVYDUTY",
    "FORWARDED_PREFERREDLOCATION",
    "FORWARDED_CURBSIDE",
]


class DeliveryRequest(OttoBaseModel):
    type: DeliveryType = Field(
        validation_alias=AliasChoices("type", "deliveryType"),
        serialization_alias="type",
    )
    delivery_time: int = Field(ge=1, description="Delivery time in days")


# ─────────────────────────────────────────────
# Pricing
# ─────────────────────────────────────────────
class MonetaryAmountRequest(OttoBaseModel):
    amount: float = Field(ge=0, examples=[float("149.99")])
    currency: Literal["EUR"] = "EUR"


class SaleRequest(OttoBaseModel):
    sale_price: MonetaryAmountRequest
    start_date: datetime | None = None
    end_date: datetime | None = None


class PricingRequest(OttoBaseModel):
    standard_price: MonetaryAmountRequest
    vat: Literal["FULL", "REDUCED", "FREE"]

    msrp: MonetaryAmountRequest | None = None
    sale: SaleRequest | None = None


# ─────────────────────────────────────────────
# Logistics
# ─────────────────────────────────────────────


class PackingUnitRequest(OttoBaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="allow"
    )


class LogisticsRequest(OttoBaseModel):
    packing_unit_count: int | None = Field(default=None, alias="packingUnitCount")
    packing_units: list[PackingUnitRequest] = Field(default_factory=list, alias="packingUnits")


class MaxOrderQuantity(OttoBaseModel):
    quantity: int | None = None
    period_in_days: int | None = Field(default=None, alias="periodInDays")


class Order(OttoBaseModel):
    max_order_quantity: MaxOrderQuantity | None = Field(default=None, alias="maxOrderQuantity")


# ─────────────────────────────────────────────
# Compliance
# ─────────────────────────────────────────────


class ProductComplianceRequest(OttoBaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="allow"
    )
    product_safety: dict[str, Any] | None = None


class ProductVariationRequest(OttoBaseModel):
    product_reference: str = Field(
        min_length=1,
        max_length=50,
        description=(
            "Groups multiple variations into one OTTO product. "
            "Must  be identical for all variations."
        ),
        examples=["Usually SKU of the main product"],
    )
    sku: str = Field(min_length=1, max_length=50, examples=["4021234231234"])
    ean: str = Field(min_length=8, max_length=13, examples=["4250123456789"])
    shipping_profile_id: str = Field(
        validation_alias=AliasChoices(
            "shippingProfileId",
            "shippingProfileID",
            "shipping_profile_id",
        ),
        serialization_alias="shippingProfileId",
    )
    quantity: int = Field(
        default=20,
        ge=0,
        le=100000,
        validation_alias=AliasChoices("quantity", "stock", "Stock"),
        serialization_alias="quantity",
        description="Stock quantity of product variation",
    )

    @field_validator("quantity", mode="before")
    @classmethod
    def default_quantity_if_none(cls, v: Any) -> int:
        if v is None:
            return 20
        return v

    isbn: str | None = None
    upc: str | None = None
    pzn: str | None = None
    mpn: str | None = None
    moin: str | None = None
    offering_start_date: datetime | None = None
    release_date: datetime | None = None
    order: Order | None = None
    product_description: ProductDescriptionRequest
    media_assets: list[MediaAssetRequest] = Field(min_length=1)
    delivery: DeliveryRequest
    pricing: PricingRequest | None = None
    logistics: LogisticsRequest | None = None

    compliance: ProductComplianceRequest | None = None


class CreateOrUpdateProductVariationRequest(RootModel[list[ProductVariationRequest]]):
    root: list[ProductVariationRequest] = Field(min_length=1, max_length=500)


class ActiveStatusByEanRequest(BaseModel):
    ean: str = Field(
        min_length=8,
        max_length=13,
        description="EAN товара, по которому нужно изменить active-status",
        examples=["4250123456789"],
    )
    controller: Controller = Field(
        default=Controller.JV,
        description="OTTO account/controller для операции",
    )


class ActiveStatusByEanResponse(BaseModel):
    success: bool
    ean: str
    sku: str | None = None
    active: bool
    controller: Controller
    status_code: int | None = None
    message: str
    response: Any | None = None


class Quantity(OttoBaseModel):
    quantity: int = Field(ge=0, le=100000, description="Quantity of a product", default=20)
    sku: str = Field(min_length=1, max_length=50, examples=["4021234231234"])


class QuantityRequest(RootModel[list[Quantity]]):
    root: list[Quantity] = Field(min_length=1, max_length=500)


class DeliveryInformation(OttoBaseModel):
    shipping_profile_id: str = Field(
        description="ID of shipping profile",
    )
    processing_time: str = Field(
        description="The processing time of specific SKU which can be any value between 1 and 99",
        default="DEFAULT",
    )
    sku: str = Field(min_length=1, max_length=50, examples=["4021234231234"])


class DeliveryInformationRequest(RootModel[list[DeliveryInformation]]):
    root: list[DeliveryInformation] = Field(min_length=1, max_length=500)


class AvailabilityRequest(OttoBaseModel):
    quantities: QuantityRequest
    delivery_information: DeliveryInformationRequest


WorkingDay = Literal[
    "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SUNDAY", "SATURDAY"
]


class ShippingProfile(OttoBaseModel):
    shipping_profile_id: str = Field(description="ID of shipping profile")
    shipping_profile_name: str = Field(description="Name of shipping profile")
    working_days: list[WorkingDay] = Field(
        description="Days on which orders are processed"
    )
    order_cutoff: str = Field(
        description=(
            "OrderCutoff specifies the time for orders to be placed, so the ProcessingTime starts within the same day. "
            "This must be in half hour duration in (HH:MM) 24 hours format and in CET time."
        ),
        examples=["23:30"],
    )
    delivery_type: DeliveryType
    default_processing_time: int = Field(
        ge=1, le=99, description="Default processing time in working days"
    )
    transport_time: int = Field(
        ge=1,
        le=99,
        description="The time the carrier needs from collecting the order from partners warehouse till the first delivery attempt.",
    )


class ShippingProfileResponse(RootModel[list[ShippingProfile]]):
    root: list[ShippingProfile] = Field(
        max_length=300, description="Full list of shipping profiles"
    )


class CreateDeliveryJob(OttoBaseModel):
    task_id: UUID = Field(default_factory=uuid4)
    marketplace_job_id: UUID = Field(description="OTTO job id. Used for status retrival")
    availability_data: AvailabilityRequest
    controller: Controller


class UpdateTaskResponse(OttoBaseModel):
    state: str
    message: str | None = None
    total: int = 0
    progress: int = 0
    succeeded: int = 0
    failed: int = 0
    unchanged: int = 0

    ping_after: datetime | None = Field(
        default=None,
        alias="pingAfter",
    )

    model_config = ConfigDict(extra="ignore")
