from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile

from app.config import (
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_VIDEO_EXTENSIONS,
    MAX_FILE_SIZE,
    UPLOAD_DIR,
)


async def save_upload(file: UploadFile) -> tuple[Path, str]:
    extension = Path(file.filename or "").suffix.lower()
    if extension in ALLOWED_IMAGE_EXTENSIONS:
        media_type = "image"
    elif extension in ALLOWED_VIDEO_EXTENSIONS:
        media_type = "video"
    else:
        raise HTTPException(400, "Unsupported file type")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty")
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, "File exceeds the 200 MB limit")

    destination = UPLOAD_DIR / f"{uuid4().hex}{extension}"
    destination.write_bytes(data)
    return destination, media_type
