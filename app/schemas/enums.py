"""Shared enum types used across schema, model, and route layers."""

from enum import Enum


class SortOrderEnum(str, Enum):
    """Ordering direction used by list and status query endpoints."""

    ASC = "ASC"
    DESC = "DESC"


class VatEnum(str, Enum):
    """Supported VAT categories accepted by OTTO/local product models."""

    FULL = "FULL"
    REDUCED = "REDUCED"
    FREE = "FREE"
    NONE = "NONE"


class RoleEnum(str, Enum):
    SEO = "SEO"
    EMPLOYEE = "EMPLOYEE"


class Controller(str, Enum):
    JV = "jv"
    XL = "xl"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            val_lower = value.lower()
            for member in cls:
                if member.value == val_lower or member.name.lower() == val_lower:
                    return member
        return None


class ShippingProfileEnum(str, Enum):
    FOUR_TO_EIGHT_WEEKS = "786c6468-3baf-52e0-88b5-13757eb7f873"
    SIX_TO_TEN_WEEKS = "360835cf-4962-59bb-ae66-78e8a41c8948"
    TWO_TO_FOUR_WEEKS = "28e3b4f8-12aa-5994-a7e9-26027baede55"
    EXPRESS_CHESTERFIELD = "ad6009b9-a82f-5284-ac64-5627575655ac"
    EXPRESS_PRODUCTION = "571dd076-4e59-5216-a86f-3e5f30319e9c"
    DELIVERY_TIME_EIGHT_TO_TWELVE_WEEKS = "935a75b0-ac88-55a8-98df-8556306f1386"
    AVAILABLE_IMMEDIATELY = "b4139e65-603f-52f7-9b99-393cf6b2461f"
    PREFERRED_LOCATION_TWO_MAN_SERVICE_IMMEDIATE = (
        "83feaefc-c110-5b39-af53-49344b77ae89"
    )
