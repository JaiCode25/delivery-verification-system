from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import FieldComparisonStatus, LabelVerificationStatus, Platform


class BoundingBoxPoint(BaseModel):
    x: int
    y: int


class BoundingBox(BaseModel):
    top_left: BoundingBoxPoint
    top_right: BoundingBoxPoint
    bottom_right: BoundingBoxPoint
    bottom_left: BoundingBoxPoint


class OcrDetection(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str
    confidence: float
    bounding_box: Optional[BoundingBox] = None
    source_frame: Optional[str] = None
    video_timestamp_seconds: Optional[float] = None
    sharpness: Optional[float] = None


class ParsedField(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    value: str = Field(..., description="Original raw extracted text from OCR")
    normalized_value: str = Field(..., description="Normalized value used for robust comparison")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score combining OCR quality and pattern strength")
    supporting_text: List[str] = Field(default_factory=list, description="Raw OCR fragments supporting this field")
    source_frame: Optional[str] = Field(None, description="Sharpest evidence frame where this field was observed")
    bounding_box: Optional[BoundingBox] = Field(None, description="Bounding coordinates of the primary detection")
    video_timestamp_seconds: Optional[float] = Field(None, description="Video timestamp in seconds, if available")
    frame_count: int = Field(1, ge=1, description="Number of distinct video frames supporting this value")


class ParsedShippingLabel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tracking_id: Optional[ParsedField] = None
    order_id: Optional[ParsedField] = None
    platform: Optional[ParsedField] = None
    courier_name: Optional[ParsedField] = None
    customer_name: Optional[ParsedField] = None
    pin_code: Optional[ParsedField] = None
    phone_number: Optional[ParsedField] = None
    seller_name: Optional[ParsedField] = None
    product_description: Optional[ParsedField] = None
    colour_model: Optional[ParsedField] = None
    quantity: Optional[ParsedField] = None
    raw_evidence: List[str] = Field(default_factory=list, description="List of raw OCR strings used during parsing")


class LabelSummary(BaseModel):
    """Concise 8-field summary of extracted shipping label information."""
    model_config = ConfigDict(from_attributes=True)

    platform: Optional[str] = None
    courier: Optional[str] = None
    order_id: Optional[str] = None
    tracking_number: Optional[str] = None
    pin_code: Optional[str] = None
    product: Optional[str] = None
    colour_model: Optional[str] = None
    quantity: Optional[int] = None


class FieldComparison(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    field_name: str = Field(..., description="Name of the compared field (e.g. tracking_id, order_id)")
    status: FieldComparisonStatus = Field(..., description="match | mismatch | not_detected | uncertain")
    label_value: Optional[str] = Field(None, description="Extracted label value (sanitized/masked)")
    order_value: Optional[str] = Field(None, description="Authoritative order value (sanitized/masked)")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    details: Optional[str] = Field(None, description="Reasoning or description of match/mismatch")


class LabelAnalysisResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: LabelVerificationStatus = Field(..., description="Overall label verification outcome")
    summary: LabelSummary = Field(default_factory=LabelSummary, description="Concise 8-field summary of extracted label information")
    parsed_fields: ParsedShippingLabel = Field(default_factory=ParsedShippingLabel)
    field_comparisons: List[FieldComparison] = Field(default_factory=list)
    supporting_frames: List[str] = Field(default_factory=list, description="Frames containing readable label evidence")
    warnings: List[str] = Field(default_factory=list, description="Non-fatal warnings encountered during OCR or parsing")


class LabelVerificationRequest(BaseModel):
    """Payload for isolated testing of label verification endpoint."""
    ocr_detections: List[OcrDetection] = Field(..., min_length=1, description="List of OCR detections")
    expected_platform: Optional[Platform] = None
    expected_order_id: Optional[str] = None
    expected_tracking_id: Optional[str] = None
    expected_customer_name: Optional[str] = None
    expected_product_name: Optional[str] = None
