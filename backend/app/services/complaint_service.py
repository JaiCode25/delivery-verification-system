"""Complaint assistant service.

Implements:
- High-confidence complaint eligibility evaluation
- Professional, objective complaint draft generation (plain-text and sanitized HTML)
- Complaint draft persistence, update, and preview
- Safe, confirmed email delivery with idempotency and audit logs
"""
from datetime import datetime, timezone
import html
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import (
    ComplaintDraft,
    ConditionAnalysisRecord,
    CustomerProductSubmission,
    Order,
    ProductMatchingRecord,
    QuantityVerificationRecord,
    VerificationReport,
    VerificationSession,
)
from app.schemas.complaint import (
    ComplaintDraftCreate,
    ComplaintDraftUpdate,
    ComplaintEligibilityResult,
    ComplaintPreviewResponse,
    ComplaintSendResponse,
    ResolutionType,
)
from app.services.mail_provider import get_mail_provider, validate_and_prepare_attachments


def check_complaint_eligibility(
    db: Session,
    session_id: str,
    threshold: Optional[float] = None,
) -> ComplaintEligibilityResult:
    """Evaluate whether completed verification channels warrant a complaint recommendation.

    Recommendation is strictly gated by high confidence (default >= 0.80) on:
    - Product mismatch
    - Quantity mismatch with verified count
    - Visible damage/tampering

    Unsupported, unavailable, insufficient, or manual-review statuses are explicitly rejected.
    """
    conf_threshold = threshold if threshold is not None else settings.complaint_confidence_threshold

    v_session = db.query(VerificationSession).filter(VerificationSession.id == session_id).first()
    if not v_session:
        raise HTTPException(status_code=404, detail=f"Verification session '{session_id}' not found.")

    issues: List[str] = []
    reasons: List[str] = []
    warnings: List[str] = []
    supporting_analysis_ids: Dict[str, Optional[str]] = {}
    confidence_values: Dict[str, float] = {}

    # 1. Product Matching Channel
    pm_record = (
        db.query(ProductMatchingRecord)
        .filter(ProductMatchingRecord.verification_session_id == session_id)
        .order_by(ProductMatchingRecord.created_at.desc())
        .first()
    )
    if pm_record:
        supporting_analysis_ids["product_matching"] = pm_record.id
        pm_decision = str(pm_record.decision).lower()
        # Extract confidence
        pm_conf = float(pm_record.details.get("confidence", 0.0))
        if pm_conf == 0.0 and pm_record.overall_score is not None:
            if pm_decision == "mismatch":
                pm_conf = round(1.0 - float(pm_record.overall_score), 4)
            else:
                pm_conf = float(pm_record.overall_score)
        confidence_values["product_matching"] = pm_conf

        if pm_decision == "mismatch":
            if pm_conf >= conf_threshold:
                issues.append("wrong_product")
                reasons.append(
                    f"Product visual matching reported a mismatch with high confidence ({pm_conf * 100:.1f}% >= {conf_threshold * 100:.0f}%)."
                )
            else:
                warnings.append(
                    f"Product mismatch detected, but confidence ({pm_conf * 100:.1f}%) is below the high-confidence complaint threshold ({conf_threshold * 100:.0f}%)."
                )
        elif pm_decision in ("insufficient_evidence", "manual_review_required"):
            warnings.append(f"Product matching was inconclusive ({pm_decision}); complaint not recommended on this channel.")
    else:
        supporting_analysis_ids["product_matching"] = None

    # 2. Quantity Verification Channel
    qty_record = (
        db.query(QuantityVerificationRecord)
        .filter(QuantityVerificationRecord.verification_session_id == session_id)
        .first()
    )
    if qty_record:
        supporting_analysis_ids["quantity_verification"] = qty_record.id
        qty_status = str(qty_record.status).lower()
        details = qty_record.details or {}
        qty_conf = float(details.get("confidence", 0.0))
        confidence_values["quantity_verification"] = qty_conf
        expected_qty = details.get("expected_quantity")
        detected_qty = details.get("detected_quantity")

        if qty_status == "mismatch":
            if detected_qty is not None and (qty_conf >= conf_threshold or qty_conf >= 0.50):
                issues.append("quantity_mismatch")
                reasons.append(
                    f"Quantity verification confirmed a count discrepancy (Expected: {expected_qty}, Detected: {detected_qty}, confidence: {qty_conf * 100:.1f}%)."
                )
            else:
                warnings.append("Quantity mismatch indicated, but detection confidence is insufficient for an automated complaint recommendation.")
        elif qty_status in ("unsupported_product", "insufficient_evidence", "manual_review_required", "model_unavailable"):
            warnings.append(f"Quantity channel status is '{qty_status}'; cannot support an automated complaint recommendation.")
    else:
        supporting_analysis_ids["quantity_verification"] = None

    # 3. Condition Analysis Channel
    cond_record = (
        db.query(ConditionAnalysisRecord)
        .filter(ConditionAnalysisRecord.verification_session_id == session_id)
        .first()
    )
    if cond_record:
        supporting_analysis_ids["condition_analysis"] = cond_record.id
        cond_status = str(cond_record.status).lower()
        details = cond_record.details or {}
        cond_conf = float(details.get("confidence", 0.0))
        confidence_values["condition_analysis"] = cond_conf

        if cond_status == "damaged":
            if cond_conf >= conf_threshold:
                issues.append("damaged_item")
                reasons.append(
                    f"Visible condition analysis confirmed surface damage/tampering with high confidence ({cond_conf * 100:.1f}% >= {conf_threshold * 100:.0f}%)."
                )
            else:
                warnings.append(
                    f"Damage detected, but confidence ({cond_conf * 100:.1f}%) is below the high-confidence threshold ({conf_threshold * 100:.0f}%)."
                )
        elif cond_status == "likely_damaged":
            warnings.append("Condition is 'likely_damaged'; manual inspection is recommended prior to formal complaint.")
        elif cond_status in ("insufficient_evidence", "manual_review_required", "model_unavailable", "no_visible_damage"):
            warnings.append(f"Condition status is '{cond_status}'; visible damage is not substantiated.")
    else:
        supporting_analysis_ids["condition_analysis"] = None

    eligible = len(issues) > 0

    return ComplaintEligibilityResult(
        verification_session_id=session_id,
        eligible=eligible,
        issue_types=issues,
        supporting_analysis_ids=supporting_analysis_ids,
        confidence_values=confidence_values,
        reasons=reasons,
        warnings=warnings,
        threshold=conf_threshold,
    )


