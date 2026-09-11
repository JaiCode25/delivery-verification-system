from datetime import datetime, timezone
from typing import List, Optional
import logging

from sqlalchemy.orm import Session

from app.schemas.enums import OrderStatus, Platform, VerificationStatus
from app.schemas.verification import BarcodeVerificationRequest, OrderVerificationResult
from app.services.order_service import (
    build_order_summary,
    compare_order_details,
    find_order_by_barcode,
    has_finalized_claim,
    store_verification_session,
)

logger = logging.getLogger("verification_engine")


def verify_barcode(
    db: Session,
    request: BarcodeVerificationRequest,
) -> OrderVerificationResult:
    """Evaluate barcode data against persistent database records and claim history.

    Implements the 6 standardized verification states:
    1. unreadable: No code provided or decoded.
    2. order_not_found: Code not present in order database.
    3. details_mismatch: Order found but user claims conflict with order record.
    4. already_used: Order was already used in a finalized claim.
    5. verified: Order found and claims align without conflicts.
    6. manual_review_required: Incomplete, cancelled, or ambiguous situation.
    """
    now = datetime.now(timezone.utc)
    barcode_value = request.barcode_value.strip() if request.barcode_value else None
    barcode_type = request.barcode_type.strip() if request.barcode_type else None

    # 1. Unreadable condition
    if not barcode_value:
        logger.info("Verification outcome: UNREADABLE (no barcode value provided)")
        mismatches = ["No readable barcode or QR code identifier was detected in the submitted evidence."]
        session = store_verification_session(
            db=db,
            verification_status=VerificationStatus.UNREADABLE,
            order_id=None,
            decoded_code=None,
            barcode_type=barcode_type,
            mismatch_reasons=mismatches,
            evidence_frame=request.evidence_frame,
            video_timestamp_seconds=request.video_timestamp_seconds,
        )
        return OrderVerificationResult(
            verification_status=VerificationStatus.UNREADABLE,
            matched_order=None,
            mismatch_reasons=mismatches,
            decoded_value=None,
            barcode_type=barcode_type,
            evidence_filename=request.evidence_frame,
            video_timestamp_seconds=request.video_timestamp_seconds,
            verification_session_id=session.id,
            created_at=now,
        )

    # 2. Search for order in database
    order = find_order_by_barcode(db, barcode_value)
    if order is None:
        logger.info(f"Verification outcome: ORDER_NOT_FOUND for barcode {barcode_value[:4]}***")
        mismatches = [f"Barcode identifier '{barcode_value}' does not match any registered order in the system."]
        session = store_verification_session(
            db=db,
            verification_status=VerificationStatus.ORDER_NOT_FOUND,
            order_id=None,
            decoded_code=barcode_value,
            barcode_type=barcode_type,
            mismatch_reasons=mismatches,
            evidence_frame=request.evidence_frame,
            video_timestamp_seconds=request.video_timestamp_seconds,
        )
        return OrderVerificationResult(
            verification_status=VerificationStatus.ORDER_NOT_FOUND,
            matched_order=None,
            mismatch_reasons=mismatches,
            decoded_value=barcode_value,
            barcode_type=barcode_type,
            evidence_filename=request.evidence_frame,
            video_timestamp_seconds=request.video_timestamp_seconds,
            verification_session_id=session.id,
            created_at=now,
        )

    # 3. Check if this order was already used in a finalized claim
    if has_finalized_claim(db, order.id):
        logger.warning(f"Verification outcome: ALREADY_USED for order {order.order_id}")
        mismatches = [f"A previous complaint or claim for order '{order.order_id}' has already been finalized."]
        session = store_verification_session(
            db=db,
            verification_status=VerificationStatus.ALREADY_USED,
            order_id=order.id,
            decoded_code=barcode_value,
            barcode_type=barcode_type,
            mismatch_reasons=mismatches,
            evidence_frame=request.evidence_frame,
            video_timestamp_seconds=request.video_timestamp_seconds,
        )
        return OrderVerificationResult(
            verification_status=VerificationStatus.ALREADY_USED,
            matched_order=build_order_summary(order),
            mismatch_reasons=mismatches,
            decoded_value=barcode_value,
            barcode_type=barcode_type,
            evidence_filename=request.evidence_frame,
            video_timestamp_seconds=request.video_timestamp_seconds,
            verification_session_id=session.id,
            created_at=now,
        )

    # 4. Compare claimed details if supplied
    mismatches = compare_order_details(
        order=order,
        expected_platform=request.expected_platform,
        expected_order_id=request.expected_order_id,
        expected_customer_name=request.expected_customer_name,
        expected_product_name=request.expected_product_name,
    )

    if mismatches:
        logger.info(f"Verification outcome: DETAILS_MISMATCH for order {order.order_id}: {mismatches}")
        session = store_verification_session(
            db=db,
            verification_status=VerificationStatus.DETAILS_MISMATCH,
            order_id=order.id,
            decoded_code=barcode_value,
            barcode_type=barcode_type,
            mismatch_reasons=mismatches,
            evidence_frame=request.evidence_frame,
            video_timestamp_seconds=request.video_timestamp_seconds,
        )
        return OrderVerificationResult(
            verification_status=VerificationStatus.DETAILS_MISMATCH,
            matched_order=build_order_summary(order),
            mismatch_reasons=mismatches,
            decoded_value=barcode_value,
            barcode_type=barcode_type,
            evidence_filename=request.evidence_frame,
            video_timestamp_seconds=request.video_timestamp_seconds,
            verification_session_id=session.id,
            created_at=now,
        )

    # 5. Check order status anomalies (e.g. cancelled)
    if order.order_status == OrderStatus.CANCELLED.value:
        logger.info(f"Verification outcome: MANUAL_REVIEW_REQUIRED for cancelled order {order.order_id}")
        reasons = [f"Order '{order.order_id}' is recorded as cancelled, requiring manual review."]
        session = store_verification_session(
            db=db,
            verification_status=VerificationStatus.MANUAL_REVIEW_REQUIRED,
            order_id=order.id,
            decoded_code=barcode_value,
            barcode_type=barcode_type,
            mismatch_reasons=reasons,
            evidence_frame=request.evidence_frame,
            video_timestamp_seconds=request.video_timestamp_seconds,
        )
        return OrderVerificationResult(
            verification_status=VerificationStatus.MANUAL_REVIEW_REQUIRED,
            matched_order=build_order_summary(order),
            mismatch_reasons=reasons,
            decoded_value=barcode_value,
            barcode_type=barcode_type,
            evidence_filename=request.evidence_frame,
            video_timestamp_seconds=request.video_timestamp_seconds,
            verification_session_id=session.id,
            created_at=now,
        )

    # 6. Verified
    logger.info(f"Verification outcome: VERIFIED for order {order.order_id}")
    session = store_verification_session(
        db=db,
        verification_status=VerificationStatus.VERIFIED,
        order_id=order.id,
        decoded_code=barcode_value,
        barcode_type=barcode_type,
        mismatch_reasons=[],
        evidence_frame=request.evidence_frame,
        video_timestamp_seconds=request.video_timestamp_seconds,
    )
    return OrderVerificationResult(
        verification_status=VerificationStatus.VERIFIED,
        matched_order=build_order_summary(order),
        mismatch_reasons=[],
        decoded_value=barcode_value,
        barcode_type=barcode_type,
        evidence_filename=request.evidence_frame,
        video_timestamp_seconds=request.video_timestamp_seconds,
        verification_session_id=session.id,
        created_at=now,
    )


