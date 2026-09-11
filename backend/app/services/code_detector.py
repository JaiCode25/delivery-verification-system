from pathlib import Path
from typing import Any, Dict, List

import cv2
import zxingcpp


def detect_codes(image_path: Path) -> List[Dict[str, Any]]:
    """Detect and decode QR codes and barcodes using ZXing-C++, including bounding coordinates."""
    image = cv2.imread(str(image_path))
    if image is None:
        return []

    results: List[Dict[str, Any]] = []
    for barcode in zxingcpp.read_barcodes(image):
        text = barcode.text.strip()
        if not text:
            continue

        item: Dict[str, Any] = {
            "type": str(barcode.format),
            "text": text,
        }

        # Extract corner coordinates if provided by ZXing
        if hasattr(barcode, "position") and barcode.position:
            try:
                pos = barcode.position
                item["position"] = {
                    "top_left": {"x": int(pos.top_left.x), "y": int(pos.top_left.y)},
                    "top_right": {"x": int(pos.top_right.x), "y": int(pos.top_right.y)},
                    "bottom_right": {"x": int(pos.bottom_right.x), "y": int(pos.bottom_right.y)},
                    "bottom_left": {"x": int(pos.bottom_left.x), "y": int(pos.bottom_left.y)},
                }
            except Exception:
                pass

        results.append(item)
    return results