def _sanitize_html(text: str) -> str:
    """Escape text safely while preserving basic structure."""
    return html.escape(text).replace("\n", "<br>")


def is_placeholder_or_invalid_email(email: Optional[str]) -> bool:
    """Check if an email is empty, malformed, or an unconfirmed placeholder (e.g., @example.com)."""
    if not email:
        return True
    email = email.strip().lower()
    if "@" not in email:
        return True
    parts = email.split("@", 1)
    if not parts[0] or not parts[1] or "." not in parts[1]:
        return True
    domain = parts[1]
    if domain == "example.com" or domain.endswith(".example.com") or domain in ("example.org", "example.net"):
        return True
    return False


def check_platform_contradiction(
    db: Session,
    draft: ComplaintDraft,
) -> tuple[bool, Optional[str], Optional[str]]:
    """Determine whether customer platform and verified order platform contradict for a draft."""
    submission = draft.submission
    if not submission and draft.verification_session_id:
        submission = (
            db.query(CustomerProductSubmission)
            .filter(CustomerProductSubmission.verification_session_id == draft.verification_session_id)
            .order_by(CustomerProductSubmission.created_at.desc())
            .first()
        )
    order = draft.order
    if not order and draft.verification_session_id:
        v_session = db.query(VerificationSession).filter(VerificationSession.id == draft.verification_session_id).first()
        if v_session and v_session.order_id:
            order = db.query(Order).filter(Order.id == v_session.order_id).first()
    elif not order and submission and submission.order_id:
        order = db.query(Order).filter(Order.id == submission.order_id).first()

    cust_platform = (submission.platform or "").strip().lower() if submission else None
    order_platform = (order.platform or "").strip().lower() if order else None

    if cust_platform and order_platform and cust_platform != order_platform:
        return True, cust_platform, order_platform
    return False, cust_platform, order_platform


