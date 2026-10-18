from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import OverallVerificationDecision
from app.schemas.label import LabelAnalysisResult, LabelSummary
from app.schemas.product_matching import ProductMatchingResult
from app.schemas.verification import OrderVerificationResult
from app.schemas.quantity_verification import QuantityVerificationResult
from app.schemas.condition_analysis import ConditionAnalysisResult


class VerificationReportSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    report_id: str
    verification_session_id: Optional[str] = None
    order_id: Optional[str] = None
    final_decision: OverallVerificationDecision
    created_at: datetime
    human_review_recommendation: str


class VerificationReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    report_id: str
    verification_session_id: Optional[str] = None
    analysis_id: Optional[str] = None
    created_at: datetime
    original_filename: Optional[str] = None
    input_type: Optional[str] = None

    # Masked Customer & Order Metadata (No raw unmasked PII)
    order_id: Optional[str] = None
    platform: Optional[str] = None
    tracking_id: Optional[str] = None
    masked_customer_name: Optional[str] = None
    masked_customer_email: Optional[str] = None
    masked_delivery_address: Optional[str] = None

    # Core Verification Signals
    barcode_verification: Optional[Dict[str, Any]] = None
    label_summary: Optional[LabelSummary] = None
    label_analysis: Optional[Dict[str, Any]] = None
    product_matching: Optional[ProductMatchingResult] = None
    quantity_verification: Optional[QuantityVerificationResult] = None
    condition_analysis: Optional[ConditionAnalysisResult] = None
    channel_statuses: Dict[str, str] = Field(default_factory=dict)

    # Customer Intake & Reference Information
    customer_product_name: Optional[str] = None
    product_url: Optional[str] = None
    reference_source: Optional[str] = None

    # Complaint Assistant Information
    complaint_eligible: Optional[bool] = None
    complaint_reasons: List[str] = []
    complaint_draft_status: Optional[str] = None
    email_delivery_status: Optional[str] = None

    # Overall Synthesis
    final_decision: OverallVerificationDecision
    decision_explanation: str
    warnings: List[str] = []
    evidence_filenames: List[str] = []
    relevant_timestamps_seconds: List[float] = []
    human_review_recommendation: str
    json_report_url: Optional[str] = None
    html_report_url: Optional[str] = None
