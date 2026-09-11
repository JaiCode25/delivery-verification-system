from pathlib import Path

import cv2

from app.config import FRAME_DIR


def prepare_frames(media_path: Path, media_type: str, analysis_id: str) -> list[Path]:
    if media_type == "image":
        return [media_path]

    output_dir = FRAME_DIR / analysis_id
    output_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(media_path))
    if not capture.isOpened():
        raise ValueError("The uploaded video could not be opened")

    fps = capture.get(cv2.CAP_PROP_FPS)
    sample_interval = max(1, round(fps if fps > 0 else 25))
    frame_number = 0
    saved_paths: list[Path] = []

    while True:
        success, frame = capture.read()
        if not success:
            break
        if frame_number % sample_interval == 0:
            path = output_dir / f"frame_{len(saved_paths):04d}.jpg"
            cv2.imwrite(str(path), frame)
            saved_paths.append(path)
        frame_number += 1

    capture.release()
    if not saved_paths:
        raise ValueError("No frames could be extracted from the video")
    return saved_paths
