from pathlib import Path
from typing import Tuple
from uuid import uuid4

from fastapi import HTTPException, UploadFile

from app.config import (
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_VIDEO_EXTENSIONS,
    MAX_FILE_SIZE,
    UPLOAD_DIR,
)

CHUNK_SIZE = 1024 * 1024  # 1 MB stream chunk


def validate_media_signature(header: bytes, media_type: str) -> None:
    """Validate initial header magic bytes against expected media signatures."""
    if not header or len(header) < 4:
        raise HTTPException(status_code=400, detail="Corrupt or unreadable file header")

    if media_type == "image":
        is_jpeg = header.startswith(b"\xff\xd8\xff")
        is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
        is_bmp = header.startswith(b"BM")
        is_webp = header.startswith(b"RIFF") and b"WEBP" in header[:16]

        if not (is_jpeg or is_png or is_bmp or is_webp):
            raise HTTPException(
                status_code=400,
                detail="File content does not match a valid image signature (JPEG, PNG, BMP, WEBP).",
            )
    elif media_type == "video":
        is_mp4_mov = b"ftyp" in header[:32] or b"moov" in header[:32]
        is_mkv_webm = header.startswith(b"\x1a\x45\xdf\xa3")  # EBML
        is_avi = header.startswith(b"RIFF") and b"AVI " in header[:16]

        if not (is_mp4_mov or is_mkv_webm or is_avi):
            raise HTTPException(
                status_code=400,
                detail="File content does not match a valid video signature (MP4, MKV, AVI, WEBM, MOV).",
            )


async def save_upload(file: UploadFile) -> Tuple[Path, str]:
    """Stream upload file in chunks, enforce max size, validate signatures, and delete on failure."""
    raw_filename = file.filename or ""
    extension = Path(raw_filename).suffix.lower()

    if extension in ALLOWED_IMAGE_EXTENSIONS:
        media_type = "image"
    elif extension in ALLOWED_VIDEO_EXTENSIONS:
        media_type = "video"
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: '{extension}'")

    destination = UPLOAD_DIR / f"{uuid4().hex}{extension}"
    total_bytes = 0
    header_bytes = bytearray()

    try:
        with open(destination, "wb") as out_file:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the maximum limit of {MAX_FILE_SIZE // (1024 * 1024)} MB",
                    )
                if len(header_bytes) < 32:
                    needed = 32 - len(header_bytes)
                    header_bytes.extend(chunk[:needed])
                out_file.write(chunk)

        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        validate_media_signature(bytes(header_bytes), media_type)

    except Exception:
        # Guarantee deletion of partially written or invalid files
        if destination.exists():
            try:
                destination.unlink()
            except Exception:
                pass
        raise

    return destination, media_type
