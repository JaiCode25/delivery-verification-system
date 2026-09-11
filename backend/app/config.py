from pathlib import Path
from typing import List, Set

from pydantic_settings import BaseSettings, SettingsConfigDict

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
    max_file_size: int = 200 * 1024 * 1024  # 200 MB
    allowed_image_extensions: Set[str] = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
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


settings = Settings()

# Backward-compatible exports for existing modules
STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = settings.upload_dir
FRAME_DIR = settings.frame_dir
RESULT_DIR = settings.result_dir
REPORT_DIR = settings.report_dir
ALLOWED_IMAGE_EXTENSIONS = settings.allowed_image_extensions
ALLOWED_VIDEO_EXTENSIONS = settings.allowed_video_extensions
MAX_FILE_SIZE = settings.max_file_size

# Ensure storage directories exist
for directory in (UPLOAD_DIR, FRAME_DIR, RESULT_DIR, REPORT_DIR):
    directory.mkdir(parents=True, exist_ok=True)
