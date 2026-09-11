from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
FRAME_DIR = STORAGE_DIR / "frames"
RESULT_DIR = STORAGE_DIR / "results"

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
MAX_FILE_SIZE = 200 * 1024 * 1024

for directory in (UPLOAD_DIR, FRAME_DIR, RESULT_DIR):
    directory.mkdir(parents=True, exist_ok=True)
