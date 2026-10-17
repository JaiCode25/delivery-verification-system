from pathlib import Path
from typing import Dict, List, Literal, Set

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = f"sqlite:///{(BASE_DIR / 'storage' / 'delivery_verification.db').as_posix()}"
    upload_dir: Path = BASE_DIR / "storage" / "uploads"
    frame_dir: Path = BASE_DIR / "storage" / "frames"
    result_dir: Path = BASE_DIR / "storage" / "results"
    report_dir: Path = BASE_DIR / "storage" / "reports"
    reference_dir: Path = BASE_DIR / "storage" / "references"
    max_file_size: int = 200 * 1024 * 1024  # 200 MB
    max_reference_file_size: int = 10 * 1024 * 1024  # 10 MB per reference image
    max_reference_images_per_item: int = 5
    max_ocr_frames: int = 10  # Maximum video frames to process with OCR
    allowed_image_extensions: Set[str] = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    allowed_reference_extensions: Set[str] = {".jpg", ".jpeg", ".png", ".webp"}
    allowed_video_extensions: Set[str] = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
    cors_origins: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "*",
    ]

    # Product matching weights & thresholds
    weight_ocr_text: float = 0.35
    weight_orb_features: float = 0.30
    weight_perceptual_hash: float = 0.20
    weight_colour_similarity: float = 0.15
    threshold_match: float = 0.75
    threshold_likely_match: float = 0.55
    enable_openclip: bool = False

    # Optional evidence models: offline, lazy and CPU-friendly by default.
    enable_quantity_model: bool = False
    enable_condition_model: bool = False
    model_cache_dir: Path = BASE_DIR / "storage" / "models"
    model_offline: bool = True
    model_device: Literal["auto", "cpu", "cuda"] = "cpu"
    quantity_checkpoint: str = "yolov8n.pt"
    condition_model_name: str = "ViT-B-32"
    condition_pretrained: str = "laion2b_s34b_b79k"
    condition_checkpoint: str = "open_clip_pytorch_model.bin"
    quantity_confidence_threshold: float = Field(0.65, ge=0.1, le=1)
    quantity_track_iou: float = Field(0.3, gt=0, le=1)
    quantity_class_mapping: Dict[str, str] = Field(default_factory=lambda: {
        "bottle": "bottle", "cup": "cup", "bowl": "bowl", "book": "book",
        "chair": "chair", "laptop": "laptop", "cell phone": "cell phone",
    })
    evidence_max_frames: int = Field(8, ge=2, le=32)
    evidence_min_sharpness: float = Field(30.0, ge=0)
    evidence_min_dimension: int = Field(128, ge=32)
    condition_damage_threshold: float = Field(0.90, gt=0.5, le=1)
    condition_likely_threshold: float = Field(0.75, gt=0.5, le=1)
    condition_intact_threshold: float = Field(0.80, gt=0.5, le=1)
    condition_margin_threshold: float = Field(0.25, gt=0, le=1)
    condition_edge_change_threshold: float = Field(0.18, gt=0, le=1)
    # Preserve uploads and supporting frames; optionally remove only unselected
    # frames created by the current quantity/condition request.
    retain_unselected_evidence_frames: bool = True


settings = Settings()

# Backward-compatible exports for existing modules
STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = settings.upload_dir
FRAME_DIR = settings.frame_dir
RESULT_DIR = settings.result_dir
REPORT_DIR = settings.report_dir
REFERENCE_DIR = settings.reference_dir
ALLOWED_IMAGE_EXTENSIONS = settings.allowed_image_extensions
ALLOWED_REFERENCE_EXTENSIONS = settings.allowed_reference_extensions
ALLOWED_VIDEO_EXTENSIONS = settings.allowed_video_extensions
MAX_FILE_SIZE = settings.max_file_size
MAX_REFERENCE_FILE_SIZE = settings.max_reference_file_size
MAX_REFERENCE_IMAGES_PER_ITEM = settings.max_reference_images_per_item
MAX_OCR_FRAMES = settings.max_ocr_frames

# Ensure storage directories exist
for directory in (UPLOAD_DIR, FRAME_DIR, RESULT_DIR, REPORT_DIR, REFERENCE_DIR):
    directory.mkdir(parents=True, exist_ok=True)
