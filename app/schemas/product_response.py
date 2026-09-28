from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.base_data import Link


class ProductCreateResponse(BaseModel):
    state: str
    total: int
    message: str
    links: list[Link]

    model_config = ConfigDict(extra="ignore")


class UpdateQuantityResponse(BaseModel):
    status_code: int
    message: str


class UpdateProductDeliveryResponse(BaseModel):
    message: str = "OK"


class OperationResult(BaseModel):
    success: bool = False
    errors: Optional[str] = None


class AvailabilityResponse(BaseModel):
    update_quantity: Optional[OperationResult]
    update_delivery: Optional[OperationResult]


class DeleteProductResponse(BaseModel):
    product_operation: Optional[OperationResult] = None
    quantity_operation: Optional[OperationResult] = None


class UrlProcessResult(BaseModel):
    url: str
    success: bool
    sku: str | None = None
    message: str


class DeleteProductFrmFileResponse(BaseModel):
    update_product: OperationResult
    update_quantity: OperationResult


class Relevance(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AttributeSchema(BaseModel):
    attributeId: Optional[int] = None
    attributeKey: Optional[str] = None
    name: str
    type: str
    attributeGroup: Optional[str] = None
    description: Optional[str] = None
    relevance: Optional[Relevance] = None
    featureRelevance: list[str] = []
    multiValue: bool = False
    unit: str = ""
    unitDisplayName: Optional[str] = None
    allowedValues: list[Any] = []

    @property
    def id(self) -> Optional[int]:
        return self.attributeId


class CategoryGroupSchema(BaseModel):
    categoryGroup: str
    categories: list[str]
    variationThemes: list[str]
    attributes: list[AttributeSchema]


class OttoCategoryResponse(BaseModel):
    categoryGroups: list[CategoryGroupSchema]

    model_config = ConfigDict(extra="ignore")


class ExternalCategoryItem(BaseModel):
    category_group: str | None = None
    category_group_id: int | None = None
    categoryId: Optional[int] = None
    name: str

    @property
    def id(self) -> Optional[int]:
        return self.categoryId


class ExternalCategoriesResponse(BaseModel):
    categories: list[ExternalCategoryItem]


class ExternalCategoryAttributesResponse(BaseModel):
    attributes: list[AttributeSchema]
