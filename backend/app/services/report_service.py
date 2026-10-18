from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from uuid import uuid4

from jinja2 import Template
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import REPORT_DIR, STORAGE_DIR
from app.database.models import (
    Order,
    ProductMatchingRecord,
    VerificationReport,
    VerificationSession,
    QuantityVerificationRecord,
    ConditionAnalysisRecord,
    CustomerProductSubmission,
    ComplaintDraft,
)
from app.schemas.enums import (
    LabelVerificationStatus,
    OverallVerificationDecision,
    ProductMatchDecision,
    VerificationStatus,
)
from app.schemas.label import LabelSummary
from app.schemas.product_matching import ProductMatchingResult
from app.schemas.report import VerificationReportRead
from app.schemas.quantity_verification import QuantityVerificationResult
from app.schemas.condition_analysis import ConditionAnalysisResult


def mask_name(name: Optional[str]) -> str:
    """Mask customer name for privacy (e.g., 'Demo Customer' -> 'D*** C***')."""
    if not name or name.strip() == "":
        return "N/A"
    parts = name.strip().split()
    masked = [f"{p[0]}***" if len(p) > 1 else f"{p}*" for p in parts]
    return " ".join(masked)


def mask_email(email: Optional[str]) -> str:
    """Mask customer email for privacy (e.g., 'user@example.com' -> 'u***@example.com')."""
    if not email or "@" not in email:
        return "N/A"
    local, domain = email.split("@", 1)
    masked_local = f"{local[0]}***" if len(local) > 1 else f"{local}*"
    return f"{masked_local}@{domain}"


def mask_address(address: Optional[str]) -> str:
    """Mask customer street address while preserving locality / PIN code."""
    if not address or address.strip() == "":
        return "N/A"
    # Find PIN code if present
    pin_match = re.search(r"\b\d{6}\b", address)
    pin_str = f" [PIN: {pin_match.group(0)}]" if pin_match else ""
    return f"*** Hidden for Privacy ***{pin_str}"


def redact_report_payload(payload: dict, order: Optional[Order] = None) -> dict:
    """Do not copy raw OCR or unmasked recipient comparisons into public reports."""
    sensitive = []
    if order:
        sensitive.extend([order.customer_name, order.customer_email, order.delivery_address])
    parsed = (payload.get("label_analysis") or {}).get("parsed_fields") or {}
    for name in ("customer_name", "phone_number"):
        field = parsed.get(name)
        if isinstance(field, dict):
            sensitive.extend([field.get("value"), field.get("normalized_value")])

    def clean(value):
        if isinstance(value, dict):
            result = {key: ([] if key in ("raw_evidence", "supporting_text", "detected_product_text") else clean(item))
                      for key, item in value.items()}
            if result.get("field_name") in ("customer_name", "phone_number"):
                for key in ("label_value", "order_value"):
                    if result.get(key):
                        result[key] = "***"
                result["details"] = "Recipient field comparison: " + str(result.get("status", "unknown"))
            return result
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, str):
            for text in sensitive:
                if text and len(text) > 1:
                    value = re.sub(re.escape(text), "***", value, flags=re.IGNORECASE)
            return value
        return value
    return clean(payload)


