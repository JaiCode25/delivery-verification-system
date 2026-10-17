"""Conservative visible-object counting, not inventory inference from expected quantity."""
from datetime import datetime, timezone
import math
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import QuantityVerificationRecord
from app.schemas.evidence import ObjectDetection
from app.schemas.quantity_verification import QuantityStatus, QuantityVerificationResult
from app.services import evidence_service as evidence, model_adapters


def box_iou(a: list[float], b: list[float]) -> float:
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    return intersection / max(area_a + area_b - intersection, 1e-9)


class FrameTracker:
    """Request-local one-to-one IoU association. IDs are audit aids, never summed.

    Sampled frames cannot prove lifetime object identity under camera motion,
    disappearance or re-entry. Count only simultaneously visible objects.
    """

    def __init__(self) -> None:
        self.previous: list[ObjectDetection] = []
        self.next_id = 1

    def update(self, detections: list[ObjectDetection]) -> None:
        used: set[int] = set()
        for detection in sorted(detections, key=lambda d: d.confidence, reverse=True):
            options = [(box_iou(detection.box, old.box), old.track_id) for old in self.previous
                       if old.track_id not in used and old.class_name == detection.class_name]
            score, track_id = max(options, default=(0, None))
            if score >= settings.quantity_track_iou:
                detection.track_id = track_id
                used.add(track_id)
            else:
                detection.track_id = self.next_id
                self.next_id += 1
        self.previous = detections


def evaluate_quantity(db: Session, order_id: str, session_id: str, media_path: Path,
                      media_type: str, regenerate: bool = False) -> QuantityVerificationResult:
    order = evidence.validate_context(db, order_id, session_id)
    existing = evidence.existing_analysis(db, QuantityVerificationRecord, session_id, regenerate)
    if not order.items or any(item.expected_quantity is None or item.expected_quantity <= 0 for item in order.items):
        raise HTTPException(400, "Stored expected quantities must be positive.")
    expected = sum(item.expected_quantity for item in order.items)
    analysis_id = existing.id if existing else uuid4().hex
    product_name = " ".join(order.items[0].product_name.casefold().split())
    mapping = {" ".join(key.casefold().split()): value for key, value in settings.quantity_class_mapping.items()}
    target = mapping.get(product_name) if len(order.items) == 1 else None
    result = QuantityVerificationResult(
        quantity_analysis_id=analysis_id, order_id=order_id, verification_session_id=session_id,
        expected_quantity=expected, target_class=target, status=QuantityStatus.INSUFFICIENT_EVIDENCE,
        model_metadata=model_adapters.model_metadata("quantity"), created_at=datetime.now(timezone.utc),
        warnings=["Counts concern visible objects of the configured class, not hidden contents or product identity. "
                  "Present all items together without occlusion; track IDs are approximate across sampled frames."],
    )
    if target is None:
        result.status = QuantityStatus.UNSUPPORTED_PRODUCT
        result.warnings.append("No exact configured detector-class mapping, or multiple order items require separate counting. Expected quantity was not used as a detected count.")
    else:
        frames, result.supporting_frames = evidence.prepare_evidence(media_path, media_type, "qty_" + uuid4().hex)
        useful = [(frame, meta) for frame, meta in zip(frames, result.supporting_frames) if meta.quality_adequate]
        if not useful:
            result.warnings.append("No adequately sharp, exposed, readable evidence frames.")
        else:
            _count_frames(result, useful, media_type)
    evidence.persist_analysis(db, QuantityVerificationRecord, existing, result, order, analysis_id)
    return result


def _count_frames(result: QuantityVerificationResult, useful: list, media_type: str) -> None:
    tracker = FrameTracker()
    counts: list[int] = []
    uncertain = False
    try:
        for frame, meta in useful:
            image = evidence.load_rgb(frame.path)
            raw = model_adapters.detect_objects(image, result.target_class)
            result.model_metadata.available = True
            result.model_metadata.device = model_adapters.resolve_device()
            width, height = image.size
            detections: list[ObjectDetection] = []
            for item in raw:
                if item.get("class_name") != result.target_class:
                    continue
                confidence = float(item.get("confidence", 0))
                box = [float(v) for v in item.get("box", [])]
                if (len(box) != 4 or not all(math.isfinite(v) for v in box + [confidence])
                        or not 0 <= confidence <= 1 or not (0 <= box[0] < box[2] <= width)
                        or not (0 <= box[1] < box[3] <= height)):
                    uncertain = True
                    continue
                detection = ObjectDetection(
                    frame_filename=meta.frame_filename, timestamp_seconds=meta.timestamp_seconds,
                    box=box, class_name=result.target_class, confidence=round(confidence, 4),
                    image_width=width, image_height=height,
                )
                result.detections.append(detection)
                if confidence < settings.quantity_confidence_threshold:
                    uncertain = True
                    continue
                # Truncation or overlapping objects make completeness unreliable.
                if min(box[0], box[1], width - box[2], height - box[3]) < 3:
                    uncertain = True
                if any(box_iou(box, old.box) > 0.35 for old in detections):
                    uncertain = True
                detections.append(detection)
            tracker.update(detections)
            counts.append(len(detections))
    except model_adapters.ModelUnavailable as exc:
        result.status = QuantityStatus.MODEL_UNAVAILABLE
        result.model_metadata.available = False
        result.warnings.append(str(exc))
        return
    if not counts or max(counts) == 0:
        result.warnings.append("No reliable objects detected; absence of detections does not prove zero delivered items.")
    elif uncertain or len(set(counts)) != 1 or (media_type == "video" and len(counts) < 2):
        result.status = QuantityStatus.MANUAL_REVIEW_REQUIRED
        result.warnings.append("Counts are uncertain, truncated, overlapping, inconsistent, or lack video confirmation.")
    else:
        result.detected_quantity = counts[0]
        result.confidence = round(min(d.confidence for d in result.detections if d.track_id is not None), 4)
        result.status = QuantityStatus.MATCH if counts[0] == result.expected_quantity else QuantityStatus.MISMATCH
        if result.status == QuantityStatus.MISMATCH:
            result.mismatch_reasons.append(f"Visible count {counts[0]} differs from stored expected quantity {result.expected_quantity}.")
