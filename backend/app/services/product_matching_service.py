from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import Order, OrderItem, ProductMatchingRecord, ProductReference
from app.schemas.enums import ProductMatchDecision
from app.schemas.product_matching import (
    ProductCandidateFrame,
    ProductMatchingComponentScores,
    ProductMatchingResult,
)
from app.services.frame_selection_service import select_candidate_frames
from app.services.image_similarity import (
    compute_colour_similarity,
    compute_orb_feature_similarity,
    compute_perceptual_hash_similarity,
    compute_semantic_similarity,
)
from app.services.ocr_service import extract_text
from app.services.reference_service import get_reference_absolute_paths


def _tokenize(text: str) -> Set[str]:
    """Tokenize and normalize text into clean alphanumeric word tokens."""
    if not text:
        return set()
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", text.casefold())
    return {w for w in cleaned.split() if len(w) > 1}


def compute_text_similarity(
    detected_texts: List[str],
    expected_brand: str,
    expected_model: str,
    expected_name: str,
    expected_colour: str,
) -> Tuple[float, List[str]]:
    """Compute text similarity score comparing detected OCR tokens with expected product attributes."""
    if not detected_texts:
        return 0.0, []

    detected_tokens: Set[str] = set()
    for line in detected_texts:
        detected_tokens.update(_tokenize(line))

    if not detected_tokens:
        return 0.0, []

    brand_tokens = _tokenize(expected_brand)
    model_tokens = _tokenize(expected_model)
    name_tokens = _tokenize(expected_name)
    colour_tokens = _tokenize(expected_colour)

    matched_indicators: List[str] = []

    # 1. Brand match (30% of text score)
    brand_score = 0.0
    if brand_tokens:
        common_brand = brand_tokens.intersection(detected_tokens)
        if common_brand:
            brand_score = len(common_brand) / len(brand_tokens)
            matched_indicators.append(f"Brand '{' '.join(common_brand)}' detected")

    # 2. Model match (40% of text score) - handles variations like 'v055' for 'v-055'
    model_score = 0.0
    if model_tokens:
        common_model = model_tokens.intersection(detected_tokens)
        if common_model:
            model_score = 1.0
            matched_indicators.append(f"Model '{' '.join(common_model)}' detected")
        else:
            # Check unspaced model concatenation
            raw_detected_blob = "".join(detected_tokens)
            clean_model = "".join(c for c in expected_model.casefold() if c.isalnum())
            if clean_model and clean_model in raw_detected_blob:
                model_score = 1.0
                matched_indicators.append(f"Model '{expected_model}' detected in text")

    # 3. Product Name keywords (20% of text score)
    name_score = 0.0
    if name_tokens:
        common_name = name_tokens.intersection(detected_tokens)
        name_score = len(common_name) / len(name_tokens)
        if common_name:
            matched_indicators.append(f"Keywords: {', '.join(common_name)}")

    # 4. Colour match (10% of text score)
    colour_score = 0.0
    if colour_tokens:
        common_colour = colour_tokens.intersection(detected_tokens)
        if common_colour:
            colour_score = 1.0

    total_text_score = (
        0.30 * brand_score
        + 0.40 * model_score
        + 0.20 * name_score
        + 0.10 * colour_score
    )

    return round(float(min(1.0, max(0.0, total_text_score))), 4), matched_indicators