def verify_detected_codes(
    db: Session,
    detected_codes: List[dict],
    expected_platform: Optional[str] = None,
    expected_order_id: Optional[str] = None,
    expected_customer_name: Optional[str] = None,
    expected_product_name: Optional[str] = None,
) -> OrderVerificationResult:
    """Helper to verify a collection of barcodes detected from media analysis."""
    if not detected_codes:
        req = BarcodeVerificationRequest(
            expected_platform=expected_platform,
            expected_order_id=expected_order_id,
            expected_customer_name=expected_customer_name,
            expected_product_name=expected_product_name,
        )
        return verify_barcode(db, req)

    # First, test if any detected code matches an order in the database
    matched_candidate = None
    for item in detected_codes:
        code_val = item.get("text", "").strip()
        if code_val and find_order_by_barcode(db, code_val):
            matched_candidate = item
            break

    # If an order was found for one of the codes, verify with that code
    candidate = matched_candidate or detected_codes[0]

    req = BarcodeVerificationRequest(
        barcode_value=candidate.get("text"),
        barcode_type=candidate.get("type"),
        expected_platform=expected_platform,
        expected_order_id=expected_order_id,
        expected_customer_name=expected_customer_name,
        expected_product_name=expected_product_name,
        evidence_frame=candidate.get("first_seen_in"),
        video_timestamp_seconds=candidate.get("video_timestamp_seconds"),
    )
    return verify_barcode(db, req)
