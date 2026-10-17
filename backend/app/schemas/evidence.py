"""Shared, path-free evidence metadata; scores are not calibrated probabilities."""
from pydantic import BaseModel, Field


class ModelMetadata(BaseModel):
    repository: str
    package_version: str
    model: str
    checkpoint: str
    license: str
    available: bool = False
    device: str = "cpu"


class SupportingFrame(BaseModel):
    frame_filename: str
    frame_index: int
    timestamp_seconds: float
    sharpness: float
    quality_adequate: bool


class ObjectDetection(BaseModel):
    frame_filename: str
    timestamp_seconds: float
    box: list[float] = Field(min_length=4, max_length=4)
    class_name: str
    confidence: float = Field(ge=0, le=1)
    track_id: int | None = None
    image_width: int | None = None
    image_height: int | None = None
