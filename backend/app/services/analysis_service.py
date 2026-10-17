from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import MAX_OCR_FRAMES, RESULT_DIR
from app.database.connection import SessionLocal
from app.database.models import VerificationSession
from app.schemas.enums import LabelVerificationStatus, VerificationStatus
from app.services.code_detector import detect_codes
from app.services.frame_extractor import extract_frame_metadata
from app.services.label_parser import parse_shipping_label
from app.services.label_verification_service import cross_check_barcode_and_label
from app.services.ocr_service import extract_text
from app.services.order_service import find_order_by_barcode, get_order_by_order_id
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
    frame for duplicate barcodes, executes structured label OCR, and performs a
    two-factor cross-check comparing barcode and shipping-label evidence.
    """
    analysis_id = uuid4().hex
    frames_meta = extract_frame_metadata(media_path, media_type, analysis_id)

    # 1. Barcode / QR detection with sharpness-based selection for duplicates
    codes_by_key: Dict[tuple[str, str], Dict[str, Any]] = {}
    for frame in frames_meta:
        frame_name = frame.path.name if media_type == "video" else original_name
        detected = detect_codes(frame.path)
        for code in detected:
            key = (code["type"], code["text"])
            existing = codes_by_key.get(key)
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

    detected_codes_list = list(codes_by_key.values())

    # 2. Select frames for OCR (enforcing MAX_OCR_FRAMES limit on long videos)
    if len(frames_meta) <= MAX_OCR_FRAMES:
        ocr_frames = frames_meta
    else:
        frames_with_codes = {c["first_seen_in"] for c in codes_by_key.values()}

        def frame_priority(f):
            fn = f.path.name if media_type == "video" else original_name
            has_code = 1 if fn in frames_with_codes else 0
            return (has_code, f.sharpness)

        ocr_frames = sorted(frames_meta, key=frame_priority, reverse=True)[:MAX_OCR_FRAMES]

    # 3. OCR text extraction on selected frames
    raw_ocr_detections: List[Dict[str, Any]] = []
    text_by_value: Dict[str, Dict[str, Any]] = {}

    for frame in ocr_frames:
        frame_name = frame.path.name if media_type == "video" else original_name
        for item in extract_text(frame.path):
            annotated_item = {
                "text": item["text"],
                "confidence": item["confidence"],
                "source_frame": frame_name,
                "video_timestamp_seconds": frame.timestamp_seconds,
                "sharpness": frame.sharpness,
            }
            if "bounding_box" in item:
                annotated_item["bounding_box"] = item["bounding_box"]

            raw_ocr_detections.append(annotated_item)

            key = item["text"].casefold()
            current = text_by_value.get(key)
            if current is None or item["confidence"] > current["confidence"]:
                text_by_value[key] = {
                    "text": item["text"],
                    "confidence": item["confidence"],
                    "first_seen_in": frame_name,
                    "video_timestamp_seconds": frame.timestamp_seconds,
                }

    # 4. Shipping Label Parsing & Multi-Frame Consensus
    frames_info = [
        {
            "frame_name": f.path.name if media_type == "video" else original_name,
            "timestamp_seconds": f.timestamp_seconds,
            "sharpness": f.sharpness,
        }
        for f in ocr_frames
    ]
    parsed_label = parse_shipping_label(
        detections=raw_ocr_detections,
        frames_metadata=frames_info,
    )

    # 5. Barcode Verification and Label Cross-Check
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

        matched_order_obj = None
        if verification_res.matched_order:
            matched_order_obj = get_order_by_order_id(session, verification_res.matched_order.order_id)
            if not matched_order_obj:
                matched_order_obj = find_order_by_barcode(session, verification_res.matched_order.barcode_value)

        # Cross-check barcode verification with structured shipping label
        label_analysis_res = cross_check_barcode_and_label(
            db=session,
            parsed_label=parsed_label,
            barcode_result=verification_res,
            matched_order=matched_order_obj,
            expected_platform=expected_platform,
            expected_order_id=expected_order_id,
            expected_customer_name=expected_customer_name,
            expected_product_name=expected_product_name,
        )

        # Cross-check alert: if barcode matched but physical label contradicts it
        if matched_order_obj and label_analysis_res.status == LabelVerificationStatus.MISMATCH:
            if verification_res.verification_status == VerificationStatus.VERIFIED:
                verification_res.verification_status = VerificationStatus.DETAILS_MISMATCH
                verification_res.mismatch_reasons.append(
                    "Cross-check alert: Shipping label text contradicts the barcode-matched order record."
                )

        # Persist analysis metadata and structured label results on the VerificationSession
        if verification_res.verification_session_id:
            v_session = (
                session.query(VerificationSession)
                .filter(VerificationSession.id == verification_res.verification_session_id)
                .first()
            )
            if v_session:
                v_session.analysis_id = analysis_id
                v_session.input_type = media_type
                v_session.original_filename = original_name
                v_session.label_analysis_status = (
                    label_analysis_res.status.value
                    if hasattr(label_analysis_res.status, "value")
                    else str(label_analysis_res.status)
                )
                v_session.label_summary = label_analysis_res.summary.model_dump(mode="json")
                v_session.label_analysis_data = label_analysis_res.model_dump(mode="json")
                v_session.verification_status = (
                    verification_res.verification_status.value
                    if hasattr(verification_res.verification_status, "value")
                    else str(verification_res.verification_status)
                )
                v_session.mismatch_reasons = verification_res.mismatch_reasons
                session.commit()

        verification_dict = verification_res.model_dump(mode="json")
        label_analysis_dict = label_analysis_res.model_dump(mode="json")
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
        "label_analysis": label_analysis_dict,
    }

    # Persist JSON report
    report_file = RESULT_DIR / f"{analysis_id}.json"
    report_file.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return result
