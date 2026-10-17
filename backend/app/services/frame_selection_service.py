from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
import imagehash
from PIL import Image

from app.config import MAX_OCR_FRAMES
from app.services.frame_extractor import ExtractedFrame, extract_frame_metadata


@dataclass
class CandidateFrame:
    path: Path
    frame_index: int
    timestamp_seconds: float
    sharpness: float
    frame_name: str


def select_candidate_frames(
    media_path: Path,
    media_type: str,
    analysis_id: str,
    max_candidates: int = 8,
    min_sharpness: float = 10.0,
    duplicate_hash_threshold: int = 6,
) -> List[CandidateFrame]:
    """Select the best non-duplicate sharp candidate frames from media.

    For images: returns 1 candidate frame.
    For videos: samples, filters by sharpness, removes perceptual duplicates, and returns top candidates sorted by timestamp.
    """
    raw_frames = extract_frame_metadata(media_path, media_type, analysis_id)

    if media_type == "image":
        f = raw_frames[0]
        return [
            CandidateFrame(
                path=f.path,
                frame_index=f.frame_index,
                timestamp_seconds=f.timestamp_seconds,
                sharpness=f.sharpness,
                frame_name=f.path.name,
            )
        ]

    # Sort frames by sharpness descending to pick best focus first
    sorted_by_sharpness = sorted(raw_frames, key=lambda x: x.sharpness, reverse=True)

    selected: List[ExtractedFrame] = []
    selected_hashes: List[imagehash.ImageHash] = []

    for frame in sorted_by_sharpness:
        if len(selected) >= max_candidates:
            break

        # Check sharpness threshold unless we have too few frames
        if frame.sharpness < min_sharpness and len(selected) >= 2:
            continue

        # Perceptual hash deduplication
        try:
            pil_img = Image.open(str(frame.path)).convert("RGB")
            phash = imagehash.phash(pil_img)

            # Compare against already selected frames
            is_dup = False
            for prev_hash in selected_hashes:
                if (phash - prev_hash) <= duplicate_hash_threshold:
                    is_dup = True
                    break

            if not is_dup:
                selected.append(frame)
                selected_hashes.append(phash)
        except Exception:
            # Fallback if image opening fails
            if len(selected) < max_candidates:
                selected.append(frame)

    # If deduplication was too aggressive, fallback to at least the sharpest frames
    if not selected and raw_frames:
        selected = sorted_by_sharpness[:max_candidates]

    # Sort selected frames chronologically by timestamp
    selected_chronological = sorted(selected, key=lambda x: x.timestamp_seconds)

    return [
        CandidateFrame(
            path=f.path,
            frame_index=f.frame_index,
            timestamp_seconds=f.timestamp_seconds,
            sharpness=f.sharpness,
            frame_name=f.path.name,
        )
        for f in selected_chronological
    ]