def synthesize_overall_decision(
    barcode_status: Optional[str], label_status: Optional[str],
    product_decision: Optional[str], mismatch_reasons: List[str],
    quantity_status: Optional[str] = None, condition_status: Optional[str] = None,
) -> tuple[OverallVerificationDecision, str, str]:
    """Preserve old outcomes while explicitly separating missing and failed channels."""
    channels = {"Barcode/QR": barcode_status, "Shipping label": label_status,
                "Product visual matching": product_decision,
                "Quantity verification": quantity_status, "Visible condition": condition_status}
    observations = "; ".join(f"{name}: {value}" for name, value in channels.items() if value is not None)
    explanation = observations + "." if observations else "No completed evidence channels."
    if label_status == "insufficient_evidence":
        explanation += " Shipping-label text was unavailable or insufficient."
    mismatch = (barcode_status in ("details_mismatch", "already_used")
                or label_status == "mismatch" or product_decision == "mismatch" or quantity_status == "mismatch")
    if mismatch:
        return OverallVerificationDecision.MISMATCH, explanation, "Review the reported contradiction and its supporting evidence."
    if condition_status == "damaged":
        return OverallVerificationDecision.ISSUE_DETECTED, explanation, "Visible damage evidence requires human confirmation and claim review."
    if condition_status in ("likely_damaged", "manual_review_required", "insufficient_evidence", "model_unavailable"):
        return OverallVerificationDecision.MANUAL_REVIEW_REQUIRED, explanation, "Resolve incomplete or concerning visible-condition evidence."
    if quantity_status in ("manual_review_required", "insufficient_evidence", "model_unavailable"):
        return OverallVerificationDecision.MANUAL_REVIEW_REQUIRED, explanation, "Obtain reliable quantity evidence before confirmation."
    if label_status == "manual_review_required":
        return OverallVerificationDecision.MANUAL_REVIEW_REQUIRED, explanation, "Resolve uncertain shipping-label evidence."
    full_old = (barcode_status == "verified" and label_status in ("verified", "partially_verified") and product_decision == "match")
    if (full_old and label_status == "verified" and quantity_status == "match"
            and condition_status == "no_visible_damage"):
        return OverallVerificationDecision.VERIFIED, explanation, "All required evidence channels support verification of the supplied views."
    if barcode_status == "verified" and product_decision in ("match", "likely_match", None):
        return OverallVerificationDecision.LIKELY_VERIFIED, explanation, "Available evidence supports a partial verification; review incomplete channels."
    return OverallVerificationDecision.MANUAL_REVIEW_REQUIRED, explanation, "Insufficient evidence for automated confirmation."


