from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.database.models import ConditionAnalysisRecord
from app.schemas.condition_analysis import ConditionAnalysisResult, EvidenceType
from app.services.evidence_service import analyze_upload, retrieve_analysis
from app.services.condition_analysis_service import evaluate_condition

router = APIRouter(prefix="/api/condition-analysis", tags=["Visible Condition"])


@router.post("/analyze", response_model=ConditionAnalysisResult, status_code=201)
async def analyze_condition(
    response: Response, order_id: str = Form(...), verification_session_id: str = Form(...),
    file: UploadFile = File(...), evidence_type: EvidenceType = Form(EvidenceType.UNKNOWN),
    regenerate: bool = Form(False), db: Session = Depends(get_db),
):
    """Assess visible external condition; uncertainty is retained for human review."""
    result, created = await analyze_upload(db, order_id, verification_session_id, file, regenerate,
                                           ConditionAnalysisRecord, evaluate_condition, evidence_type)
    response.status_code = 201 if created else 200
    return result


@router.get("/{analysis_id}", response_model=ConditionAnalysisResult)
def get_condition(analysis_id: str, db: Session = Depends(get_db)):
    return retrieve_analysis(db, ConditionAnalysisRecord, ConditionAnalysisResult, analysis_id)
