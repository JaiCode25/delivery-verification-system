from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import ProductMatchDecision


class ProductMatchingComponentScores(BaseModel):
    ocr_text_score: float = Field(0.0, ge=0.0, le=1.0)
    perceptual_hash_score: float = Field(0.0, ge=0.0, le=1.0)
    colour_similarity_score: float = Field(0.0, ge=0.0, le=1.0)
    feature_match_score: float = Field(0.0, ge=0.0, le=1.0)
    semantic_score: Optional[float] = Field(None, ge=0.0, le=1.0)


class ProductCandidateFrame(BaseModel):
    frame_index: int
    timestamp_seconds: float
    sharpness: float
    frame_filename: str
    best_reference_id: Optional[str] = None
    scores: ProductMatchingComponentScores
    overall_score: float = Field(0.0, ge=0.0, le=1.0)


class ProductMatchingResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    matching_id: Optional[str] = None
    order_id: str
    expected_product_name: str
    expected_brand: str
    expected_model: str
    expected_colour: str
    expected_quantity: int
    detected_product_text: List[str] = []
    best_reference_id: Optional[str] = None
    best_candidate_frame: Optional[str] = None
    best_frame_timestamp_seconds: Optional[float] = None
    ocr_text_score: float = Field(0.0, ge=0.0, le=1.0)
    perceptual_hash_score: float = Field(0.0, ge=0.0, le=1.0)
    colour_similarity_score: float = Field(0.0, ge=0.0, le=1.0)
    feature_match_score: float = Field(0.0, ge=0.0, le=1.0)
    semantic_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    overall_match_score: float = Field(0.0, ge=0.0, le=1.0)
    decision: ProductMatchDecision
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    warnings: List[str] = []
    supporting_frames: List[ProductCandidateFrame] = []
    created_at: Optional[datetime] = None
