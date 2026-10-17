from functools import lru_cache
from pathlib import Path

import easyocr

from app.services.image_preprocessor import preprocess_image_for_ocr


@lru_cache(maxsize=1)
def get_reader():
    return easyocr.Reader(["en"], gpu=False)


def _format_bounding_box(bbox, scale_factor: float = 1.0) -> dict | None:
    try:
        pts = [[int(round(pt[0] / scale_factor)), int(round(pt[1] / scale_factor))] for pt in bbox]
        if len(pts) == 4:
            return {
                "top_left": {"x": pts[0][0], "y": pts[0][1]},
                "top_right": {"x": pts[1][0], "y": pts[1][1]},
                "bottom_right": {"x": pts[2][0], "y": pts[2][1]},
                "bottom_left": {"x": pts[3][0], "y": pts[3][1]},
            }
    except Exception:
        pass
    return None


def extract_text(image_path: Path) -> list[dict]:
    """Extract text from an image using preprocessed OCR enhancement.

    Applies safe in-memory contrast enhancement and resolution adjustment,
    then executes EasyOCR with fine-tuned thresholds and magnification to capture
    small table digits and low-contrast labels. All bounding boxes are mapped back
    to original image coordinates.
    """
    enhanced_img, scale_factor = preprocess_image_for_ocr(image_path, target_dimension=2000)
    detections = get_reader().readtext(
        enhanced_img,
        detail=1,
        paragraph=False,
        text_threshold=0.20,
        low_text=0.20,
        width_ths=0.2,
        mag_ratio=1.5,
    )
    results = []
    for bbox, text, confidence in detections:
        clean_text = text.strip()
        conf = round(float(confidence), 3)
        if clean_text and conf >= 0.20:
            item = {
                "text": clean_text,
                "confidence": conf,
            }
            box = _format_bounding_box(bbox, scale_factor=scale_factor)
            if box:
                item["bounding_box"] = box
            results.append(item)
    return results
