from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import RESULT_DIR
from app.database.connection import SessionLocal
from app.services.code_detector import detect_codes
from app.services.frame_extractor import extract_frame_metadata
from app.services.ocr_service import extract_text
from app.services.verification_engine import verify_detected_codes


def analyze_media(
    media_path: Path,
    media_type: str,
    original_name: str,
    expected_platform: Optional[str] = None,
    expected_order_id: Optional[str] = None,
    expected_customer_name: Optional[str] = None,
    expected_product_name: Optional[str] = None,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """Execute end-to-end analysis on uploaded image or video.

    Extracts frames with timestamps and sharpness scoring, retains the sharpest
    frame for duplicate barcodes, runs OCR, and cross-verifies against SQLite orders.
    """
    analysis_id = uuid4().hex
    frames_meta = extract_frame_metadata(media_path, media_type, analysis_id)

    # Keyed by (code_type, code_text)
    codes_by_key: Dict[tuple[str, str], Dict[str, Any]] = {}
    text_by_value: Dict[str, Dict[str, Any]] = {}

    for frame in frames_meta:
        frame_name = frame.path.name if media_type == "video" else original_name

        # 1. Barcode / QR detection with sharpness-based selection for duplicates
        detected = detect_codes(frame.path)
        for code in detected:
            key = (code["type"], code["text"])
            existing = codes_by_key.get(key)
            # Retain the detection from the frame with the highest sharpness score
            if existing is None or frame.sharpness > existing.get("sharpness", -1.0):
                detection_entry = {
                    "type": code["type"],
                    "text": code["text"],
                    "first_seen_in": frame_name,
                    "sharpness": frame.sharpness,
                    "video_timestamp_seconds": frame.timestamp_seconds,
                }
                if "position" in code:
                    detection_entry["position"] = code["position"]
                codes_by_key[key] = detection_entry

        # 2. OCR text extraction with highest-confidence retention
        for item in extract_text(frame.path):
            key = item["text"].casefold()
            current = text_by_value.get(key)
            if current is None or item["confidence"] > current["confidence"]:
                text_by_value[key] = {
                    "text": item["text"],
                    "confidence": item["confidence"],
                    "first_seen_in": frame_name,
                    "video_timestamp_seconds": frame.timestamp_seconds,
                }

    detected_codes_list = list(codes_by_key.values())

    # 3. Barcode-to-Order Verification
    own_session = False
    session = db
    if session is None:
        session = SessionLocal()
        own_session = True

    try:
        verification_res = verify_detected_codes(
            db=session,
            detected_codes=detected_codes_list,
            expected_platform=expected_platform,
            expected_order_id=expected_order_id,
            expected_customer_name=expected_customer_name,
            expected_product_name=expected_product_name,
        )
        verification_dict = verification_res.model_dump(mode="json")
    finally:
        if own_session and session:
            session.close()

    result: Dict[str, Any] = {
        "analysis_id": analysis_id,
        "status": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "original_filename": original_name,
        "input_type": media_type,
        "frames_processed": len(frames_meta),
        "codes": detected_codes_list,
        "detected_text": list(text_by_value.values()),
        "order_verification": verification_dict,
    }

    # Persist JSON report
    report_file = RESULT_DIR / f"{analysis_id}.json"
    report_file.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return result
