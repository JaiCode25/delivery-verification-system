from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.label import LabelAnalysisResult, LabelVerificationRequest
from app.schemas.verification import (
    BarcodeVerificationRequest,
    OrderVerificationResult,
    VerificationSessionRead,
)
from app.services import label_verification_service, order_service, verification_engine
from app.services.label_parser import parse_shipping_label

router = APIRouter(prefix="/api/verification", tags=["Verification"])


@router.post("/barcode", response_model=OrderVerificationResult)
def verify_barcode_data(
    request: BarcodeVerificationRequest,
    db: Session = Depends(get_db),
):
    """Verify decoded barcode against persistent SQLite orders and prior claim history."""
    return verification_engine.verify_barcode(db=db, request=request)


@router.post("/claim/finalize/{session_id}", response_model=VerificationSessionRead)
def finalize_verification_claim(
    session_id: str,
    db: Session = Depends(get_db),
):
    """Simulate finalization of a complaint/claim for an order.

    Subsequent verification attempts for this order will be flagged as ALREADY_USED.
    """
    session = order_service.finalize_claim(db=db, session_id=session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification session '{session_id}' not found.",
        )
    return session


@router.post("/label", response_model=LabelAnalysisResult)
def verify_label_data(
    request: LabelVerificationRequest,
    db: Session = Depends(get_db),
):
    """Verify structured shipping-label OCR detections against persistent SQLite orders."""
    dets = [d.model_dump() for d in request.ocr_detections]
    parsed_label = parse_shipping_label(dets)

    matched_order = None
    if request.expected_tracking_id:
        matched_order = order_service.find_order_by_tracking_id(db, request.expected_tracking_id)
    if not matched_order and request.expected_order_id:
        matched_order = order_service.get_order_by_order_id(db, request.expected_order_id)

    return label_verification_service.cross_check_barcode_and_label(
        db=db,
        parsed_label=parsed_label,
        matched_order=matched_order,
        expected_platform=request.expected_platform.value if request.expected_platform else None,
        expected_order_id=request.expected_order_id,
        expected_customer_name=request.expected_customer_name,
        expected_product_name=request.expected_product_name,
    )
