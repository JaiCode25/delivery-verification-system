"""Pydantic schemas for customer-facing product intake."""
from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContradictionItem(BaseModel):
    field: str
    customer_value: Any
    verified_order_value: Any
    reason: str


class ReferenceResolutionResult(BaseModel):
    reference_id: Optional[str] = None
    reference_url: Optional[str] = None
    reference_source: str = "reference_unavailable"  # "demo_catalogue" or "reference_unavailable"
    match_score: float = Field(0.0, ge=0.0, le=1.0)
    match_reason: str
    matched_product_name: Optional[str] = None
    matched_brand: Optional[str] = None
    matched_model: Optional[str] = None


class CustomerSubmissionCreate(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=255, description="Name of the product")
    platform: str = Field(..., min_length=1, max_length=50, description="Platform or store name (e.g. Amazon, Flipkart, Blinkit)")
    product_url: str = Field(..., min_length=8, description="HTTPS URL of the product page")
    expected_quantity: int = Field(default=1, ge=1, le=100, description="Expected quantity (1 to 100)")
    colour_or_variant: Optional[str] = Field(None, max_length=100, description="Optional color, variant, or model info")
    customer_name: Optional[str] = Field(None, max_length=200, description="Customer name")
    customer_email: Optional[str] = Field(None, max_length=200, description="Customer email address")
    notes: Optional[str] = Field(None, max_length=2000, description="Additional customer notes")

    @field_validator("product_url")
    @classmethod
    def validate_product_url(cls, v: str) -> str:
        s = v.strip()
        if not s.lower().startswith("https://"):
            raise ValueError("product_url must be a valid HTTPS URL (starting with https://)")
        if len(s) < 10 or "." not in s.split("://", 1)[1]:
            raise ValueError("product_url must contain a valid domain name")
        return s

    @field_validator("platform")
    @classmethod
    def normalize_platform(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("customer_email")
    @classmethod
    def validate_customer_email(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if v and ("@" not in v or "." not in v.split("@", 1)[1]):
                raise ValueError("customer_email must be a valid email address")
        return v or None


class CustomerSubmissionUpdate(BaseModel):
    product_name: Optional[str] = Field(None, min_length=1, max_length=255)
    platform: Optional[str] = Field(None, min_length=1, max_length=50)
    product_url: Optional[str] = Field(None, min_length=8)
    expected_quantity: Optional[int] = Field(None, ge=1, le=100)
    colour_or_variant: Optional[str] = Field(None, max_length=100)
    customer_name: Optional[str] = Field(None, max_length=200)
    customer_email: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = Field(None, max_length=2000)

    @field_validator("product_url")
    @classmethod
    def validate_product_url(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not s.lower().startswith("https://"):
                raise ValueError("product_url must be a valid HTTPS URL (starting with https://)")
            if len(s) < 10 or "." not in s.split("://", 1)[1]:
                raise ValueError("product_url must contain a valid domain name")
            return s
        return v

    @field_validator("platform")
    @classmethod
    def normalize_platform(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return v.strip().lower()
        return v

    @field_validator("customer_email")
    @classmethod
    def validate_customer_email(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if v and ("@" not in v or "." not in v.split("@", 1)[1]):
                raise ValueError("customer_email must be a valid email address")
        return v or None


class LinkSessionRequest(BaseModel):
    verification_session_id: str = Field(..., min_length=1, max_length=100)


class CustomerSubmissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    submission_id: str
    id: str
    product_name: str
    platform: str
    product_url: str
    normalized_product_url: str
    expected_quantity: int
    colour_or_variant: Optional[str] = None
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    notes: Optional[str] = None
    verification_session_id: Optional[str] = None
    order_id: Optional[int] = None
    resolved_reference: Optional[ReferenceResolutionResult] = None
    contradictions: List[ContradictionItem] = []
    created_at: datetime
    updated_at: datetime
