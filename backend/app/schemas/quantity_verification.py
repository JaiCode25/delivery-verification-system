from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from app.schemas.evidence import ModelMetadata, ObjectDetection, SupportingFrame


class QuantityStatus(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNSUPPORTED_PRODUCT = "unsupported_product"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    MODEL_UNAVAILABLE = "model_unavailable"


class QuantityVerificationResult(BaseModel):
    quantity_analysis_id: str
    order_id: str
    verification_session_id: str
    expected_quantity: int = Field(gt=0)
    detected_quantity: int | None = Field(default=None, ge=0)
    detection_method: str = "conservative_simultaneous_frame_count_with_iou_tracks"
    target_class: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    status: QuantityStatus
    mismatch_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    supporting_frames: list[SupportingFrame] = Field(default_factory=list)
    detections: list[ObjectDetection] = Field(default_factory=list)
    model_metadata: ModelMetadata
    created_at: datetime
