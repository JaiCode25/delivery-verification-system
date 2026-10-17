from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.database.models import QuantityVerificationRecord
from app.schemas.quantity_verification import QuantityVerificationResult
from app.services.evidence_service import analyze_upload, retrieve_analysis
from app.services.quantity_verification_service import evaluate_quantity

router = APIRouter(prefix="/api/quantity-verification", tags=["Quantity Verification"])


@router.post("/analyze", response_model=QuantityVerificationResult, status_code=201)
async def analyze_quantity(
    response: Response, order_id: str = Form(...), verification_session_id: str = Form(...),
    file: UploadFile = File(...), regenerate: bool = Form(False), db: Session = Depends(get_db),
):
    """Count supported visible objects. Regeneration replaces this session's result, retaining evidence."""
    result, created = await analyze_upload(db, order_id, verification_session_id, file, regenerate,
                                           QuantityVerificationRecord, evaluate_quantity)
    response.status_code = 201 if created else 200
    return result


@router.get("/{analysis_id}", response_model=QuantityVerificationResult)
def get_quantity(analysis_id: str, db: Session = Depends(get_db)):
    return retrieve_analysis(db, QuantityVerificationRecord, QuantityVerificationResult, analysis_id)
