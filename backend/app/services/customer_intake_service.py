"""Customer product intake service.

Handles customer product submission, URL normalization, demo-catalogue reference matching,
and linking to verification sessions with non-destructive contradiction detection.
"""
from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database.models import CustomerProductSubmission, Order, OrderItem, ProductReference, VerificationSession
from app.schemas.customer_submission import (
    ContradictionItem,
    CustomerSubmissionCreate,
    CustomerSubmissionUpdate,
    ReferenceResolutionResult,
)
from app.services.metadata_extractor import get_metadata_extractor


def normalize_product_url(url: str) -> str:
    """Normalize HTTPS product URL by lowercasing scheme/host, sorting query, and stripping tracking parameters."""
    if not url:
        return ""
    url = url.strip()
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Strip standard tracking query parameters
    tracking_params = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "ref", "tag", "qid", "sr", "keywords", "pf_rd_r", "pf_rd_p", "pd_rd_r",
        "pd_rd_w", "pd_rd_wg", "linkcode", "creative", "creativeasin",
    }
    filtered_query = []
    if parsed.query:
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            if k.lower() not in tracking_params and not k.lower().startswith("utm_"):
                filtered_query.append((k, v))
        filtered_query.sort()

    new_query = urlencode(filtered_query)
    # Remove fragment
    return urlunsplit((scheme, netloc, parsed.path.rstrip("/") or "/", new_query, ""))


def _tokenize(text: Optional[str]) -> set[str]:
    if not text:
        return set()
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return {w for w in cleaned.split() if len(w) > 1}


def resolve_demo_catalogue_reference(
    db: Session,
    platform: str,
    product_name: str,
    colour_or_variant: Optional[str] = None,
) -> ReferenceResolutionResult:
    """Deterministically match customer submission against local demo catalogue references.

    Clearly marks resolved assets with reference_source='demo_catalogue'.
    Never claims that an image came from the customer-supplied URL.
    """
    sub_tokens = _tokenize(product_name)
    sub_platform = platform.strip().lower()
    sub_variant_tokens = _tokenize(colour_or_variant)

    candidates = (
        db.query(OrderItem, Order, ProductReference)
        .join(Order, OrderItem.order_id == Order.id)
        .join(ProductReference, ProductReference.order_item_id == OrderItem.id)
        .all()
    )

    best_match: Optional[tuple[OrderItem, Order, ProductReference, float, str]] = None
    best_score = 0.0

    for item, order, ref in candidates:
        score = 0.0
        reasons = []

        # 1. Platform match
        order_platform = (order.platform or "").strip().lower()
        if order_platform == sub_platform:
            score += 0.25
            reasons.append(f"Matching platform '{order_platform}'")

        # 2. Product name & model token overlap
        item_tokens = _tokenize(item.product_name) | _tokenize(item.brand) | _tokenize(item.model)
        if sub_tokens and item_tokens:
            overlap = sub_tokens.intersection(item_tokens)
            overlap_ratio = len(overlap) / max(len(sub_tokens), 1)
            score += 0.55 * overlap_ratio
            if overlap:
                reasons.append(f"Matched terms: {', '.join(sorted(overlap))}")

        # 3. Colour/variant match
        if sub_variant_tokens:
            item_colour_tokens = _tokenize(item.colour) | _tokenize(item.model)
            if sub_variant_tokens.intersection(item_colour_tokens):
                score += 0.20
                reasons.append("Matched variant/colour")

        score = round(min(1.0, score), 4)
        if score > best_score:
            best_score = score
            best_match = (item, order, ref, score, "; ".join(reasons))

    if best_match and best_score >= 0.40:
        item, order, ref, score, reason = best_match
        return ReferenceResolutionResult(
            reference_id=ref.id,
            reference_url=f"/api/references/{ref.id}" if hasattr(ref, "id") else None,
            reference_source="demo_catalogue",
            match_score=best_score,
            match_reason=f"Matched demo catalogue entry for '{item.product_name}' ({reason})",
            matched_product_name=item.product_name,
            matched_brand=item.brand,
            matched_model=item.model,
        )

    return ReferenceResolutionResult(
        reference_id=None,
        reference_url=None,
        reference_source="reference_unavailable",
        match_score=0.0,
        match_reason="No matching product reference found in demo catalogue",
    )


