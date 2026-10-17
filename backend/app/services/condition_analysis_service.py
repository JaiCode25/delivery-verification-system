"""Visible surface-condition support; never infer internal or functional damage."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import ConditionAnalysisRecord
from app.schemas.condition_analysis import (
    ConditionAnalysisResult, ConditionStatus, EvidenceType, VisualCue, ZeroShotScores,
)
from app.services import evidence_service as evidence, model_adapters
from app.services.reference_service import get_reference_absolute_paths


def compare_surface(image, references: list[tuple[str, Path]], frame_name: str) -> VisualCue:
    """Compare aligned edges only for similar viewpoints with strong ORB agreement.

    Edge changes are non-diagnostic corroboration, never a damage classification
    on their own. Failed alignment/background/lighting changes remain uncertain.
    """
    cue = VisualCue(frame_filename=frame_name)
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    orb = cv2.ORB_create(nfeatures=1500)
    keypoints, descriptors = orb.detectAndCompute(gray, None)
    if descriptors is None:
        return cue
    candidates = []
    for reference_id, path in references:
        try:
            reference = cv2.cvtColor(np.asarray(evidence.load_rgb(path)), cv2.COLOR_RGB2GRAY)
            ref_points, ref_descriptors = orb.detectAndCompute(reference, None)
            if ref_descriptors is None:
                continue
            pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(ref_descriptors, descriptors, k=2)
            matches = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.7 * pair[1].distance]
            if len(matches) < 25:
                continue
            source = np.float32([ref_points[m.queryIdx].pt for m in matches])
            target = np.float32([keypoints[m.trainIdx].pt for m in matches])
            transform, inliers = cv2.findHomography(source, target, cv2.RANSAC, 3.0)
            if transform is None or inliers is None or float(inliers.mean()) < 0.8:
                continue
            # Restrict to near-matching views; uncertain viewpoint changes must
            # not count as damaged surfaces.
            if (np.linalg.norm(transform[2, :2]) > 0.0002
                    or not 0.8 < abs(np.linalg.det(transform[:2, :2])) < 1.25):
                continue
            height, width = gray.shape
            hull = cv2.convexHull(target[inliers.ravel().astype(bool)])
            if cv2.contourArea(hull) < width * height * 0.4:
                continue
            aligned = cv2.warpPerspective(reference, transform, (width, height))
            valid = cv2.warpPerspective(np.full(reference.shape, 255, dtype=np.uint8), transform, (width, height))
            region = np.zeros_like(gray)
            cv2.fillConvexPoly(region, hull.astype(np.int32), 255)
            valid = cv2.erode(cv2.bitwise_and(valid, region), np.ones((9, 9), np.uint8)) > 0
            if valid.mean() < 0.35:
                continue
            # Reject substantial photometric changes before comparing geometry.
            correlation = np.corrcoef(gray[valid], aligned[valid])[0, 1]
            if not np.isfinite(correlation) or correlation < 0.85:
                continue
            a = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 80, 160) > 0
            b = cv2.Canny(cv2.GaussianBlur(aligned, (5, 5), 0), 80, 160) > 0
            near_b = cv2.dilate(b.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            near_a = cv2.dilate(a.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            changes = ((a & ~near_b) | (b & ~near_a)) & valid
            fraction = float(changes.sum()) / max(int(((a | b) & valid).sum()), 1)
            candidates.append((fraction, reference_id))
        except (OSError, ValueError, cv2.error):
            continue
    if candidates:
        # Least changed comparable reference prevents picking a misleading view.
        fraction, reference_id = min(candidates)
        cue.reference_id = reference_id
        cue.reference_aligned = True
        cue.edge_change_fraction = round(fraction, 4)
        cue.supports_surface_change = fraction >= settings.condition_edge_change_threshold
        cue.description = "Aligned surface edges differ; this is a non-diagnostic structural cue." if cue.supports_surface_change else "Comparable reference shows no substantial aligned edge change."
    return cue


def evaluate_condition(db: Session, order_id: str, session_id: str, media_path: Path,
                       media_type: str, evidence_type: EvidenceType,
                       regenerate: bool = False) -> ConditionAnalysisResult:
    order = evidence.validate_context(db, order_id, session_id)
    existing = evidence.existing_analysis(db, ConditionAnalysisRecord, session_id, regenerate)
    analysis_id = existing.id if existing else uuid4().hex
    result = ConditionAnalysisResult(
        condition_analysis_id=analysis_id, order_id=order_id, verification_session_id=session_id,
        evidence_type=evidence_type, status=ConditionStatus.INSUFFICIENT_EVIDENCE,
        model_metadata=model_adapters.model_metadata("condition"), created_at=datetime.now(timezone.utc),
        warnings=["Only visible external condition is assessed. Internal, electrical and functional condition are not assessed.",
                  "Zero-shot scores are uncalibrated supporting evidence, not probabilities of actual damage."],
    )
    frames, result.supporting_frames = evidence.prepare_evidence(media_path, media_type, "cond_" + uuid4().hex)
    useful = [(f, m) for f, m in zip(frames, result.supporting_frames) if m.quality_adequate]
    if not useful:
        result.warnings.append("Evidence is blurred, poorly exposed, too small or unreadable.")
    elif evidence_type == EvidenceType.UNKNOWN:
        result.status = ConditionStatus.MANUAL_REVIEW_REQUIRED
        result.warnings.append("Specify whether the evidence shows a product, sealed package or opened package.")
    else:
        # Stored references describe products. Never compare packaging against a
        # product reference and call the expected visual difference damage.
        references = get_reference_absolute_paths(db, order_id) if evidence_type == EvidenceType.PRODUCT else []
        try:
            for frame, meta in useful:
                image = evidence.load_rgb(frame.path)
                damaged, intact = model_adapters.classify_condition(image, evidence_type.value)
                scores = ZeroShotScores(frame_filename=meta.frame_filename, damage=damaged,
                                        intact=intact, margin=round(abs(damaged - intact), 4))
                result.zero_shot_scores.append(scores)
                result.visual_cues.append(compare_surface(image, references, meta.frame_filename))
            result.model_metadata.available = True
            result.model_metadata.device = model_adapters.resolve_device()
            decide_condition(result, media_type)
        except model_adapters.ModelUnavailable as exc:
            result.status = ConditionStatus.MODEL_UNAVAILABLE
            result.warnings.append(str(exc))
    evidence.persist_analysis(db, ConditionAnalysisRecord, existing, result, order, analysis_id)
    return result


def decide_condition(result: ConditionAnalysisResult, media_type: str) -> None:
    scores = result.zero_shot_scores
    positive = [s.damage >= settings.condition_likely_threshold and s.margin >= settings.condition_margin_threshold for s in scores]
    negative = [s.intact >= settings.condition_intact_threshold and s.margin >= settings.condition_margin_threshold for s in scores]
    supporting = [c.supports_surface_change for c in result.visual_cues]
    strong = [s.damage >= settings.condition_damage_threshold for s in scores]
    enough_frames = media_type == "image" or len(scores) >= 2
    if any(positive) and any(negative):
        result.status = ConditionStatus.MANUAL_REVIEW_REQUIRED
        result.warnings.append("Useful frames disagree about visible condition.")
    elif not enough_frames:
        result.status = ConditionStatus.INSUFFICIENT_EVIDENCE
        result.warnings.append("Video requires at least two useful observations.")
    elif all(positive) and all(supporting):
        # A single picture cannot establish strong multi-view damage agreement.
        result.status = ConditionStatus.DAMAGED if len(scores) >= 2 and all(strong) else ConditionStatus.LIKELY_DAMAGED
        result.damage_detected = True if result.status == ConditionStatus.DAMAGED else None
        result.damage_categories = ["visible_surface_change"]
        result.confidence = round(min(s.damage for s in scores), 4)
        result.mismatch_reasons.append("Damage prompts and comparable surface-change cues agree; human confirmation is required.")
    elif all(negative) and not any(supporting):
        result.status = ConditionStatus.NO_VISIBLE_DAMAGE
        result.damage_detected = False
        result.confidence = round(min(s.intact for s in scores), 4)
        result.warnings.append("No visible damage detected in the supplied views; unseen surfaces remain unassessed.")
    else:
        result.status = ConditionStatus.MANUAL_REVIEW_REQUIRED
        result.warnings.append("Insufficient prompt margin, frame agreement or independent structural support; damage is unconfirmed.")
    if not all(c.reference_aligned for c in result.visual_cues):
        result.warnings.append("Comparable reference alignment unavailable for one or more frames. Packaging needs human review for positive damage signals.")
