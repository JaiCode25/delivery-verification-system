from functools import lru_cache
from pathlib import Path

import easyocr


@lru_cache(maxsize=1)
def get_reader():
    return easyocr.Reader(["en"], gpu=False)


def _format_bounding_box(bbox) -> dict | None:
    try:
        pts = [[int(pt[0]), int(pt[1])] for pt in bbox]
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
    detections = get_reader().readtext(str(image_path), detail=1, paragraph=False)
    results = []
    for bbox, text, confidence in detections:
        clean_text = text.strip()
        conf = round(float(confidence), 3)
        if clean_text and conf >= 0.25:
            item = {
                "text": clean_text,
                "confidence": conf,
            }
            box = _format_bounding_box(bbox)
            if box:
                item["bounding_box"] = box
            results.append(item)
    return results
