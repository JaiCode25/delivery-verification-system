from dataclasses import dataclass
from pathlib import Path
from typing import List

import cv2

from app.config import FRAME_DIR


@dataclass
class ExtractedFrame:
    path: Path
    frame_index: int
    timestamp_seconds: float
    sharpness: float


def compute_sharpness(image) -> float:
    """Compute Laplacian variance of an image as a measure of focus and sharpness."""
    if image is None or image.size == 0:
        return 0.0
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image
    return round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2)


def extract_frame_metadata(media_path: Path, media_type: str, analysis_id: str) -> List[ExtractedFrame]:
    """Extract frames with timestamp metadata and sharpness scoring.

    Guarantees VideoCapture release via a try...finally block.
    """
    if media_type == "image":
        img = cv2.imread(str(media_path))
        sharpness = compute_sharpness(img)
        return [
            ExtractedFrame(
                path=media_path,
                frame_index=0,
                timestamp_seconds=0.0,
                sharpness=sharpness,
            )
        ]

    output_dir = FRAME_DIR / analysis_id
    output_dir.mkdir(parents=True, exist_ok=True)

    capture = cv2.VideoCapture(str(media_path))
    extracted_frames: List[ExtractedFrame] = []

    try:
        if not capture.isOpened():
            raise ValueError("The uploaded video could not be opened or is corrupted")

        fps = capture.get(cv2.CAP_PROP_FPS)
        fps_val = fps if fps > 0 else 25.0
        sample_interval = max(1, round(fps_val))
        frame_number = 0

        while True:
            success, frame = capture.read()
            if not success:
                break

            if frame_number % sample_interval == 0:
                timestamp = round(frame_number / fps_val, 2)
                frame_path = output_dir / f"frame_{len(extracted_frames):04d}.jpg"
                cv2.imwrite(str(frame_path), frame)
                sharpness = compute_sharpness(frame)
                extracted_frames.append(
                    ExtractedFrame(
                        path=frame_path,
                        frame_index=frame_number,
                        timestamp_seconds=timestamp,
                        sharpness=sharpness,
                    )
                )

            frame_number += 1
    finally:
        capture.release()

    if not extracted_frames:
        raise ValueError("No frames could be extracted from the video")

    return extracted_frames


def prepare_frames(media_path: Path, media_type: str, analysis_id: str) -> List[Path]:
    """Backward-compatible helper returning a simple list of frame paths."""
    frames_meta = extract_frame_metadata(media_path, media_type, analysis_id)
    return [f.path for f in frames_meta]
