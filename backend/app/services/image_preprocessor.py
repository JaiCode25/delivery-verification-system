"""Image preprocessing service for shipping label OCR.

Provides safe, in-memory image enhancements to improve OCR accuracy on noisy,
low-contrast, rotated, or low-resolution shipping labels without modifying
the original media files on disk.
"""

from pathlib import Path
from typing import Tuple, Union

import cv2
import numpy as np
from PIL import Image, ImageOps


def preprocess_image_for_ocr(
    image_input: Union[Path, str, np.ndarray, Image.Image],
    target_dimension: int = 1800,
    clahe_clip_limit: float = 2.0,
) -> Tuple[np.ndarray, float]:
    """Preprocess an image in-memory for optimal OCR detection.

    Steps applied:
    1. EXIF orientation correction via PIL ImageOps.exif_transpose.
    2. Aspect-preserving upscale if image is low-resolution.
    3. Grayscale conversion.
    4. Contrast Limited Adaptive Histogram Equalization (CLAHE).
    5. Gentle bilateral filtering to reduce noise while preserving character edges.

    Args:
        image_input: Path to image file, PIL Image, or numpy array.
        target_dimension: Desired maximum dimension (width or height) for upscaling.
        clahe_clip_limit: Contrast limit threshold for CLAHE.

    Returns:
        Tuple of (preprocessed_image_ndarray, scale_factor).
        The scale_factor allows mapping detected bounding boxes back to the original coordinate space.
    """
    # 1. Load image and apply EXIF orientation correction
    pil_img: Optional[Image.Image] = None
    if isinstance(image_input, (str, Path)):
        pil_img = Image.open(image_input)
    elif isinstance(image_input, Image.Image):
        pil_img = image_input
    elif isinstance(image_input, np.ndarray):
        img_np = image_input.copy()
    else:
        raise ValueError(f"Unsupported image input type: {type(image_input)}")

    if pil_img is not None:
        pil_img = ImageOps.exif_transpose(pil_img)
        # Convert palette images to RGB to recover true colours
        if pil_img.mode == "P":
            pil_img = pil_img.convert("RGB")
        elif pil_img.mode == "1":
            pil_img = pil_img.convert("L")
        elif pil_img.mode == "LA":
            pil_img = pil_img.convert("L")
        elif pil_img.mode == "RGBA":
            pil_img = pil_img.convert("RGB")
        img_np = np.array(pil_img)

    # Handle boolean or non-uint8 numpy arrays
    if img_np.dtype == bool:
        img_np = (img_np.astype(np.uint8) * 255)
    elif img_np.dtype != np.uint8:
        img_np = img_np.astype(np.uint8)

    # Ensure 2D or 3D numpy array
    if img_np.ndim == 3:
        if img_np.shape[2] == 4:
            # RGBA to RGB
            img_np = cv2.cvtColor(img_np, cv2.COLOR_RGBA2RGB)
        h, w = img_np.shape[:2]
    else:
        h, w = img_np.shape[:2]

    # 2. Aspect-preserving scale calculation
    max_dim = max(h, w)
    if max_dim < target_dimension:
        scale_factor = target_dimension / float(max_dim)
        # Cap scale factor at 2.0 to avoid excessive memory consumption
        scale_factor = min(scale_factor, 2.0)
    else:
        scale_factor = 1.0

    if scale_factor != 1.0:
        new_w = int(round(w * scale_factor))
        new_h = int(round(h * scale_factor))
        resized = cv2.resize(img_np, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    else:
        resized = img_np

    # 3. Grayscale conversion
    if resized.ndim == 3:
        gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)
    else:
        gray = resized

    # 4. CLAHE contrast enhancement
    clahe = cv2.createCLAHE(clipLimit=clahe_clip_limit, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # 5. Gentle bilateral filtering (edge-preserving denoising)
    denoised = cv2.bilateralFilter(enhanced, d=5, sigmaColor=50, sigmaSpace=50)

    return denoised, scale_factor
