from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.report import VerificationReportRead
from app.services import report_service

router = APIRouter(prefix="/api/reports", tags=["Verification Reports"])


@router.post("/generate/{session_id}", response_model=VerificationReportRead, status_code=status.HTTP_201_CREATED)
def generate_report(
    session_id: str,
    db: Session = Depends(get_db),
):
    """Generate a structured verification report and responsive HTML document for a verification session."""
    try:
        return report_service.generate_verification_report(db=db, session_id=session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Report generation failed: {exc}",
        ) from exc


@router.get("/{report_id}", response_model=VerificationReportRead)
def get_report_json(
    report_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve structured JSON verification report by report ID."""
    report = report_service.get_report_by_id(db=db, report_id=report_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification report '{report_id}' not found.",
        )
    return report


@router.get("/{report_id}/html", response_class=HTMLResponse)
def get_report_html(
    report_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve standalone, mobile-responsive HTML report by report ID."""
    html_content = report_service.get_report_html_by_id(db=db, report_id=report_id)
    if not html_content:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HTML report for '{report_id}' not found.",
        )
    return HTMLResponse(content=html_content, status_code=status.HTTP_200_OK)
