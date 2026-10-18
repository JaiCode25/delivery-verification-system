"""FastAPI router for customer-facing product intake."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.customer_submission import (
    CustomerSubmissionCreate,
    CustomerSubmissionRead,
    CustomerSubmissionUpdate,
    LinkSessionRequest,
    ReferenceResolutionResult,
)
from app.services import customer_intake_service as intake_service

router = APIRouter(prefix="/api/customer/submissions", tags=["Customer Product Intake"])


@router.post("", response_model=CustomerSubmissionRead, status_code=status.HTTP_201_CREATED)
def create_customer_submission(
    payload: CustomerSubmissionCreate,
    db: Session = Depends(get_db),
):
    """Submit customer-entered product details without asking for any retailer-internal order or tracking ID."""
    submission = intake_service.create_submission(db, payload)
    read_data = CustomerSubmissionRead.model_validate(submission)
    read_data.submission_id = submission.id
    if submission.resolution_metadata:
        read_data.resolved_reference = ReferenceResolutionResult.model_validate(submission.resolution_metadata)
    return read_data


@router.get("/{submission_id}", response_model=CustomerSubmissionRead)
def get_customer_submission(
    submission_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve an existing customer product submission by server-generated submission ID."""
    submission = intake_service.get_submission(db, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail=f"Submission '{submission_id}' not found.")
    read_data = CustomerSubmissionRead.model_validate(submission)
    read_data.submission_id = submission.id
    if submission.resolution_metadata:
        read_data.resolved_reference = ReferenceResolutionResult.model_validate(submission.resolution_metadata)
    return read_data


@router.patch("/{submission_id}", response_model=CustomerSubmissionRead)
def update_customer_submission(
    submission_id: str,
    payload: CustomerSubmissionUpdate,
    db: Session = Depends(get_db),
):
    """Update editable customer-entered fields (product name, URL, quantity, notes, contact)."""
    submission = intake_service.update_submission(db, submission_id, payload)
    read_data = CustomerSubmissionRead.model_validate(submission)
    read_data.submission_id = submission.id
    if submission.resolution_metadata:
        read_data.resolved_reference = ReferenceResolutionResult.model_validate(submission.resolution_metadata)
    return read_data


@router.post("/{submission_id}/link-session", response_model=CustomerSubmissionRead)
def link_submission_to_verification_session(
    submission_id: str,
    payload: LinkSessionRequest,
    db: Session = Depends(get_db),
):
    """Connect product submission to a verification session obtained after QR/label scan.

    Preserves both customer-entered and order-verified data, reporting any contradictions.
    """
    submission = intake_service.link_submission_to_session(
        db, submission_id, payload.verification_session_id
    )
    read_data = CustomerSubmissionRead.model_validate(submission)
    read_data.submission_id = submission.id
    if submission.resolution_metadata:
        read_data.resolved_reference = ReferenceResolutionResult.model_validate(submission.resolution_metadata)
    return read_data


@router.get("/{submission_id}/resolved-reference", response_model=ReferenceResolutionResult)
def get_resolved_reference(
    submission_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve demo-catalogue reference resolution status for the submitted product."""
    submission = intake_service.get_submission(db, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail=f"Submission '{submission_id}' not found.")
    if submission.resolution_metadata:
        return ReferenceResolutionResult.model_validate(submission.resolution_metadata)
    return ReferenceResolutionResult(
        reference_id=None,
        reference_url=None,
        reference_source="reference_unavailable",
        match_score=0.0,
        match_reason="No matching product found in demo catalogue",
    )