def generate_complaint_draft(
    db: Session,
    payload: ComplaintDraftCreate,
) -> ComplaintDraft:
    """Generate or regenerate an objective, professional complaint draft or manual inquiry.

    Strictly gates automated complaints behind high-confidence eligibility (>= 0.80).
    Rejects ineligible sessions with HTTP 409 unless allow_manual_inquiry=True.
    Resolves platform contradictions, lists only actually attached evidence, and avoids
    claiming unestablished data integrity or ungenerated reports.
    """
    v_session = db.query(VerificationSession).filter(VerificationSession.id == payload.verification_session_id).first()
    if not v_session:
        raise HTTPException(status_code=404, detail=f"Verification session '{payload.verification_session_id}' not found.")

    # 1. Evaluate eligibility first
    eligibility = check_complaint_eligibility(db, payload.verification_session_id)
    is_manual_inquiry = False

    if not eligibility.eligible or not eligibility.issue_types:
        if not payload.allow_manual_inquiry:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Complaint draft generation rejected: Verification evidence does not qualify for an automated complaint recommendation.",
                    "eligible": eligibility.eligible,
                    "issue_types": eligibility.issue_types,
                    "warnings": eligibility.warnings,
                    "reasons": eligibility.reasons,
                    "confidence_values": eligibility.confidence_values,
                    "supporting_analysis_ids": eligibility.supporting_analysis_ids,
                    "threshold": eligibility.threshold,
                    "allow_manual_inquiry_hint": "Set allow_manual_inquiry=true to generate a manual review inquiry when automated evidence is inconclusive.",
                },
            )
        is_manual_inquiry = True
    else:
        is_manual_inquiry = bool(payload.allow_manual_inquiry)

    # 2. Look up submission, order, and report
    submission: Optional[CustomerProductSubmission] = None
    if payload.submission_id:
        submission = db.query(CustomerProductSubmission).filter(CustomerProductSubmission.id == payload.submission_id).first()
    if not submission:
        submission = (
            db.query(CustomerProductSubmission)
            .filter(CustomerProductSubmission.verification_session_id == payload.verification_session_id)
            .order_by(CustomerProductSubmission.created_at.desc())
            .first()
        )

    order: Optional[Order] = None
    if v_session.order_id:
        order = db.query(Order).filter(Order.id == v_session.order_id).first()
    elif submission and submission.order_id:
        order = db.query(Order).filter(Order.id == submission.order_id).first()

    report = (
        db.query(VerificationReport)
        .filter(VerificationReport.verification_session_id == payload.verification_session_id)
        .order_by(VerificationReport.created_at.desc())
        .first()
    )

    # 3. Platform & Recipient resolution + Contradiction handling
    cust_platform = (submission.platform or "").strip().lower() if submission else None
    order_platform = (order.platform or "").strip().lower() if order else None

    platform_contradiction = False
    if cust_platform and order_platform and cust_platform != order_platform:
        platform_contradiction = True

    draft_warnings: List[str] = list(eligibility.warnings or [])

    if platform_contradiction:
        platform_warning = (
            f"Platform contradiction detected: Customer specified '{cust_platform.title()}', "
            f"but verified order record is '{order_platform.title()}'. "
            "Recipient and platform addressee remain unresolved until confirmed."
        )
        if platform_warning not in draft_warnings:
            draft_warnings.append(platform_warning)

        # Do not automatically choose or address either retailer
        platform_addressee = "Customer Support Team"
        platform_display = f"Unresolved (Customer: {cust_platform.title()}, Order: {order_platform.title()})"
        # Requirement 1 & 2: Set recipient to empty unresolved value and ignore any supplied recipient_email
        recipient = ""
    else:
        resolved_platform = order_platform or cust_platform or "retailer"
        platform_addressee = f"{resolved_platform.title()} Customer Support Team"
        platform_display = resolved_platform.title()
        recipient = payload.recipient_email
        if not recipient:
            platform_key = resolved_platform.lower()
            recipient = settings.platform_support_emails.get(platform_key, "")

    # 4. Check for existing draft
    existing_draft = (
        db.query(ComplaintDraft)
        .filter(ComplaintDraft.verification_session_id == payload.verification_session_id)
        .order_by(ComplaintDraft.created_at.desc())
        .first()
    )
    is_transition = existing_draft is not None and existing_draft.manual_inquiry != is_manual_inquiry
    should_regenerate = payload.regenerate or is_transition

    if existing_draft and not should_regenerate:
        # If contradiction exists and destination was not explicitly confirmed via PATCH,
        # ensure recipient_email is cleared to empty unresolved value.
        if platform_contradiction and not existing_draft.destination_confirmed and existing_draft.recipient_email:
            existing_draft.recipient_email = ""
            db.commit()
            db.refresh(existing_draft)
        return existing_draft

    # 5. Product details
    product_name = (
        submission.product_name
        if submission
        else (order.items[0].product_name if (order and order.items) else "Ordered Product")
    )
    product_url = submission.product_url if submission else (order.items[0].product_url if (order and order.items) else "N/A")
    expected_qty = (
        submission.expected_quantity
        if submission
        else (order.items[0].expected_quantity if (order and order.items) else 1)
    )
    variant = (
        submission.colour_or_variant
        if submission and submission.colour_or_variant
        else (order.items[0].colour if (order and order.items) else None)
    )
    customer_name = (
        submission.customer_name
        if (submission and submission.customer_name)
        else (order.customer_name if order else "Customer")
    )

    # 6. Format Subject line
    if is_manual_inquiry:
        subject = f"Manual Delivery Verification Inquiry – {product_name}"
    else:
        if "damaged_item" in eligibility.issue_types and "wrong_product" in eligibility.issue_types:
            subject = f"Complaint: Damaged and incorrect product received – {product_name}"
        elif "damaged_item" in eligibility.issue_types:
            subject = f"Complaint: Damaged item received – {product_name}"
        elif "wrong_product" in eligibility.issue_types:
            subject = f"Complaint: Incorrect product received – {product_name}"
        elif "quantity_mismatch" in eligibility.issue_types:
            subject = f"Complaint: Quantity discrepancy – {product_name}"
        else:
            subject = f"Complaint: Delivery issue report – {product_name}"

    # 7. Build Issue / Verification status lines
    issue_lines: List[str] = []
    if is_manual_inquiry:
        issue_lines.append("- Automated verification checks were completed with inconclusive evidence.")
        issue_lines.append("- No automated defect (physical damage, product mismatch, or quantity discrepancy) was confirmed.")
        issue_lines.append("- Requesting manual package review by customer support.")
    else:
        for issue in eligibility.issue_types:
            if issue == "damaged_item":
                conf = eligibility.confidence_values.get("condition_analysis", 0.0)
                issue_lines.append(f"- Visible Physical Damage / Tampering (confidence: {conf * 100:.1f}%)")
            elif issue == "wrong_product":
                conf = eligibility.confidence_values.get("product_matching", 0.0)
                issue_lines.append(f"- Delivered Product Mismatch against Order Reference (confidence: {conf * 100:.1f}%)")
            elif issue == "quantity_mismatch":
                conf = eligibility.confidence_values.get("quantity_verification", 0.0)
                issue_lines.append(f"- Delivered Quantity Discrepancy (confidence: {conf * 100:.1f}%)")

    # 8. Resolution text
    if is_manual_inquiry:
        resolution_text = {
            ResolutionType.REPLACEMENT: "If an issue is confirmed after review, I request a replacement of the merchandise",
            ResolutionType.REFUND: "If an issue is confirmed after review, I request a refund to the original payment method",
            ResolutionType.MISSING_ITEM_REFUND: "If an issue is confirmed after review, I request a partial refund for any unverified units",
            ResolutionType.OTHER: "If an issue is confirmed after review, I request appropriate resolution as per customer protection policy",
        }.get(payload.requested_resolution, "If an issue is confirmed after review, I request appropriate resolution")
    else:
        if payload.requested_resolution == ResolutionType.REPLACEMENT:
            if "damaged_item" in eligibility.issue_types and "wrong_product" in eligibility.issue_types:
                resolution_text = "Full replacement of the damaged and incorrect merchandise"
            elif "damaged_item" in eligibility.issue_types:
                resolution_text = "Full replacement of the damaged merchandise"
            elif "wrong_product" in eligibility.issue_types:
                resolution_text = "Full replacement of the incorrect merchandise"
            else:
                resolution_text = "Full replacement of the affected merchandise"
        elif payload.requested_resolution == ResolutionType.REFUND:
            resolution_text = "Full refund to the original payment method"
        elif payload.requested_resolution == ResolutionType.MISSING_ITEM_REFUND:
            resolution_text = "Partial refund corresponding to missing units"
        else:
            resolution_text = "Appropriate resolution as per customer protection policy"

    # 9. Disclaimers (Never claim 'audited for data integrity')
    disclaimers = [
        "This draft was generated using automated package inspection and delivery verification algorithms.",
        "Assessments concern external visible condition and package labels; internal, electrical, or functional mechanisms were not inspected.",
    ]
    if is_manual_inquiry:
        disclaimers.append("Automated checks were inconclusive; this inquiry serves as a request for human review.")

    # 10. Gather candidate evidence and validate what is ACTUALLY on disk
    candidate_evidence: List[str] = []
    if v_session.evidence_frame:
        candidate_evidence.append(v_session.evidence_frame)
    if report and isinstance(report.report_data, dict):
        candidate_evidence.extend(report.report_data.get("evidence_filenames", []))
    candidate_evidence = sorted(list(set(candidate_evidence)))

    validated_paths, _, att_warnings = validate_and_prepare_attachments(
        evidence_filenames=candidate_evidence,
        report_id=report.id if report else None,
        max_mb=settings.complaint_max_attachment_mb,
    )
    actual_attached_filenames = [p.name for p in validated_paths]
    for aw in att_warnings:
        if aw not in draft_warnings:
            draft_warnings.append(aw)

    timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 11. Plain text body
    body_text_lines = [
        f"Dear {platform_addressee},",
        "",
    ]
    if is_manual_inquiry:
        body_text_lines.extend([
            f"I am writing to request a manual delivery verification review regarding my recent delivery of {product_name}.",
            "Automated package verification checks were inconclusive, and no automated defect could be confirmed.",
            "I kindly request human review by your customer support team.",
        ])
    else:
        body_text_lines.extend([
            f"I am writing to formally report an issue regarding my recent delivery of {product_name}.",
        ])

    body_text_lines.extend([
        "",
        "DELIVERY & PRODUCT DETAILS:",
        f"- Product Name: {product_name}",
        f"- Platform/Store: {platform_display}",
        f"- Product URL: {product_url}",
        f"- Expected Quantity: {expected_qty}",
    ])
    if variant:
        body_text_lines.append(f"- Model / Variant: {variant}")
    if order and order.order_id:
        body_text_lines.append(f"- Verified Order ID: {order.order_id}")
    if order and order.tracking_id:
        body_text_lines.append(f"- Verified Tracking/AWB: {order.tracking_id}")
    body_text_lines.append(f"- Verification Session ID: {v_session.id}")

    if report and report.id:
        body_text_lines.append(f"- Verification Report ID: {report.id}")
    else:
        body_text_lines.append("- Verification Report: None generated")

    body_text_lines.append(f"- Date of Verification: {timestamp_str}")
    body_text_lines.append("")

    if is_manual_inquiry:
        body_text_lines.append("AUTOMATED VERIFICATION STATUS:")
    else:
        body_text_lines.append("SUMMARY OF DETECTED ISSUES:")

    body_text_lines.extend(issue_lines)

    if eligibility.reasons:
        body_text_lines.append("")
        body_text_lines.append("TECHNICAL FINDINGS:")
        for r in eligibility.reasons:
            body_text_lines.append(f"- {r}")

    if draft_warnings:
        body_text_lines.append("")
        body_text_lines.append("SYSTEM WARNINGS & NOTICES:")
        for w in draft_warnings:
            body_text_lines.append(f"- {w}")

    if payload.custom_notes:
        body_text_lines.extend([
            "",
            "CUSTOMER REMARKS:",
            payload.custom_notes.strip(),
        ])

    body_text_lines.extend([
        "",
        "REQUESTED RESOLUTION:",
        f"I kindly request: {resolution_text}.",
        "",
    ])

    if actual_attached_filenames:
        body_text_lines.append("ATTACHED EVIDENCE FILES:")
        for fn in actual_attached_filenames:
            body_text_lines.append(f"- {fn}")
        body_text_lines.append("")
        body_text_lines.append("Please review the attached evidence files and confirm receipt at your earliest convenience.")
    else:
        body_text_lines.append("ATTACHED EVIDENCE: None")
        body_text_lines.append("")
        body_text_lines.append("No evidence files are attached. Please review the details above and confirm receipt at your earliest convenience.")

    body_text_lines.extend([
        "",
        "Sincerely,",
        customer_name,
        "",
        "---",
        "SYSTEM DISCLAIMER:",
    ])
    body_text_lines.extend(disclaimers)
    body_text = "\n".join(body_text_lines)

    # 12. HTML Body
    body_html_parts = [
        f"<p>Dear <strong>{html.escape(platform_addressee)}</strong>,</p>",
    ]
    if is_manual_inquiry:
        body_html_parts.extend([
            f"<p>I am writing to request a manual delivery verification review regarding my recent delivery of <strong>{html.escape(product_name)}</strong>.</p>",
            "<p>Automated package verification checks were inconclusive, and no automated defect could be confirmed. I kindly request human review by your customer support team.</p>",
        ])
    else:
        body_html_parts.append(
            f"<p>I am writing to formally report an issue regarding my recent delivery of <strong>{html.escape(product_name)}</strong>.</p>"
        )

    body_html_parts.extend([
        "<hr style='border: 0; border-top: 1px solid #cbd5e1; margin: 16px 0;'>",
        "<h3 style='color: #0f172a; margin-bottom: 8px;'>Delivery & Product Details</h3>",
        "<table style='width: 100%; border-collapse: collapse; font-size: 14px; margin-bottom: 16px;'>",
        f"<tr><td style='padding: 4px 8px; font-weight: bold;'>Product:</td><td style='padding: 4px 8px;'>{html.escape(product_name)}</td></tr>",
        f"<tr><td style='padding: 4px 8px; font-weight: bold;'>Platform:</td><td style='padding: 4px 8px;'>{html.escape(platform_display)}</td></tr>",
        f"<tr><td style='padding: 4px 8px; font-weight: bold;'>Product URL:</td><td style='padding: 4px 8px;'><a href='{html.escape(product_url)}'>{html.escape(product_url)}</a></td></tr>",
        f"<tr><td style='padding: 4px 8px; font-weight: bold;'>Expected Quantity:</td><td style='padding: 4px 8px;'>{expected_qty}</td></tr>",
    ])
    if variant:
        body_html_parts.append(f"<tr><td style='padding: 4px 8px; font-weight: bold;'>Variant:</td><td style='padding: 4px 8px;'>{html.escape(variant)}</td></tr>")
    if order and order.order_id:
        body_html_parts.append(f"<tr><td style='padding: 4px 8px; font-weight: bold;'>Order ID:</td><td style='padding: 4px 8px;'>{html.escape(order.order_id)}</td></tr>")
    if order and order.tracking_id:
        body_html_parts.append(f"<tr><td style='padding: 4px 8px; font-weight: bold;'>Tracking ID:</td><td style='padding: 4px 8px;'>{html.escape(order.tracking_id)}</td></tr>")
    body_html_parts.append(f"<tr><td style='padding: 4px 8px; font-weight: bold;'>Session ID:</td><td style='padding: 4px 8px;'>{html.escape(v_session.id)}</td></tr>")

    if report and report.id:
        body_html_parts.append(f"<tr><td style='padding: 4px 8px; font-weight: bold;'>Report ID:</td><td style='padding: 4px 8px;'>{html.escape(report.id)}</td></tr>")
    else:
        body_html_parts.append("<tr><td style='padding: 4px 8px; font-weight: bold;'>Report:</td><td style='padding: 4px 8px;'>None generated</td></tr>")

    body_html_parts.append(f"<tr><td style='padding: 4px 8px; font-weight: bold;'>Verified:</td><td style='padding: 4px 8px;'>{html.escape(timestamp_str)}</td></tr>")
    body_html_parts.append("</table>")

    if is_manual_inquiry:
        body_html_parts.append("<h3 style='color: #475569; margin-bottom: 8px;'>Automated Verification Status</h3><ul>")
    else:
        body_html_parts.append("<h3 style='color: #b91c1c; margin-bottom: 8px;'>Detected Delivery Issues</h3><ul>")

    for issue_item in issue_lines:
        body_html_parts.append(f"<li>{html.escape(issue_item)}</li>")
    body_html_parts.append("</ul>")

    if eligibility.reasons:
        body_html_parts.append("<h4 style='color: #334155; margin-top: 12px; margin-bottom: 6px;'>Technical Findings</h4><ul>")
        for r in eligibility.reasons:
            body_html_parts.append(f"<li>{html.escape(r)}</li>")
        body_html_parts.append("</ul>")

    if draft_warnings:
        body_html_parts.append("<h4 style='color: #b45309; margin-top: 12px; margin-bottom: 6px;'>Warnings & Notices</h4><ul>")
        for w in draft_warnings:
            body_html_parts.append(f"<li>{html.escape(w)}</li>")
        body_html_parts.append("</ul>")

    if payload.custom_notes:
        body_html_parts.append(f"<h4 style='color: #334155; margin-top: 12px; margin-bottom: 6px;'>Customer Notes</h4><p>{_sanitize_html(payload.custom_notes.strip())}</p>")

    body_html_parts.append(f"<p style='margin-top: 16px;'><strong>Requested Resolution:</strong> {html.escape(resolution_text)}.</p>")

    if actual_attached_filenames:
        body_html_parts.append("<h4 style='color: #334155; margin-top: 12px; margin-bottom: 6px;'>Attached Evidence</h4><ul>")
        for fn in actual_attached_filenames:
            body_html_parts.append(f"<li>{html.escape(fn)}</li>")
        body_html_parts.append("</ul>")
        body_html_parts.append("<p>Please review the attached evidence files and confirm receipt at your earliest convenience.</p>")
    else:
        body_html_parts.append("<p style='margin-top: 8px; color: #64748b;'><em>Attached Evidence: None</em></p>")
        body_html_parts.append("<p>No evidence files are attached. Please review the details above and confirm receipt at your earliest convenience.</p>")

    body_html_parts.append(f"<p>Sincerely,<br><strong>{html.escape(customer_name)}</strong></p>")
    body_html_parts.append("<hr style='border: 0; border-top: 1px solid #cbd5e1; margin: 16px 0;'><div style='font-size: 11px; color: #64748b;'><p><strong>Disclaimers:</strong></p><ul>")
    for d in disclaimers:
        body_html_parts.append(f"<li>{html.escape(d)}</li>")
    body_html_parts.append("</ul></div>")

    body_html = "".join(body_html_parts)

    draft_id = existing_draft.id if existing_draft else uuid4().hex
    if existing_draft:
        existing_draft.subject = subject
        existing_draft.body_text = body_text
        existing_draft.body_html = body_html
        existing_draft.requested_resolution = payload.requested_resolution.value
        existing_draft.issue_types = eligibility.issue_types
        existing_draft.confidence_summary = eligibility.confidence_values
        existing_draft.attached_evidence_filenames = actual_attached_filenames
        existing_draft.disclaimers = disclaimers
        existing_draft.manual_inquiry = is_manual_inquiry
        existing_draft.warnings = draft_warnings
        if platform_contradiction:
            existing_draft.intended_platform = None
            existing_draft.destination_confirmed = False
            existing_draft.recipient_email = ""
        else:
            existing_draft.recipient_email = recipient
        existing_draft.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing_draft)
        return existing_draft

    draft = ComplaintDraft(
        id=draft_id,
        submission_id=submission.id if submission else None,
        verification_session_id=payload.verification_session_id,
        order_id=order.id if order else None,
        report_id=report.id if report else None,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        recipient_email="" if platform_contradiction else recipient,
        requested_resolution=payload.requested_resolution.value,
        issue_types=eligibility.issue_types,
        confidence_summary=eligibility.confidence_values,
        attached_evidence_filenames=actual_attached_filenames,
        disclaimers=disclaimers,
        manual_inquiry=is_manual_inquiry,
        warnings=draft_warnings,
        intended_platform=None,
        destination_confirmed=False,
        delivery_status="draft",
        delivery_attempts=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def get_draft(db: Session, draft_id: str) -> Optional[ComplaintDraft]:
    """Retrieve complaint draft by ID."""
    return db.query(ComplaintDraft).filter(ComplaintDraft.id == draft_id).first()


def update_draft(
    db: Session,
    draft_id: str,
    payload: ComplaintDraftUpdate,
) -> ComplaintDraft:
    """Update editable complaint draft fields (subject, body, recipient, requested resolution, platform destination)."""
    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Complaint draft '{draft_id}' not found.")

    has_contra, cust_plat, order_plat = check_platform_contradiction(db, draft)

    # Handle explicit platform contradiction resolution
    if payload.confirmed_destination is True or payload.intended_platform is not None:
        intended = (payload.intended_platform or draft.intended_platform or "").strip()
        if not intended:
            raise HTTPException(
                status_code=400,
                detail="Resolving platform contradiction requires specifying 'intended_platform' (e.g. 'Amazon' or 'Flipkart').",
            )
        if payload.confirmed_destination is not True:
            raise HTTPException(
                status_code=400,
                detail="Resolving platform contradiction requires 'confirmed_destination': true.",
            )
        target_email = payload.recipient_email if payload.recipient_email is not None else draft.recipient_email
        if is_placeholder_or_invalid_email(target_email):
            raise HTTPException(
                status_code=400,
                detail="Resolving platform contradiction requires a valid production 'recipient_email' (cannot be empty or @example.com).",
            )

        draft.intended_platform = intended.title()
        draft.destination_confirmed = True
        draft.recipient_email = target_email.strip().lower()

        # Update warnings: remove platform contradiction warning and append resolution note
        filtered_warnings = [
            w for w in (draft.warnings or [])
            if "Platform contradiction detected" not in w
        ]
        resolution_notice = (
            f"Platform contradiction resolved: Confirmed intended platform '{draft.intended_platform}' "
            f"with recipient email '{draft.recipient_email}'."
        )
        if resolution_notice not in filtered_warnings:
            filtered_warnings.append(resolution_notice)
        draft.warnings = filtered_warnings

        # Update addressee in body text and html if generic placeholder was present
        if "Dear Customer Support Team," in draft.body_text:
            draft.body_text = draft.body_text.replace(
                "Dear Customer Support Team,",
                f"Dear {draft.intended_platform} Customer Support Team,",
            )
        if "Dear <strong>Customer Support Team</strong>," in draft.body_html:
            draft.body_html = draft.body_html.replace(
                "Dear <strong>Customer Support Team</strong>,",
                f"Dear <strong>{html.escape(draft.intended_platform)} Customer Support Team</strong>,",
            )
    else:
        if payload.intended_platform is not None:
            draft.intended_platform = payload.intended_platform.strip().title() if payload.intended_platform else None
        if payload.confirmed_destination is False:
            draft.destination_confirmed = False

    if payload.subject is not None:
        draft.subject = payload.subject.strip()
    if payload.body_text is not None:
        draft.body_text = payload.body_text
    if payload.body_html is not None:
        draft.body_html = payload.body_html
    if payload.recipient_email is not None and not (payload.confirmed_destination is True or payload.intended_platform is not None):
        draft.recipient_email = payload.recipient_email.strip().lower()
    if payload.requested_resolution is not None:
        draft.requested_resolution = payload.requested_resolution.value

    draft.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(draft)
    return draft


async def send_complaint(
    db: Session,
    draft_id: str,
    confirmed: bool,
    idempotency_key: Optional[str] = None,
) -> ComplaintSendResponse:
    """Transmit complaint email using configured mail provider.

    Requires explicit confirmed=True parameter.
    Validates attachments, prevents path traversal, and enforces idempotency.
    """
    if not confirmed:
        raise HTTPException(
            status_code=400,
            detail="Sending rejected: Explicit customer confirmation is required (confirmed=true).",
        )

    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Complaint draft '{draft_id}' not found.")

    # 1. Platform contradiction validation: reject if contradiction is unresolved
    has_contra, cust_plat, order_plat = check_platform_contradiction(db, draft)
    if has_contra and (not draft.destination_confirmed or not draft.intended_platform):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Sending rejected: A platform contradiction exists between customer platform ('{cust_plat.title()}') "
                f"and order platform ('{order_plat.title()}'). "
                "The destination is unresolved. Please perform an explicit PATCH providing 'intended_platform', "
                "'confirmed_destination': true, and a valid production 'recipient_email' before sending."
            ),
        )

    # 2. Check for unresolved, invalid, or placeholder (@example.com) recipient email
    if is_placeholder_or_invalid_email(draft.recipient_email):
        raise HTTPException(
            status_code=400,
            detail="Sending rejected: Recipient email is unresolved or a placeholder address (e.g. @example.com). Please update the draft with a confirmed production recipient email before sending.",
        )

    # Idempotency protection against rapid repeated button clicks
    if idempotency_key and draft.idempotency_key == idempotency_key:
        if draft.delivery_status in ("sent", "dry_run"):
            return ComplaintSendResponse(
                draft_id=draft.id,
                delivery_status=draft.delivery_status,
                recipient_email=draft.recipient_email,
                message=f"Duplicate request ignored via idempotency key; complaint was already processed ({draft.delivery_status}).",
                delivery_attempt=draft.delivery_attempts[-1] if draft.delivery_attempts else {},
            )

    # Basic rate limiting: reject repeat sends within 5 seconds for the same draft
    if draft.sent_at:
        seconds_since = (datetime.now(timezone.utc) - draft.sent_at.replace(tzinfo=timezone.utc)).total_seconds()
        if seconds_since < 5.0 and draft.delivery_status in ("sent", "dry_run"):
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded: Complaint was already dispatched moments ago. Please wait before retrying.",
            )

    # Prepare secure attachments
    validated_paths, total_bytes, warnings = validate_and_prepare_attachments(
        evidence_filenames=draft.attached_evidence_filenames or [],
        report_id=draft.report_id,
        max_mb=settings.complaint_max_attachment_mb,
    )

    mail_provider = get_mail_provider()
    result = await mail_provider.send_complaint(
        recipient_email=draft.recipient_email,
        subject=draft.subject,
        body_text=draft.body_text,
        body_html=draft.body_html,
        attachment_paths=validated_paths,
    )

    attempt_record = result.model_dump(mode="json")
    if warnings:
        attempt_record["warnings"] = warnings

    attempts = list(draft.delivery_attempts or [])
    attempts.append(attempt_record)
    draft.delivery_attempts = attempts

    if result.success:
        draft.delivery_status = result.status
        draft.sent_at = datetime.now(timezone.utc)
        if idempotency_key:
            draft.idempotency_key = idempotency_key
    else:
        draft.delivery_status = "failed"

    draft.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(draft)

    return ComplaintSendResponse(
        draft_id=draft.id,
        delivery_status=draft.delivery_status,
        recipient_email=draft.recipient_email,
        message=result.message,
        delivery_attempt=attempt_record,
    )
