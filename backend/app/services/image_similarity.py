from pathlib import Path
from typing import Optional, Tuple, Union
import cv2
import imagehash
import numpy as np
from PIL import Image

from app.config import settings


def compute_perceptual_hash_similarity(
    img1: Union[Path, str, np.ndarray, Image.Image],
    img2: Union[Path, str, np.ndarray, Image.Image],
) -> float:
    """Compute perceptual hash similarity between two images using ImageHash (pHash).

    Returns a normalized similarity score in [0.0, 1.0].
    """
    try:
        pil1 = _to_pil_image(img1)
        pil2 = _to_pil_image(img2)
        if pil1 is None or pil2 is None:
            return 0.0

        # Compute both pHash and dHash for robust perceptual representation
        phash1 = imagehash.phash(pil1)
        phash2 = imagehash.phash(pil2)
        diff_p = phash1 - phash2

        dhash1 = imagehash.dhash(pil1)
        dhash2 = imagehash.dhash(pil2)
        diff_d = dhash1 - dhash2

        # Average distance across hashes
        avg_diff = (diff_p + diff_d) / 2.0

        # Max meaningful difference for 64-bit hash is ~32 (50% bits changed)
        # diff 0 -> score 1.0; diff 32+ -> score 0.0
        score = max(0.0, min(1.0, 1.0 - (avg_diff / 32.0)))
        return round(float(score), 4)
    except Exception:
        return 0.0


def compute_colour_similarity(
    img1: Union[Path, str, np.ndarray, Image.Image],
    img2: Union[Path, str, np.ndarray, Image.Image],
) -> float:
    """Compute HSV colour histogram correlation similarity between two images.

    Returns a normalized score in [0.0, 1.0].
    """
    try:
        cv_img1 = _to_cv_image(img1)
        cv_img2 = _to_cv_image(img2)
        if cv_img1 is None or cv_img2 is None or cv_img1.size == 0 or cv_img2.size == 0:
            return 0.0

        hsv1 = cv2.cvtColor(cv_img1, cv2.COLOR_BGR2HSV)
        hsv2 = cv2.cvtColor(cv_img2, cv2.COLOR_BGR2HSV)

        # 2D H-S histogram (30 Hue bins, 32 Saturation bins)
        hist1 = cv2.calcHist([hsv1], [0, 1], None, [30, 32], [0, 180, 0, 256])
        hist2 = cv2.calcHist([hsv2], [0, 1], None, [30, 32], [0, 180, 0, 256])

        cv2.normalize(hist1, hist1, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
        cv2.normalize(hist2, hist2, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)

        correl = cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)

        # Correlation ranges from -1.0 to 1.0; map negative to 0.0
        score = max(0.0, min(1.0, float(correl)))
        return round(score, 4)
    except Exception:
        return 0.0


def compute_orb_feature_similarity(
    img1: Union[Path, str, np.ndarray, Image.Image],
    img2: Union[Path, str, np.ndarray, Image.Image],
    nfeatures: int = 500,
    ratio_threshold: float = 0.75,
) -> float:
    """Compute local visual feature similarity using OpenCV ORB + BFMatcher with Lowe's ratio test.

    Returns a normalized score in [0.0, 1.0] without crashing on blank/featureless images.
    """
    try:
        cv_img1 = _to_cv_image(img1)
        cv_img2 = _to_cv_image(img2)
        if cv_img1 is None or cv_img2 is None or cv_img1.size == 0 or cv_img2.size == 0:
            return 0.0

        gray1 = cv2.cvtColor(cv_img1, cv2.COLOR_BGR2GRAY) if len(cv_img1.shape) == 3 else cv_img1
        gray2 = cv2.cvtColor(cv_img2, cv2.COLOR_BGR2GRAY) if len(cv_img2.shape) == 3 else cv_img2

        orb = cv2.ORB_create(nfeatures=nfeatures)
        kp1, desc1 = orb.detectAndCompute(gray1, None)
        kp2, desc2 = orb.detectAndCompute(gray2, None)

        if desc1 is None or desc2 is None or len(desc1) < 4 or len(desc2) < 4:
            return 0.0

        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        raw_matches = bf.knnMatch(desc1, desc2, k=2)

        good_matches = 0
        for pair in raw_matches:
            if len(pair) == 2:
                m, n = pair
                if m.distance < ratio_threshold * n.distance:
                    good_matches += 1

        # Normalize score: 25+ high-confidence inlier matches indicates a confident match
        # Also relative to min descriptor count
        min_desc = min(len(desc1), len(desc2))
        ratio_score = good_matches / max(1, min_desc)
        count_score = min(1.0, good_matches / 25.0)

        # Combined score with emphasis on inlier ratio and count
        combined = 0.5 * ratio_score + 0.5 * count_score
        return round(float(min(1.0, max(0.0, combined))), 4)
    except Exception:
        return 0.0


