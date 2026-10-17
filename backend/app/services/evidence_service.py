"""Shared validation, frame quality and persistence for optional evidence channels."""
from pathlib import Path
from typing import Type
from anyio import to_thread

import cv2
import numpy as np
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import Order, VerificationSession
from app.schemas.evidence import SupportingFrame
from app.services import file_handler, frame_extractor
from app.services.frame_selection_service import CandidateFrame, select_candidate_frames


def validate_context(db: Session, order_id: str, session_id: str) -> Order:
    order = db.query(Order).filter(Order.order_id == order_id).first()
    if order is None:
        raise HTTPException(404, "Order not found.")
    session = db.get(VerificationSession, session_id)
    if session is None:
        raise HTTPException(404, "Verification session not found.")
    if session.order_id != order.id:
        raise HTTPException(409, "Verification session is not linked to the supplied order.")
    return order


def existing_analysis(db: Session, record_type: type, session_id: str, regenerate: bool):
    record = db.query(record_type).filter(record_type.verification_session_id == session_id).first()
    if record is not None and not regenerate:
        raise HTTPException(409, "Analysis already exists. Use regenerate=true to replace its result.")
    return record


def persist_analysis(db: Session, record_type: type, existing, result: BaseModel, order: Order, analysis_id: str) -> None:
    record = existing if existing is not None else record_type(
        id=analysis_id, order_id=order.id, verification_session_id=result.verification_session_id,
    )
    record.status = result.status.value
    record.details = result.model_dump(mode="json")
    record.created_at = result.created_at
    db.add(record)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "An analysis already exists for this session; retrieve or regenerate it.") from exc


def retrieve_analysis(db: Session, record_type: type, schema: Type[BaseModel], analysis_id: str) -> BaseModel:
    record = db.get(record_type, analysis_id)
    if record is None:
        raise HTTPException(404, "Analysis not found.")
    return schema.model_validate(record.details)


async def analyze_upload(db: Session, order_id: str, session_id: str, file: UploadFile,
                         regenerate: bool, record_type: type, evaluator, *options) -> tuple[BaseModel, bool]:
    validate_context(db, order_id, session_id)
    existing = existing_analysis(db, record_type, session_id, regenerate)
    path, media_type = await save_evidence(file)
    try:
        result = await to_thread.run_sync(evaluator, db, order_id, session_id, path, media_type, *options, regenerate)
        return result, existing is None
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, "Evidence analysis failed unexpectedly.") from exc


async def save_evidence(file: UploadFile) -> tuple[Path, str]:
    """Add strict MIME/extension agreement while preserving legacy upload APIs."""
    extension = Path((file.filename or "").replace("\\", "/")).suffix.lower()
    mimes = {
        ".jpg": {"image/jpeg"}, ".jpeg": {"image/jpeg"}, ".png": {"image/png"},
        ".bmp": {"image/bmp", "image/x-ms-bmp"}, ".webp": {"image/webp"},
        ".mp4": {"video/mp4"}, ".mov": {"video/quicktime"},
        ".avi": {"video/x-msvideo", "video/avi"}, ".mkv": {"video/x-matroska"},
        ".webm": {"video/webm"},
    }
    if extension not in mimes or file.content_type not in mimes[extension]:
        raise HTTPException(415, "Unsupported media extension or MIME type.")
    if extension not in settings.allowed_image_extensions | settings.allowed_video_extensions:
        raise HTTPException(415, "Media extension is disabled.")
    path, media_type = await file_handler.save_upload(file)
    if media_type == "image":
        try:
            with Image.open(path) as image:
                expected = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".bmp": "BMP", ".webp": "WEBP"}
                if image.format != expected[extension]:
                    raise ValueError("Image format mismatch")
                image.verify()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            # Only this request's newly created invalid upload is removed.
            path.unlink(missing_ok=True)
            raise HTTPException(400, "Corrupt image or extension/content mismatch.") from exc
    return path, media_type


def load_rgb(path: Path) -> Image.Image:
    """Normalize EXIF and palette/one-bit modes without changing source evidence."""
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1280, 1280))
        return image.copy()


def quality_frame(candidate: CandidateFrame) -> SupportingFrame:
    try:
        rgb = load_rgb(candidate.path)
        gray = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2GRAY)
        adequate = (
            min(rgb.size) >= settings.evidence_min_dimension
            and frame_extractor.compute_sharpness(gray) >= settings.evidence_min_sharpness
            and 15 < float(gray.mean()) < 240
            and float(gray.std()) > 8
        )
    except (OSError, ValueError):
        adequate = False
    return SupportingFrame(
        frame_filename=candidate.frame_name, frame_index=candidate.frame_index,
        timestamp_seconds=candidate.timestamp_seconds, sharpness=candidate.sharpness,
        quality_adequate=adequate,
    )


def prepare_evidence(path: Path, media_type: str, analysis_tag: str) -> tuple[list[CandidateFrame], list[SupportingFrame]]:
    try:
        frames = select_candidate_frames(
            path, media_type, analysis_tag,
            max_candidates=settings.evidence_max_frames,
            min_sharpness=settings.evidence_min_sharpness,
            # Quantity needs repeated observations for stable counts/tracks.
            duplicate_hash_threshold=6 if analysis_tag.startswith("cond_") else -1,
        )
    except (ValueError, OSError):
        return [], []
    metadata = [quality_frame(frame) for frame in frames]
    if media_type == "video" and not settings.retain_unselected_evidence_frames:
        directory = (frame_extractor.FRAME_DIR / analysis_tag).resolve()
        directory.relative_to(frame_extractor.FRAME_DIR.resolve())
        keep = {frame.path.resolve() for frame in frames}
        for generated in directory.glob("frame_*.jpg"):
            if generated.resolve() not in keep:
                generated.unlink()
    return frames, metadata
