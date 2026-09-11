from pathlib import Path

import cv2
import zxingcpp


def detect_codes(image_path: Path) -> list[dict]:
    image = cv2.imread(str(image_path))
    if image is None:
        return []

    results = []
    for barcode in zxingcpp.read_barcodes(image):
        text = barcode.text.strip()
        if text:
            results.append({"type": str(barcode.format), "text": text})
    return results