# Optional OpenCLIP Adapter (Disabled by default, lazy-loaded)
_openclip_model = None
_openclip_preprocess = None


def compute_semantic_similarity(
    img1: Union[Path, str, np.ndarray, Image.Image],
    img2: Union[Path, str, np.ndarray, Image.Image],
) -> Optional[float]:
    """Optional OpenCLIP embedding similarity adapter.

    Returns None if disabled or unavailable.
    """
    if not settings.enable_openclip:
        return None

    global _openclip_model, _openclip_preprocess
    try:
        import torch
        import open_clip

        if _openclip_model is None:
            _openclip_model, _, _openclip_preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained="laion2b_s34b_b79k"
            )
            _openclip_model.eval()

        pil1 = _to_pil_image(img1)
        pil2 = _to_pil_image(img2)
        if pil1 is None or pil2 is None:
            return 0.0

        t1 = _openclip_preprocess(pil1).unsqueeze(0)
        t2 = _openclip_preprocess(pil2).unsqueeze(0)

        with torch.no_grad():
            emb1 = _openclip_model.encode_image(t1)
            emb2 = _openclip_model.encode_image(t2)
            emb1 = emb1 / emb1.norm(dim=-1, keepdim=True)
            emb2 = emb2 / emb2.norm(dim=-1, keepdim=True)
            cosine = (emb1 @ emb2.T).item()

        score = max(0.0, min(1.0, (cosine + 1.0) / 2.0))
        return round(float(score), 4)
    except Exception:
        return None


def _to_pil_image(img: Union[Path, str, np.ndarray, Image.Image]) -> Optional[Image.Image]:
    """Convert various image formats to PIL Image."""
    if isinstance(img, Image.Image):
        return img
    elif isinstance(img, np.ndarray):
        if len(img.shape) == 2:
            return Image.fromarray(img)
        elif len(img.shape) == 3:
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb)
    elif isinstance(img, (str, Path)):
        p = Path(img)
        if p.exists():
            return Image.open(str(p)).convert("RGB")
    return None


def _to_cv_image(img: Union[Path, str, np.ndarray, Image.Image]) -> Optional[np.ndarray]:
    """Convert various image formats to OpenCV BGR numpy array."""
    if isinstance(img, np.ndarray):
        if img.dtype == bool:
            return (img.astype(np.uint8) * 255)
        return img
    elif isinstance(img, Image.Image):
        pil_rgb = img.convert("RGB")
        rgb = np.array(pil_rgb, dtype=np.uint8)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    elif isinstance(img, (str, Path)):
        p = Path(img)
        if p.exists():
            cv_img = cv2.imread(str(p))
            if cv_img is None:
                try:
                    pil_img = Image.open(str(p)).convert("RGB")
                    rgb_arr = np.array(pil_img, dtype=np.uint8)
                    cv_img = cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2BGR)
                except Exception:
                    return None
            return cv_img
    return None
