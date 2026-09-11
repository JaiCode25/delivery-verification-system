import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import RESULT_DIR
from app.services.code_detector import detect_codes
from app.services.frame_extractor import prepare_frames
from app.services.ocr_service import extract_text


def analyze_media(media_path: Path, media_type: str, original_name: str) -> dict:
    analysis_id = uuid4().hex
    frames = prepare_frames(media_path, media_type, analysis_id)
    codes_by_key: dict[tuple[str, str], dict] = {}
    text_by_value: dict[str, dict] = {}

    for index, frame_path in enumerate(frames):
        frame_name = frame_path.name if media_type == "video" else original_name
        for code in detect_codes(frame_path):
            key = (code["type"], code["text"])
            codes_by_key.setdefault(key, {**code, "first_seen_in": frame_name})
        for item in extract_text(frame_path):
            key = item["text"].casefold()
            current = text_by_value.get(key)
            if current is None or item["confidence"] > current["confidence"]:
                text_by_value[key] = {**item, "first_seen_in": frame_name}

    result = {
        "analysis_id": analysis_id,
        "status": "completed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "original_filename": original_name,
        "input_type": media_type,
        "frames_processed": len(frames),
        "codes": list(codes_by_key.values()),
        "detected_text": list(text_by_value.values()),
    }
    (RESULT_DIR / f"{analysis_id}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result
