"""FastAPI router for complaint assistant and email dispatch."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.complaint import (
    ComplaintDraftCreate,
    ComplaintDraftRead,
    ComplaintDraftUpdate,
    ComplaintEligibilityResult,
    ComplaintPreviewResponse,
    ComplaintSendRequest,
    ComplaintSendResponse,
)
from app.services import complaint_service

router = APIRouter(prefix="/api/complaints", tags=["Complaint Assistant"])


class CheckEligibilityRequest(BaseModel):
    verification_session_id: str
    threshold: Optional[float] = None


@router.get("/eligibility/{verification_session_id}", response_model=ComplaintEligibilityResult)
def get_complaint_eligibility(
    verification_session_id: str,
    threshold: Optional[float] = Query(None, ge=0.0, le=1.0, description="Override high-confidence threshold"),
    db: Session = Depends(get_db),
):
    """Check whether a verification session qualifies for automated complaint recommendation."""
    return complaint_service.check_complaint_eligibility(db, verification_session_id, threshold=threshold)


@router.post("/check-eligibility", response_model=ComplaintEligibilityResult)
def check_complaint_eligibility_post(
    payload: CheckEligibilityRequest,
    db: Session = Depends(get_db),
):
    """Post request to check complaint eligibility."""
    return complaint_service.check_complaint_eligibility(
        db, payload.verification_session_id, threshold=payload.threshold
    )


@router.post("/drafts", response_model=ComplaintDraftRead, status_code=status.HTTP_201_CREATED)
def create_or_regenerate_draft(
    payload: ComplaintDraftCreate,
    db: Session = Depends(get_db),
):
    """Generate or regenerate an objective, professional complaint draft for a verification session."""
    draft = complaint_service.generate_complaint_draft(db, payload)
    read_data = ComplaintDraftRead.model_validate(draft)
    read_data.draft_id = draft.id
    return read_data


@router.get("/drafts", response_model=list[ComplaintDraftRead])
def list_complaint_drafts(
    verification_session_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """List complaint drafts, optionally filtered by verification session."""
    query = db.query(complaint_service.ComplaintDraft)
    if verification_session_id:
        query = query.filter(complaint_service.ComplaintDraft.verification_session_id == verification_session_id)
    drafts = query.order_by(complaint_service.ComplaintDraft.created_at.desc()).all()
    results = []
    for d in drafts:
        rd = ComplaintDraftRead.model_validate(d)
        rd.draft_id = d.id
        results.append(rd)
    return results


@router.get("/drafts/{draft_id}", response_model=ComplaintDraftRead)
def get_complaint_draft(
    draft_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve saved complaint draft by ID."""
    draft = complaint_service.get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Complaint draft '{draft_id}' not found.")
    read_data = ComplaintDraftRead.model_validate(draft)
    read_data.draft_id = draft.id
    return read_data


@router.patch("/drafts/{draft_id}", response_model=ComplaintDraftRead)
def update_complaint_draft(
    draft_id: str,
    payload: ComplaintDraftUpdate,
    db: Session = Depends(get_db),
):
    """Edit allowed fields of a complaint draft (subject, body, recipient, requested resolution)."""
    draft = complaint_service.update_draft(db, draft_id, payload)
    read_data = ComplaintDraftRead.model_validate(draft)
    read_data.draft_id = draft.id
    return read_data


@router.get("/drafts/{draft_id}/preview", response_model=ComplaintPreviewResponse)
def preview_complaint_draft(
    draft_id: str,
    db: Session = Depends(get_db),
):
    """Preview plain-text and HTML rendered complaint draft before sending."""
    draft = complaint_service.get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Complaint draft '{draft_id}' not found.")
    return ComplaintPreviewResponse(
        draft_id=draft.id,
        subject=draft.subject,
        body_text=draft.body_text,
        body_html=draft.body_html,
        recipient_email=draft.recipient_email,
        requested_resolution=draft.requested_resolution,
        attached_evidence_filenames=draft.attached_evidence_filenames or [],
        disclaimers=draft.disclaimers or [],
        delivery_status=draft.delivery_status,
        eligible=draft.eligible,
        manual_inquiry=draft.manual_inquiry,
        warnings=draft.warnings or [],
        issue_types=draft.issue_types or [],
        intended_platform=draft.intended_platform,
        destination_confirmed=draft.destination_confirmed,
    )


@router.post("/drafts/{draft_id}/send", response_model=ComplaintSendResponse)
async def send_complaint_draft(
    draft_id: str,
    payload: ComplaintSendRequest,
    db: Session = Depends(get_db),
):
    """Transmit complaint email. Requires confirmed=true in the request body."""
    return await complaint_service.send_complaint(
        db, draft_id, confirmed=payload.confirmed, idempotency_key=payload.idempotency_key
    )