HTML_REPORT_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Delivery Verification Report - {{ report_id }}</title>
    <style>
        :root {
            --bg-color: #0f172a;
            --surface-color: #1e293b;
            --card-border: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --brand-color: #38bdf8;
            --status-verified: #22c55e;
            --status-likely: #0ea5e9;
            --status-mismatch: #ef4444;
            --status-review: #f59e0b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            line-height: 1.6;
            padding: 24px 16px;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--card-border);
            padding-bottom: 20px;
            margin-bottom: 24px;
            flex-wrap: wrap;
            gap: 16px;
        }
        .header h1 { font-size: 1.5rem; font-weight: 700; color: #fff; }
        .header .meta { font-size: 0.85rem; color: var(--text-muted); }
        .badge {
            display: inline-block;
            padding: 6px 14px;
            border-radius: 9999px;
            font-weight: 700;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .badge-verified { background: rgba(34, 197, 94, 0.15); color: var(--status-verified); border: 1px solid var(--status-verified); }
        .badge-likely_verified { background: rgba(14, 165, 233, 0.15); color: var(--status-likely); border: 1px solid var(--status-likely); }
        .badge-mismatch { background: rgba(239, 68, 68, 0.15); color: var(--status-mismatch); border: 1px solid var(--status-mismatch); }
        .badge-issue_detected { color: var(--status-mismatch); border: 1px solid var(--status-mismatch); }
        .badge-manual_review_required { background: rgba(245, 158, 11, 0.15); color: var(--status-review); border: 1px solid var(--status-review); }
        
        .card {
            background-color: var(--surface-color);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .card-title {
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 16px;
            color: var(--brand-color);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
        }
        .field {
            display: flex;
            flex-direction: column;
        }
        .field-label {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 4px;
        }
        .field-value {
            font-size: 0.95rem;
            font-weight: 500;
            word-break: break-all;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
            margin-top: 8px;
        }
        th, td {
            text-align: left;
            padding: 10px 12px;
            border-bottom: 1px solid var(--card-border);
        }
        th { color: var(--text-muted); font-size: 0.8rem; text-transform: uppercase; }
        .score-bar-bg {
            background-color: #334155;
            border-radius: 6px;
            height: 8px;
            width: 100%;
            overflow: hidden;
            margin-top: 6px;
        }
        .score-bar-fill {
            background: linear-gradient(90deg, #38bdf8, #22c55e);
            height: 100%;
            border-radius: 6px;
        }
        .warning-box {
            background: rgba(245, 158, 11, 0.1);
            border-left: 4px solid var(--status-review);
            padding: 12px 16px;
            border-radius: 4px;
            font-size: 0.9rem;
            margin-top: 12px;
        }
        .footer {
            text-align: center;
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: 32px;
            border-top: 1px solid var(--card-border);
            padding-top: 16px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>Delivery Verification Report</h1>
                <div class="meta">Report ID: {{ report_id }} | Generated: {{ created_at_formatted }}</div>
            </div>
            <div>
                <span class="badge badge-{{ final_decision }}">{{ final_decision.replace('_', ' ') }}</span>
            </div>
        </div>

        <!-- Overall Summary Card -->
        <div class="card">
            <div class="card-title">Executive Summary</div>
            <p style="margin-bottom: 12px; font-size: 0.95rem;">{{ decision_explanation }}</p>
            <div class="field" style="margin-top: 8px;">
                <span class="field-label">System Recommendation</span>
                <span class="field-value" style="color: #cbd5e1;">{{ human_review_recommendation }}</span>
            </div>
            {% if warnings %}
            <div class="warning-box">
                <strong>Attention / Warnings:</strong>
                <ul style="margin-left: 20px; margin-top: 6px;">
                    {% for w in warnings %}
                    <li>{{ w }}</li>
                    {% endfor %}
                </ul>
            </div>
            {% endif %}
        </div>

        <!-- Order & Customer Information -->
        <div class="card">
            <div class="card-title">Order Information (PII Masked)</div>
            <div class="grid">
                <div class="field">
                    <span class="field-label">Order ID</span>
                    <span class="field-value">{{ order_id or 'N/A' }}</span>
                </div>
                <div class="field">
                    <span class="field-label">Platform</span>
                    <span class="field-value">{{ platform or 'N/A' }}</span>
                </div>
                <div class="field">
                    <span class="field-label">Tracking / Barcode ID</span>
                    <span class="field-value">{{ tracking_id or 'N/A' }}</span>
                </div>
                <div class="field">
                    <span class="field-label">Customer Name</span>
                    <span class="field-value">{{ masked_customer_name }}</span>
                </div>
                <div class="field">
                    <span class="field-label">Customer Email</span>
                    <span class="field-value">{{ masked_customer_email }}</span>
                </div>
                <div class="field">
                    <span class="field-label">Delivery Location</span>
                    <span class="field-value">{{ masked_delivery_address }}</span>
                </div>
            </div>
        </div>

        <!-- 8-Field Shipping Label Analysis -->
        {% if label_summary %}
        <div class="card">
            <div class="card-title">Shipping Label OCR Summary (8 Fields)</div>
            <table>
                <thead>
                    <tr>
                        <th>Field Name</th>
                        <th>Detected OCR Value</th>
                    </tr>
                </thead>
                <tbody>
                    <tr><td>Platform</td><td>{{ label_summary.platform or 'Not Detected' }}</td></tr>
                    <tr><td>Courier</td><td>{{ label_summary.courier or 'Not Detected' }}</td></tr>
                    <tr><td>Order ID</td><td>{{ label_summary.order_id or 'Not Detected' }}</td></tr>
                    <tr><td>AWB / Tracking Number</td><td>{{ label_summary.tracking_number or 'Not Detected' }}</td></tr>
                    <tr><td>PIN Code</td><td>{{ label_summary.pin_code or 'Not Detected' }}</td></tr>
                    <tr><td>Product Description</td><td>{{ label_summary.product or 'Not Detected' }}</td></tr>
                    <tr><td>Colour / Model</td><td>{{ label_summary.colour_model or 'Not Detected' }}</td></tr>
                    <tr><td>Quantity</td><td>{{ label_summary.quantity if label_summary.quantity is not none else 'Not Detected' }}</td></tr>
                </tbody>
            </table>
        </div>
        {% endif %}

        <!-- Product Matching Scores -->
        {% if product_matching %}
        <div class="card">
            <div class="card-title">
                <span>Product Visual Matching Analysis</span>
                <span style="font-size: 0.9rem; color: #fff;">Overall Score: <strong>{{ (product_matching.overall_match_score * 100)|round(1) }}%</strong></span>
            </div>
            <div class="grid" style="margin-bottom: 16px;">
                <div class="field">
                    <span class="field-label">Expected Product</span>
                    <span class="field-value">{{ product_matching.expected_product_name }}</span>
                </div>
                <div class="field">
                    <span class="field-label">Expected Brand & Model</span>
                    <span class="field-value">{{ product_matching.expected_brand }} - {{ product_matching.expected_model }}</span>
                </div>
                <div class="field">
                    <span class="field-label">Best Match Frame</span>
                    <span class="field-value">{{ product_matching.best_candidate_frame or 'N/A' }} ({{ product_matching.best_frame_timestamp_seconds or 0.0 }}s)</span>
                </div>
                <div class="field">
                    <span class="field-label">Matching Decision</span>
                    <span class="field-value">{{ product_matching.decision.value }}</span>
                </div>
            </div>

            <div style="margin-top: 12px;">
                <span class="field-label">Component Breakdown</span>
                <table style="margin-top: 6px;">
                    <tbody>
                        <tr>
                            <td>OCR Text Similarity</td>
                            <td style="width: 35%;">
                                <div class="score-bar-bg"><div class="score-bar-fill" style="width: {{ (product_matching.ocr_text_score * 100)|round }}%;"></div></div>
                            </td>
                            <td style="text-align: right; width: 15%;">{{ (product_matching.ocr_text_score * 100)|round(1) }}%</td>
                        </tr>
                        <tr>
                            <td>ORB Local Feature Match</td>
                            <td>
                                <div class="score-bar-bg"><div class="score-bar-fill" style="width: {{ (product_matching.feature_match_score * 100)|round }}%;"></div></div>
                            </td>
                            <td style="text-align: right;">{{ (product_matching.feature_match_score * 100)|round(1) }}%</td>
                        </tr>
                        <tr>
                            <td>Perceptual Image Hash</td>
                            <td>
                                <div class="score-bar-bg"><div class="score-bar-fill" style="width: {{ (product_matching.perceptual_hash_score * 100)|round }}%;"></div></div>
                            </td>
                            <td style="text-align: right;">{{ (product_matching.perceptual_hash_score * 100)|round(1) }}%</td>
                        </tr>
                        <tr>
                            <td>HSV Colour Distribution</td>
                            <td>
                                <div class="score-bar-bg"><div class="score-bar-fill" style="width: {{ (product_matching.colour_similarity_score * 100)|round }}%;"></div></div>
                            </td>
                            <td style="text-align: right;">{{ (product_matching.colour_similarity_score * 100)|round(1) }}%</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
        {% endif %}

        <div class="card">
            <div class="card-title">Evidence Channel Status</div>
            {% if channel_statuses %}
            <ul>{% for channel, state in channel_statuses.items() %}<li>{{ channel }}: {{ state }}</li>{% endfor %}</ul>
            {% else %}<p>Legacy report: detailed channel status was not recorded.</p>{% endif %}
        </div>
        <div class="card">
            <div class="card-title">Quantity Verification</div>
            {% if quantity_verification %}
            <p>Status: {{ quantity_verification.status.value }}</p>
            <p>Expected: {{ quantity_verification.expected_quantity }} | Detected:
                {{ quantity_verification.detected_quantity if quantity_verification.detected_quantity is not none else 'Unknown' }}</p>
            <p>Method: {{ quantity_verification.detection_method }} | Target: {{ quantity_verification.target_class or 'Unsupported' }}</p>
            <p>Score: {{ quantity_verification.confidence }} | Model: {{ quantity_verification.model_metadata.model }}</p>
            <ul>{% for frame in quantity_verification.supporting_frames %}<li>{{ frame.frame_filename }} at {{ frame.timestamp_seconds }}s</li>{% endfor %}</ul>
            {% else %}<p>Not run / no saved analysis. Quantity has not been verified.</p>{% endif %}
        </div>
        <div class="card">
            <div class="card-title">Visible Condition Analysis</div>
            {% if condition_analysis %}
            <p>Status: {{ 'no visible damage detected' if condition_analysis.status.value == 'no_visible_damage' else condition_analysis.status.value }}</p>
            <p>Evidence: {{ condition_analysis.evidence_type.value }} | Score: {{ condition_analysis.confidence }}</p>
            <p>Model: {{ condition_analysis.model_metadata.model }} | Categories: {{ condition_analysis.damage_categories|join(', ') or 'Unconfirmed' }}</p>
            <ul>{% for frame in condition_analysis.supporting_frames %}<li>{{ frame.frame_filename }} at {{ frame.timestamp_seconds }}s</li>{% endfor %}</ul>
            {% else %}<p>Not run / no saved analysis. Visible condition has not been assessed.</p>{% endif %}
            <p>Internal, electrical and functional condition are not assessed.</p>
        </div>

        <!-- Customer Product Intake & Reference -->
        {% if customer_product_name or product_url %}
        <div class="card">
            <div class="card-title">Customer Product Intake & Reference</div>
            <div class="grid">
                <div class="field">
                    <span class="field-label">Customer Specified Product</span>
                    <span class="field-value">{{ customer_product_name or 'N/A' }}</span>
                </div>
                <div class="field">
                    <span class="field-label">Product URL</span>
                    <span class="field-value">{{ product_url or 'N/A' }}</span>
                </div>
                <div class="field">
                    <span class="field-label">Reference Source</span>
                    <span class="field-value">{{ reference_source or 'reference_unavailable' }}</span>
                </div>
            </div>
        </div>
        {% endif %}

        <!-- Complaint Assistant Status -->
        <div class="card">
            <div class="card-title">Complaint Assistant Status</div>
            <p><strong>Recommendation:</strong> {{ 'Eligible for Complaint' if complaint_eligible else 'Not Recommended' }}</p>
            {% if complaint_reasons %}
            <ul style="margin-left: 20px; margin-top: 6px;">
                {% for r in complaint_reasons %}
                <li>{{ r }}</li>
                {% endfor %}
            </ul>
            {% endif %}
            <div class="grid" style="margin-top: 12px;">
                <div class="field">
                    <span class="field-label">Complaint Draft</span>
                    <span class="field-value">{{ complaint_draft_status or 'Not Generated' }}</span>
                </div>
                <div class="field">
                    <span class="field-label">Email Delivery</span>
                    <span class="field-value">{{ email_delivery_status or 'Not Attempted' }}</span>
                </div>
            </div>
        </div>

        <!-- Evidence & Media Metadata -->
        <div class="card">
            <div class="card-title">Media & Audit Trail</div>
            <div class="grid">
                <div class="field">
                    <span class="field-label">Input Source</span>
                    <span class="field-value">{{ original_filename or 'Uploaded File' }} ({{ input_type or 'media' }})</span>
                </div>
                <div class="field">
                    <span class="field-label">Session ID</span>
                    <span class="field-value">{{ verification_session_id or 'N/A' }}</span>
                </div>
                <div class="field">
                    <span class="field-label">Evidence Files</span>
                    <span class="field-value">{{ evidence_filenames|join(', ') if evidence_filenames else 'N/A' }}</span>
                </div>
            </div>
        </div>

        <div class="footer">
            Delivery Verification & Automated Complaint System | Confidential Prototype Report
        </div>
    </div>
</body>
</html>
"""


def generate_verification_report(
    db: Session,
    session_id: str,
) -> VerificationReportRead:
    """Generate a comprehensive verification report from a verification session and persist it."""
    now = datetime.now(timezone.utc)

    # 1. Fetch verification session
    v_session = (
        db.query(VerificationSession)
        .filter(VerificationSession.id == session_id)
        .first()
    )
    if not v_session:
        raise ValueError(f"Verification session '{session_id}' not found.")

    # Check for existing report for this session to ensure idempotent regeneration
    existing_report = (
        db.query(VerificationReport)
        .filter(VerificationReport.verification_session_id == session_id)
        .order_by(VerificationReport.created_at.desc())
        .first()
    )
    report_id = existing_report.id if existing_report else uuid4().hex

    # 2. Fetch associated order
    order: Optional[Order] = None
    if v_session.order_id:
        order = db.query(Order).filter(Order.id == v_session.order_id).first()
    elif v_session.decoded_code:
        order = (
            db.query(Order)
            .filter(
                (Order.barcode_value == v_session.decoded_code)
                | (Order.tracking_id == v_session.decoded_code)
            )
            .first()
        )

    # 3. Retrieve product matching record strictly linked to this verification session
    pm_record = (
        db.query(ProductMatchingRecord)
        .filter(ProductMatchingRecord.verification_session_id == session_id)
        .order_by(ProductMatchingRecord.created_at.desc())
        .first()
    )
    pm_result: Optional[ProductMatchingResult] = None
    if pm_record and pm_record.details:
        try:
            pm_result = ProductMatchingResult.model_validate(pm_record.details)
        except Exception:
            pass

    quantity_record = db.query(QuantityVerificationRecord).filter_by(verification_session_id=session_id, order_id=v_session.order_id).first()
    condition_record = db.query(ConditionAnalysisRecord).filter_by(verification_session_id=session_id, order_id=v_session.order_id).first()
    quantity_result = _read_optional_analysis(quantity_record, QuantityVerificationResult)
    condition_result = _read_optional_analysis(condition_record, ConditionAnalysisResult)

    # 4. Fetch customer product submission & complaint draft
    cust_submission = (
        db.query(CustomerProductSubmission)
        .filter(CustomerProductSubmission.verification_session_id == session_id)
        .order_by(CustomerProductSubmission.created_at.desc())
        .first()
    )
    complaint_draft = (
        db.query(ComplaintDraft)
        .filter(ComplaintDraft.verification_session_id == session_id)
        .order_by(ComplaintDraft.created_at.desc())
        .first()
    )

    # 5. Mask Customer PII
    masked_name = mask_name(order.customer_name if order else None)
    masked_email = mask_email(order.customer_email if order else None)
    masked_addr = mask_address(order.delivery_address if order else None)

    # 5. Build Barcode Result Summary
    barcode_summary = {
        "status": v_session.verification_status,
        "decoded_code": v_session.decoded_code,
        "barcode_type": v_session.barcode_type,
        "mismatch_reasons": v_session.mismatch_reasons,
        "evidence_frame": v_session.evidence_frame,
        "video_timestamp_seconds": v_session.video_timestamp_seconds,
    }

    # 6. Extract Label Analysis from VerificationSession
    label_summary: Optional[LabelSummary] = None
    label_analysis_dict: Optional[Dict[str, Any]] = None
    label_status_val: Optional[str] = v_session.label_analysis_status

    if v_session.label_summary is not None:
        try:
            label_summary = LabelSummary.model_validate(v_session.label_summary)
        except Exception:
            pass

    if v_session.label_analysis_data is not None and isinstance(v_session.label_analysis_data, dict):
        label_analysis_dict = v_session.label_analysis_data

    # Synthesize overall verdict
    final_dec, explanation, recommendation = synthesize_overall_decision(
        barcode_status=v_session.verification_status,
        label_status=label_status_val,
        product_decision=pm_result.decision.value if pm_result else None,
        mismatch_reasons=v_session.mismatch_reasons or [],
        quantity_status=quantity_result.status.value if quantity_result else None,
        condition_status=condition_result.status.value if condition_result else None,
    )

    all_warnings: List[str] = list(v_session.mismatch_reasons or [])
    if label_analysis_dict:
        for lm in label_analysis_dict.get("mismatch_reasons") or []:
            if lm not in all_warnings:
                all_warnings.append(lm)
        for lw in label_analysis_dict.get("warnings") or []:
            if lw not in all_warnings:
                all_warnings.append(lw)

    if label_status_val == LabelVerificationStatus.INSUFFICIENT_EVIDENCE.value:
        insuf_msg = "Shipping-label evidence was insufficient; verify label details manually if full confirmation is required."
        if insuf_msg not in all_warnings:
            all_warnings.append(insuf_msg)

    if pm_result and pm_result.warnings:
        for pw in pm_result.warnings:
            if pw not in all_warnings:
                all_warnings.append(pw)

    channel_statuses = {
        "barcode_verification": v_session.verification_status or "not_run",
        "label_analysis": label_status_val or "not_run",
        "product_matching": pm_result.decision.value if pm_result else ("missing_analysis" if pm_record else "not_run"),
        "quantity_verification": quantity_result.status.value if quantity_result else ("missing_analysis" if quantity_record else "not_run"),
        "condition_analysis": condition_result.status.value if condition_result else ("missing_analysis" if condition_record else "not_run"),
    }
    successes = {"verified", "match", "no_visible_damage"}
    if "missing_analysis" in channel_statuses.values() and final_dec in (
        OverallVerificationDecision.VERIFIED, OverallVerificationDecision.LIKELY_VERIFIED,
    ):
        final_dec = OverallVerificationDecision.MANUAL_REVIEW_REQUIRED
        recommendation = "Regenerate unavailable saved analysis before confirmation."
    for channel, channel_status in channel_statuses.items():
        if channel_status not in successes:
            all_warnings.append(f"{channel}: {channel_status}; this channel has not passed.")
    for analysis in (quantity_result, condition_result):
        if analysis:
            all_warnings.extend(analysis.warnings)
            all_warnings.extend(analysis.mismatch_reasons)

    evidence_files: List[str] = []
    if v_session.evidence_frame:
        evidence_files.append(v_session.evidence_frame)
    if pm_result and pm_result.best_candidate_frame:
        evidence_files.append(pm_result.best_candidate_frame)

    relevant_timestamps: List[float] = []
    if v_session.video_timestamp_seconds is not None:
        relevant_timestamps.append(v_session.video_timestamp_seconds)
    if pm_result and pm_result.best_frame_timestamp_seconds is not None:
        relevant_timestamps.append(pm_result.best_frame_timestamp_seconds)

    for analysis in (quantity_result, condition_result):
        if analysis:
            evidence_files.extend(f.frame_filename for f in analysis.supporting_frames)
            relevant_timestamps.extend(f.timestamp_seconds for f in analysis.supporting_frames)

    # Determine Customer Intake & Reference fields
    cust_product_name = cust_submission.product_name if cust_submission else None
    prod_url = cust_submission.product_url if cust_submission else (order.items[0].product_url if (order and order.items) else None)
    ref_source: Optional[str] = None
    if cust_submission and cust_submission.resolution_metadata:
        ref_source = cust_submission.resolution_metadata.get("reference_source")
    elif pm_result and pm_result.best_reference_id:
        ref_source = "demo_catalogue"

    from app.services.complaint_service import check_complaint_eligibility
    complaint_elig = check_complaint_eligibility(db, session_id)

    report_read = VerificationReportRead(
        report_id=report_id,
        verification_session_id=session_id,
        analysis_id=v_session.analysis_id,
        created_at=now,
        original_filename=v_session.original_filename or v_session.evidence_frame,
        input_type=v_session.input_type or ("video" if v_session.video_timestamp_seconds is not None else "image"),
        order_id=order.order_id if order else None,
        platform=order.platform if order else None,
        tracking_id=order.tracking_id if order else None,
        masked_customer_name=masked_name,
        masked_customer_email=masked_email,
        masked_delivery_address=masked_addr,
        barcode_verification=barcode_summary,
        label_summary=label_summary,
        label_analysis=label_analysis_dict,
        product_matching=pm_result,
        quantity_verification=quantity_result,
        condition_analysis=condition_result,
        channel_statuses=channel_statuses,
        customer_product_name=cust_product_name,
        product_url=prod_url,
        reference_source=ref_source,
        complaint_eligible=complaint_elig.eligible,
        complaint_reasons=complaint_elig.reasons,
        complaint_draft_status=complaint_draft.delivery_status if complaint_draft else None,
        email_delivery_status=complaint_draft.delivery_status if (complaint_draft and complaint_draft.delivery_status in ("sent", "dry_run", "failed")) else None,
        final_decision=final_dec,
        decision_explanation=explanation,
        warnings=all_warnings,
        evidence_filenames=list(set(evidence_files)),
        relevant_timestamps_seconds=list(set(relevant_timestamps)),
        human_review_recommendation=recommendation,
        json_report_url=f"/api/reports/{report_id}",
        html_report_url=f"/api/reports/{report_id}/html",
    )

    report_read = VerificationReportRead.model_validate(redact_report_payload(report_read.model_dump(mode="json"), order))

    # 7. Render the same persisted snapshot used by JSON retrieval.
    html_content = render_report(report_read)

    # 8. Persist files to disk
    json_path = REPORT_DIR / f"{report_id}.json"
    html_path = REPORT_DIR / f"{report_id}.html"

    json_path.write_text(report_read.model_dump_json(indent=2), encoding="utf-8")
    html_path.write_text(html_content, encoding="utf-8")

    # 9. Persist Report in SQLite
    if existing_report:
        existing_report.order_id = order.id if order else None
        existing_report.final_decision = final_dec.value
        existing_report.report_data = report_read.model_dump(mode="json")
        existing_report.json_path = f"reports/{report_id}.json"
        existing_report.html_path = f"reports/{report_id}.html"
        existing_report.created_at = now
        db.commit()
    else:
        db_report = VerificationReport(
            id=report_id,
            verification_session_id=session_id,
            order_id=order.id if order else None,
            final_decision=final_dec.value,
            report_data=report_read.model_dump(mode="json"),
            json_path=f"reports/{report_id}.json",
            html_path=f"reports/{report_id}.html",
            created_at=now,
        )
        db.add(db_report)
        db.commit()

    return report_read


def get_report_by_id(db: Session, report_id: str) -> Optional[VerificationReportRead]:
    """Retrieve saved report by its report UUID."""
    record = db.query(VerificationReport).filter(VerificationReport.id == report_id).first()
    if not record or not record.report_data:
        return None
    order = db.get(Order, record.order_id) if record.order_id is not None else None
    return VerificationReportRead.model_validate(redact_report_payload(record.report_data, order))


def _read_optional_analysis(record, schema):
    if record is None:
        return None
    try:
        return schema.model_validate(record.details)
    except (ValidationError, TypeError):
        return None


def render_report(report: VerificationReportRead) -> str:
    values = {name: getattr(report, name) for name in type(report).model_fields}
    values["created_at_formatted"] = report.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    values["final_decision"] = report.final_decision.value
    return Template(HTML_REPORT_TEMPLATE, autoescape=True).render(**values)


def get_report_html_by_id(db: Session, report_id: str) -> Optional[str]:
    """Render the stored DB snapshot, including legacy reports; never accept a filesystem path."""
    report = get_report_by_id(db, report_id)
    return render_report(report) if report else None
