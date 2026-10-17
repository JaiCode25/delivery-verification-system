from pathlib import Path
from typing import Any, Dict, List

import cv2
import numpy as np
from PIL import Image, ImageOps
import zxingcpp


def detect_codes(image_path: Path) -> List[Dict[str, Any]]:
    """Detect and decode QR codes and barcodes using ZXing-C++, including bounding coordinates."""
    image = cv2.imread(str(image_path))
    if image is None:
        try:
            pil_img = Image.open(image_path)
            pil_img = ImageOps.exif_transpose(pil_img)
            if pil_img.mode in ("1", "L", "LA"):
                pil_img = pil_img.convert("RGB")
            elif pil_img.mode in ("RGBA", "P"):
                pil_img = pil_img.convert("RGB")
            rgb_arr = np.array(pil_img, dtype=np.uint8)
            image = cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2BGR)
        except Exception:
            return []

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
