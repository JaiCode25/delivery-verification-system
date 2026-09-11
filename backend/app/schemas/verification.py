from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import Platform, VerificationStatus
from app.schemas.order import OrderSummary


class BarcodeVerificationRequest(BaseModel):
    barcode_value: Optional[str] = Field(None, description="Decoded tracking or barcode identifier")
    barcode_type: Optional[str] = Field(None, description="Symbology type (e.g. Code 128, QR_CODE)")
    expected_platform: Optional[Platform] = Field(None, description="Platform claimed by user (e.g. amazon, flipkart)")
    expected_order_id: Optional[str] = Field(None, description="Order ID claimed by user")
    expected_customer_name: Optional[str] = Field(None, description="Customer name claimed by user")
    expected_product_name: Optional[str] = Field(None, description="Product name expected by user")
    evidence_frame: Optional[str] = Field(None, description="Filename of frame containing barcode")
    video_timestamp_seconds: Optional[float] = Field(None, description="Timestamp in video when code was seen")


class VerificationSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: Optional[int] = None
    decoded_code: Optional[str] = None
    barcode_type: Optional[str] = None
    verification_status: VerificationStatus
    mismatch_reasons: List[str] = []
    evidence_frame: Optional[str] = None
    video_timestamp_seconds: Optional[float] = None
    is_claim_finalized: bool = False
    claim_finalized_at: Optional[datetime] = None
    created_at: datetime


class OrderVerificationResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    verification_status: VerificationStatus
    matched_order: Optional[OrderSummary] = None
    mismatch_reasons: List[str] = Field(default_factory=list)
    decoded_value: Optional[str] = None
    barcode_type: Optional[str] = None
    evidence_filename: Optional[str] = None
    video_timestamp_seconds: Optional[float] = None
    verification_session_id: Optional[str] = None
    created_at: datetime
