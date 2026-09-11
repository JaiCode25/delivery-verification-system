from functools import lru_cache
from pathlib import Path

import easyocr


@lru_cache(maxsize=1)
def get_reader():
    return easyocr.Reader(["en"], gpu=False)


def extract_text(image_path: Path) -> list[dict]:
    detections = get_reader().readtext(str(image_path), detail=1, paragraph=False)
    return [
        {"text": text.strip(), "confidence": round(float(confidence), 3)}
        for _, text, confidence in detections
        if text.strip() and confidence >= 0.25
    ]