def create_submission(
    db: Session,
    payload: CustomerSubmissionCreate,
) -> CustomerProductSubmission:
    """Create a new customer product submission with normalized URL and resolved demo catalogue reference."""
    submission_id = uuid4().hex
    norm_url = normalize_product_url(payload.product_url)

    # Deterministic demo catalogue matching
    ref_res = resolve_demo_catalogue_reference(
        db,
        platform=payload.platform,
        product_name=payload.product_name,
        colour_or_variant=payload.colour_or_variant,
    )

    submission = CustomerProductSubmission(
        id=submission_id,
        product_name=payload.product_name.strip(),
        platform=payload.platform.strip().lower(),
        product_url=payload.product_url.strip(),
        normalized_product_url=norm_url,
        expected_quantity=payload.expected_quantity,
        colour_or_variant=payload.colour_or_variant.strip() if payload.colour_or_variant else None,
        customer_name=payload.customer_name.strip() if payload.customer_name else None,
        customer_email=payload.customer_email.strip().lower() if payload.customer_email else None,
        notes=payload.notes.strip() if payload.notes else None,
        resolved_reference_id=ref_res.reference_id,
        resolution_metadata=ref_res.model_dump(),
        contradictions=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


def get_submission(db: Session, submission_id: str) -> Optional[CustomerProductSubmission]:
    """Retrieve customer product submission by ID."""
    return db.query(CustomerProductSubmission).filter(CustomerProductSubmission.id == submission_id).first()


def update_submission(
    db: Session,
    submission_id: str,
    payload: CustomerSubmissionUpdate,
) -> CustomerProductSubmission:
    """Update editable customer-entered submission fields."""
    sub = get_submission(db, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail=f"Product submission '{submission_id}' not found.")

    re_resolve = False
    if payload.product_name is not None:
        sub.product_name = payload.product_name.strip()
        re_resolve = True
    if payload.platform is not None:
        sub.platform = payload.platform.strip().lower()
        re_resolve = True
    if payload.product_url is not None:
        sub.product_url = payload.product_url.strip()
        sub.normalized_product_url = normalize_product_url(payload.product_url)
    if payload.expected_quantity is not None:
        sub.expected_quantity = payload.expected_quantity
    if payload.colour_or_variant is not None:
        sub.colour_or_variant = payload.colour_or_variant.strip() or None
        re_resolve = True
    if payload.customer_name is not None:
        sub.customer_name = payload.customer_name.strip() or None
    if payload.customer_email is not None:
        sub.customer_email = payload.customer_email.strip().lower() or None
    if payload.notes is not None:
        sub.notes = payload.notes.strip() or None

    if re_resolve:
        ref_res = resolve_demo_catalogue_reference(
            db,
            platform=sub.platform,
            product_name=sub.product_name,
            colour_or_variant=sub.colour_or_variant,
        )
        sub.resolved_reference_id = ref_res.reference_id
        sub.resolution_metadata = ref_res.model_dump()

    sub.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(sub)
    return sub


def link_submission_to_session(
    db: Session,
    submission_id: str,
    verification_session_id: str,
) -> CustomerProductSubmission:
    """Link customer submission to a verified session, preserving both inputs and recording contradictions."""
    sub = get_submission(db, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail=f"Product submission '{submission_id}' not found.")

    v_session = db.query(VerificationSession).filter(VerificationSession.id == verification_session_id).first()
    if not v_session:
        raise HTTPException(status_code=404, detail=f"Verification session '{verification_session_id}' not found.")

    order: Optional[Order] = None
    if v_session.order_id:
        order = db.query(Order).filter(Order.id == v_session.order_id).first()

    contradictions: List[Dict[str, Any]] = []

    if order:
        # 1. Platform contradiction
        if sub.platform and order.platform:
            if sub.platform.casefold() != order.platform.casefold():
                contradictions.append({
                    "field": "platform",
                    "customer_value": sub.platform,
                    "verified_order_value": order.platform,
                    "reason": f"Customer specified platform '{sub.platform}', but verified order indicates '{order.platform}'.",
                })

        # 2. Expected Quantity contradiction
        if order.items and sub.expected_quantity:
            order_expected_qty = sum(it.expected_quantity for it in order.items if it.expected_quantity is not None)
            if order_expected_qty > 0 and sub.expected_quantity != order_expected_qty:
                contradictions.append({
                    "field": "expected_quantity",
                    "customer_value": sub.expected_quantity,
                    "verified_order_value": order_expected_qty,
                    "reason": f"Customer expected quantity ({sub.expected_quantity}) differs from retailer order quantity ({order_expected_qty}).",
                })

        # 3. Product Name contradiction
        if order.items and sub.product_name:
            order_product_name = order.items[0].product_name
            sub_toks = _tokenize(sub.product_name)
            order_toks = _tokenize(order_product_name)
            if sub_toks and order_toks and not sub_toks.intersection(order_toks):
                contradictions.append({
                    "field": "product_name",
                    "customer_value": sub.product_name,
                    "verified_order_value": order_product_name,
                    "reason": f"Customer product name '{sub.product_name}' has no common terms with verified order item '{order_product_name}'.",
                })

        # 4. Customer Name contradiction
        if sub.customer_name and order.customer_name:
            sub_name_toks = _tokenize(sub.customer_name)
            order_name_toks = _tokenize(order.customer_name)
            if sub_name_toks and order_name_toks and not sub_name_toks.intersection(order_name_toks):
                contradictions.append({
                    "field": "customer_name",
                    "customer_value": sub.customer_name,
                    "verified_order_value": order.customer_name,
                    "reason": "Customer entered name differs from retailer registered customer name.",
                })

        # 5. Customer Email contradiction
        if sub.customer_email and order.customer_email:
            if sub.customer_email.casefold() != order.customer_email.casefold():
                contradictions.append({
                    "field": "customer_email",
                    "customer_value": sub.customer_email,
                    "verified_order_value": order.customer_email,
                    "reason": "Customer email differs from verified order email.",
                })

    sub.verification_session_id = verification_session_id
    if order:
        sub.order_id = order.id
    sub.contradictions = contradictions
    sub.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(sub)
    return sub
