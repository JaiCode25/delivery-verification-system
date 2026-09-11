from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.verification import (
    BarcodeVerificationRequest,
    OrderVerificationResult,
    VerificationSessionRead,
)
from app.services import order_service, verification_engine

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
