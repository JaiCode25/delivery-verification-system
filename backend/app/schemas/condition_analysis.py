from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from app.schemas.evidence import ModelMetadata, SupportingFrame


class EvidenceType(str, Enum):
    SEALED_PACKAGE = "sealed_package"
    OPENED_PACKAGE = "opened_package"
    PRODUCT = "product"
    UNKNOWN = "unknown"


class ConditionStatus(str, Enum):
    DAMAGED = "damaged"
    LIKELY_DAMAGED = "likely_damaged"
    NO_VISIBLE_DAMAGE = "no_visible_damage"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    MODEL_UNAVAILABLE = "model_unavailable"


class ZeroShotScores(BaseModel):
    frame_filename: str
    damage: float = Field(ge=0, le=1)
    intact: float = Field(ge=0, le=1)
    margin: float = Field(ge=0, le=1)


class VisualCue(BaseModel):
    frame_filename: str
    reference_id: str | None = None
    reference_aligned: bool = False
    edge_change_fraction: float | None = None
    supports_surface_change: bool = False
    description: str = "No comparable reference; structural condition is unconfirmed."


class ConditionAnalysisResult(BaseModel):
    condition_analysis_id: str
    order_id: str
    verification_session_id: str
    evidence_type: EvidenceType
    status: ConditionStatus
    damage_detected: bool | None = None
    damage_categories: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    zero_shot_scores: list[ZeroShotScores] = Field(default_factory=list)
    visual_cues: list[VisualCue] = Field(default_factory=list)
    mismatch_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    supporting_frames: list[SupportingFrame] = Field(default_factory=list)
    model_metadata: ModelMetadata
    created_at: datetime