def evaluate_candidate_matching(
    db: Session,
    order_id: str,
    media_path: Path,
    media_type: str,
    verification_session_id: Optional[str] = None,
    pre_extracted_ocr: Optional[List[str]] = None,
) -> ProductMatchingResult:
    """Execute hybrid product matching comparing candidate media against stored order reference images."""
    matching_id = uuid4().hex
    order = db.query(Order).filter(Order.order_id == order_id).first()

    if not order:
        return ProductMatchingResult(
            matching_id=matching_id,
            order_id=order_id,
            expected_product_name="Unknown",
            expected_brand="Unknown",
            expected_model="Unknown",
            expected_colour="Unknown",
            expected_quantity=1,
            overall_match_score=0.0,
            decision=ProductMatchDecision.INSUFFICIENT_EVIDENCE,
            confidence=0.0,
            warnings=[f"Order '{order_id}' was not found in the persistent order database."],
            created_at=datetime.now(timezone.utc),
        )

    # Extract primary item attributes
    item = order.items[0] if order.items else None
    expected_name = item.product_name if item else "Unknown"
    expected_brand = item.brand if item else "Unknown"
    expected_model = item.model if item else "Unknown"
    expected_colour = item.colour if item else "Unknown"
    expected_qty = item.expected_quantity if item else 1

    # Retrieve stored reference images
    ref_paths = get_reference_absolute_paths(db, order.order_id)
    if not ref_paths:
        return ProductMatchingResult(
            matching_id=matching_id,
            order_id=order_id,
            expected_product_name=expected_name,
            expected_brand=expected_brand,
            expected_model=expected_model,
            expected_colour=expected_colour,
            expected_quantity=expected_qty,
            overall_match_score=0.0,
            decision=ProductMatchDecision.INSUFFICIENT_EVIDENCE,
            confidence=0.0,
            warnings=[
                f"No reference images uploaded for order '{order_id}'. Upload 1-5 product reference images via POST /api/orders/{order_id}/references to enable visual verification."
            ],
            created_at=datetime.now(timezone.utc),
        )

    # 1. Select candidate frames from media
    analysis_tag = f"pm_{matching_id[:8]}"
    candidates = select_candidate_frames(media_path, media_type, analysis_tag)

    if not candidates:
        return ProductMatchingResult(
            matching_id=matching_id,
            order_id=order_id,
            expected_product_name=expected_name,
            expected_brand=expected_brand,
            expected_model=expected_model,
            expected_colour=expected_colour,
            expected_quantity=expected_qty,
            overall_match_score=0.0,
            decision=ProductMatchDecision.INSUFFICIENT_EVIDENCE,
            confidence=0.0,
            warnings=["No candidate frames could be extracted from uploaded media."],
            created_at=datetime.now(timezone.utc),
        )

    # 2. Extract OCR text across candidate frames if not provided
    detected_texts: List[str] = list(pre_extracted_ocr or [])
    if not pre_extracted_ocr:
        for cand in candidates:
            try:
                ocr_items = extract_text(cand.path)
                for item_dict in ocr_items:
                    if item_dict.get("text"):
                        detected_texts.append(item_dict["text"])
            except Exception:
                pass

    # Compute OCR text similarity
    ocr_score, matched_indicators = compute_text_similarity(
        detected_texts=detected_texts,
        expected_brand=expected_brand,
        expected_model=expected_model,
        expected_name=expected_name,
        expected_colour=expected_colour,
    )

    # 3. Compare each candidate frame against all reference images
    evaluated_frames: List[ProductCandidateFrame] = []
    best_frame: Optional[ProductCandidateFrame] = None
    best_frame_overall = -1.0
    best_ref_id: Optional[str] = None

    for cand in candidates:
        cand_best_phash = 0.0
        cand_best_colour = 0.0
        cand_best_orb = 0.0
        cand_best_semantic: Optional[float] = None
        cand_best_ref_id: Optional[str] = None
        cand_best_score = -1.0

        for ref_id, ref_p in ref_paths:
            phash_s = compute_perceptual_hash_similarity(cand.path, ref_p)
            colour_s = compute_colour_similarity(cand.path, ref_p)
            orb_s = compute_orb_feature_similarity(cand.path, ref_p)
            sem_s = compute_semantic_similarity(cand.path, ref_p)

            # Combined frame score against this reference
            comb = (
                settings.weight_ocr_text * ocr_score
                + settings.weight_orb_features * orb_s
                + settings.weight_perceptual_hash * phash_s
                + settings.weight_colour_similarity * colour_s
            )

            if comb > cand_best_score:
                cand_best_score = comb
                cand_best_phash = phash_s
                cand_best_colour = colour_s
                cand_best_orb = orb_s
                cand_best_semantic = sem_s
                cand_best_ref_id = ref_id

        frame_scores = ProductMatchingComponentScores(
            ocr_text_score=ocr_score,
            perceptual_hash_score=cand_best_phash,
            colour_similarity_score=cand_best_colour,
            feature_match_score=cand_best_orb,
            semantic_score=cand_best_semantic,
        )

        cand_frame_obj = ProductCandidateFrame(
            frame_index=cand.frame_index,
            timestamp_seconds=cand.timestamp_seconds,
            sharpness=cand.sharpness,
            frame_filename=cand.frame_name,
            best_reference_id=cand_best_ref_id,
            scores=frame_scores,
            overall_score=round(cand_best_score, 4),
        )
        evaluated_frames.append(cand_frame_obj)

        if cand_best_score > best_frame_overall:
            best_frame_overall = cand_best_score
            best_frame = cand_frame_obj
            best_ref_id = cand_best_ref_id

    # 4. Weight Calculation & Dynamic Normalization
    if best_frame is None:
        best_frame = evaluated_frames[0]
        best_ref_id = ref_paths[0][0]

    b_scores = best_frame.scores
    w_ocr = settings.weight_ocr_text
    w_orb = settings.weight_orb_features
    w_phash = settings.weight_perceptual_hash
    w_colour = settings.weight_colour_similarity

    # If OCR text is completely absent in visual capture, renormalize visual weights slightly
    # but still retain an OCR penalty if visual evidence is low
    if not detected_texts:
        # Renormalize visual weights: sum = 0.65 -> scaled to 1.0
        tot_vis = w_orb + w_phash + w_colour
        overall_score = (
            (w_orb / tot_vis) * b_scores.feature_match_score
            + (w_phash / tot_vis) * b_scores.perceptual_hash_score
            + (w_colour / tot_vis) * b_scores.colour_similarity_score
        )
    else:
        overall_score = (
            w_ocr * b_scores.ocr_text_score
            + w_orb * b_scores.feature_match_score
            + w_phash * b_scores.perceptual_hash_score
            + w_colour * b_scores.colour_similarity_score
        )

    overall_score = round(float(min(1.0, max(0.0, overall_score))), 4)

    # 5. Classify Decision
    warnings: List[str] = []
    if overall_score >= settings.threshold_match:
        decision = ProductMatchDecision.MATCH
        confidence = overall_score
    elif overall_score >= settings.threshold_likely_match:
        decision = ProductMatchDecision.LIKELY_MATCH
        confidence = overall_score
    else:
        decision = ProductMatchDecision.MISMATCH
        confidence = round(1.0 - overall_score, 4)
        warnings.append("Delivered product does not match stored reference images.")

    if not detected_texts:
        warnings.append("No readable product text or brand logo was recognized on the item.")

    result = ProductMatchingResult(
        matching_id=matching_id,
        order_id=order_id,
        expected_product_name=expected_name,
        expected_brand=expected_brand,
        expected_model=expected_model,
        expected_colour=expected_colour,
        expected_quantity=expected_qty,
        detected_product_text=list(set(detected_texts)),
        best_reference_id=best_ref_id,
        best_candidate_frame=best_frame.frame_filename,
        best_frame_timestamp_seconds=best_frame.timestamp_seconds,
        ocr_text_score=b_scores.ocr_text_score,
        perceptual_hash_score=b_scores.perceptual_hash_score,
        colour_similarity_score=b_scores.colour_similarity_score,
        feature_match_score=b_scores.feature_match_score,
        semantic_score=b_scores.semantic_score,
        overall_match_score=overall_score,
        decision=decision,
        confidence=confidence,
        warnings=warnings,
        supporting_frames=evaluated_frames,
        created_at=datetime.now(timezone.utc),
    )

    # Persist record in SQLite
    try:
        record = ProductMatchingRecord(
            id=matching_id,
            verification_session_id=verification_session_id,
            order_id=order.id,
            decision=decision.value,
            overall_score=overall_score,
            component_scores=b_scores.model_dump(),
            details=result.model_dump(mode="json"),
        )
        db.add(record)
        db.commit()
    except Exception:
        db.rollback()

    return result
