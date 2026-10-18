"""Pydantic schemas for complaint assistant and email delivery."""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResolutionType(str, Enum):
    REPLACEMENT = "replacement"
    REFUND = "refund"
    MISSING_ITEM_REFUND = "missing_item_refund"
    OTHER = "other"


class ComplaintDeliveryStatus(str, Enum):
    DRAFT = "draft"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    DRY_RUN = "dry_run"


class ComplaintEligibilityResult(BaseModel):
    verification_session_id: str
    eligible: bool
    issue_types: List[str] = []
    supporting_analysis_ids: Dict[str, Optional[str]] = {}
    confidence_values: Dict[str, float] = {}
    reasons: List[str] = []
    warnings: List[str] = []
    threshold: float = 0.80


class ComplaintDraftCreate(BaseModel):
    verification_session_id: str = Field(..., min_length=1, description="Verification session ID with completed analysis")
    submission_id: Optional[str] = Field(None, description="Optional customer product submission ID")
    requested_resolution: ResolutionType = Field(default=ResolutionType.REPLACEMENT, description="Requested remedy from retailer")
    custom_notes: Optional[str] = Field(None, max_length=2000, description="Optional custom notes from customer")
    recipient_email: Optional[str] = Field(None, max_length=200, description="Explicit recipient support email (if known)")
    regenerate: bool = Field(default=False, description="Whether to regenerate an existing draft for this session")
    allow_manual_inquiry: bool = Field(default=False, description="Explicitly allow generating a manual review inquiry when automated evidence is inconclusive or not eligible")

    @field_validator("recipient_email")
    @classmethod
    def validate_recipient_email(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if v and ("@" not in v or "." not in v.split("@", 1)[1]):
                raise ValueError("recipient_email must be a valid email address")
        return v or None


class ComplaintDraftUpdate(BaseModel):
    subject: Optional[str] = Field(None, min_length=1, max_length=255)
    body_text: Optional[str] = Field(None, min_length=1)
    body_html: Optional[str] = Field(None, min_length=1)
    recipient_email: Optional[str] = Field(None, max_length=200)
    requested_resolution: Optional[ResolutionType] = None

    @field_validator("recipient_email")
    @classmethod
    def validate_recipient_email(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if v and ("@" not in v or "." not in v.split("@", 1)[1]):
                raise ValueError("recipient_email must be a valid email address")
        return v or None


class ComplaintDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    draft_id: str
    id: str
    submission_id: Optional[str] = None
    verification_session_id: Optional[str] = None
    order_id: Optional[int] = None
    report_id: Optional[str] = None
    subject: str
    body_text: str
    body_html: str
    recipient_email: str
    requested_resolution: str
    issue_types: List[str] = []
    confidence_summary: Dict[str, float] = {}
    attached_evidence_filenames: List[str] = []
    disclaimers: List[str] = []
    delivery_status: str
    delivery_attempts: List[Dict[str, Any]] = []
    sent_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    eligible: bool = False
    manual_inquiry: bool = False
    warnings: List[str] = []


class ComplaintPreviewResponse(BaseModel):
    draft_id: str
    subject: str
    body_text: str
    body_html: str
    recipient_email: str
    requested_resolution: str
    attached_evidence_filenames: List[str] = []
    disclaimers: List[str] = []
    delivery_status: str
    eligible: bool = False
    manual_inquiry: bool = False
    warnings: List[str] = []
    issue_types: List[str] = []


class ComplaintSendRequest(BaseModel):
    confirmed: bool = Field(..., description="Explicit user confirmation to send the email (must be true)")
    idempotency_key: Optional[str] = Field(None, max_length=100, description="Optional idempotency key to prevent double sends")


class ComplaintSendResponse(BaseModel):
    draft_id: str
    delivery_status: str
    recipient_email: str
    message: str
    delivery_attempt: Dict[str, Any] = {}
