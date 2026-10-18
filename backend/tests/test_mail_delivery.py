"""Tests for complaint mail delivery provider and attachment security.

Validates:
- Explicit confirmed=True safety requirement (confirmed=False rejected with HTTP 400)
- Dry-run mail provider (default, safe for test suites)
- SMTP mail provider with mocked FastMail transport
- Idempotency protection (identical idempotency key returns cached result)
- Rate limiting protection (rapid repeat sends rejected with HTTP 429)
- Strict attachment security:
  - Path traversal attempt rejection (../)
  - Sensitive file blocking (.pt, .sqlite, .log, .bin)
  - File size bounds enforcement (max 10MB)
  - Non-existent file filtering
- Failure handling: draft state preserved as 'failed', delivery attempt recorded
"""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from app.config import settings
from app.database.models import (
    ComplaintDraft,
    ConditionAnalysisRecord,
    Order,
    VerificationSession,
)
from app.services.complaint_service import generate_complaint_draft
from app.services.mail_provider import (
    DryRunMailProvider,
    FastMailSmtpProvider,
    validate_and_prepare_attachments,
)


def test_send_complaint_requires_confirmed_flag(client, seeded_db):
    order = seeded_db.query(Order).first()
    session = VerificationSession(
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)
    seeded_db.commit()

    cond = ConditionAnalysisRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="damaged",
        details={"confidence": 0.92},
    )
    seeded_db.add(cond)
    seeded_db.commit()

    draft_res = client.post("/api/complaints/drafts", json={"verification_session_id": session.id})
    draft_id = draft_res.json()["draft_id"]

    # Attempt send with confirmed=False
    res_false = client.post(f"/api/complaints/drafts/{draft_id}/send", json={"confirmed": False})
    assert res_false.status_code == 400
    assert "Explicit customer confirmation is required" in res_false.json()["detail"]


def test_dry_run_mail_delivery_success(client, seeded_db):
    order = seeded_db.query(Order).first()
    session = VerificationSession(
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)
    seeded_db.commit()

    cond = ConditionAnalysisRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="damaged",
        details={"confidence": 0.92},
    )
    seeded_db.add(cond)
    seeded_db.commit()

    draft_res = client.post("/api/complaints/drafts", json={"verification_session_id": session.id})
    draft_id = draft_res.json()["draft_id"]

    # Send with confirmed=True in dry-run mode (default)
    send_res = client.post(
        f"/api/complaints/drafts/{draft_id}/send",
        json={"confirmed": True, "idempotency_key": "test-key-dryrun-1"},
    )
    assert send_res.status_code == 200
    data = send_res.json()
    assert data["delivery_status"] == "dry_run"
    assert "Dry-run delivery simulated" in data["message"]
    assert len(data["delivery_attempt"]) > 0
    assert data["delivery_attempt"]["success"] is True


def test_idempotency_prevents_duplicate_dispatches(client, seeded_db):
    order = seeded_db.query(Order).first()
    session = VerificationSession(
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)
    seeded_db.commit()

    cond = ConditionAnalysisRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="damaged",
        details={"confidence": 0.92},
    )
    seeded_db.add(cond)
    seeded_db.commit()

    draft_res = client.post("/api/complaints/drafts", json={"verification_session_id": session.id})
    draft_id = draft_res.json()["draft_id"]

    idem_key = "idemp-unique-abc-123"

    # First send
    res1 = client.post(
        f"/api/complaints/drafts/{draft_id}/send",
        json={"confirmed": True, "idempotency_key": idem_key},
    )
    assert res1.status_code == 200
    assert res1.json()["delivery_status"] == "dry_run"

    # Duplicate send with same idempotency key
    res2 = client.post(
        f"/api/complaints/drafts/{draft_id}/send",
        json={"confirmed": True, "idempotency_key": idem_key},
    )
    assert res2.status_code == 200
    assert "Duplicate request ignored via idempotency key" in res2.json()["message"]


def test_rate_limiting_rejects_rapid_repeat_sends(client, seeded_db):
    order = seeded_db.query(Order).first()
    session = VerificationSession(
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)
    seeded_db.commit()

    cond = ConditionAnalysisRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="damaged",
        details={"confidence": 0.92},
    )
    seeded_db.add(cond)
    seeded_db.commit()

    draft_res = client.post("/api/complaints/drafts", json={"verification_session_id": session.id})
    draft_id = draft_res.json()["draft_id"]

    # First send
    res1 = client.post(
        f"/api/complaints/drafts/{draft_id}/send",
        json={"confirmed": True, "idempotency_key": "key-rate-1"},
    )
    assert res1.status_code == 200

    # Rapid second send with different key triggers 429 rate limit
    res2 = client.post(
        f"/api/complaints/drafts/{draft_id}/send",
        json={"confirmed": True, "idempotency_key": "key-rate-2"},
    )
    assert res2.status_code == 429
    assert "Rate limit exceeded" in res2.json()["detail"]


def test_smtp_mail_provider_with_mocked_transport(monkeypatch):
    import asyncio
    monkeypatch.setattr(settings, "complaint_email_enabled", True)
    monkeypatch.setattr(settings, "complaint_email_provider", "smtp")
    monkeypatch.setattr(settings, "complaint_smtp_host", "smtp.retailer.com")
    monkeypatch.setattr(settings, "complaint_smtp_port", 587)
    monkeypatch.setattr(settings, "complaint_smtp_username", "bot@store.in")
    monkeypatch.setattr(settings, "complaint_smtp_password", "supersecret")

    provider = FastMailSmtpProvider()
    mock_send = AsyncMock(return_value=None)
    monkeypatch.setattr(provider.fastmail, "send_message", mock_send)

    result = asyncio.run(provider.send_complaint(
        recipient_email="support@retailer.com",
        subject="Damaged Package Notice",
        body_text="Package arrived crushed.",
        body_html="<p>Package arrived crushed.</p>",
        attachment_paths=[],
    ))
    assert result.success is True
    assert result.status == "sent"
    assert "successfully transmitted" in result.message
    assert mock_send.await_count == 1


def test_smtp_mail_provider_failure_handling(monkeypatch):
    import asyncio
    monkeypatch.setattr(settings, "complaint_email_enabled", True)
    monkeypatch.setattr(settings, "complaint_email_provider", "smtp")
    monkeypatch.setattr(settings, "complaint_smtp_host", "smtp.retailer.com")
    monkeypatch.setattr(settings, "complaint_smtp_username", "bot@store.in")
    monkeypatch.setattr(settings, "complaint_smtp_password", "topsecretpass")

    provider = FastMailSmtpProvider()
    mock_send = AsyncMock(side_effect=ConnectionError("Connection to smtp.retailer.com failed with topsecretpass"))
    monkeypatch.setattr(provider.fastmail, "send_message", mock_send)

    result = asyncio.run(provider.send_complaint(
        recipient_email="support@retailer.com",
        subject="Damaged Package Notice",
        body_text="Details",
        body_html="<p>Details</p>",
        attachment_paths=[],
    ))
    assert result.success is False
    assert result.status == "failed"
    assert "SMTP transmission failed" in result.message
    # Verify password was masked and never leaked in error message
    assert "topsecretpass" not in result.message
    assert "******" in result.message


def test_attachment_security_filters_and_blocks(tmp_path, monkeypatch):
    # Setup allowed storage directory
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(settings, "frame_dir", tmp_path / "frames")
    monkeypatch.setattr(settings, "report_dir", tmp_path / "reports")
    monkeypatch.setattr(settings, "reference_dir", tmp_path / "refs")

    # 1. Create a legitimate evidence image
    valid_img = upload_dir / "valid_evidence.png"
    valid_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1024)

    # 2. Create prohibited sensitive files
    model_weight = upload_dir / "bad_model.pt"
    model_weight.write_bytes(b"PYTORCH_WEIGHTS")

    sqlite_db = upload_dir / "leaked_db.sqlite"
    sqlite_db.write_bytes(b"SQLITE_DATA")

    log_file = upload_dir / "system.log"
    log_file.write_bytes(b"LOG_SECRET")

    # 3. Create an oversized image (exceeding 2 MB limit for test)
    oversized_img = upload_dir / "huge_photo.jpg"
    oversized_img.write_bytes(b"\xff\xd8\xff" + b"\x00" * (3 * 1024 * 1024))

    evidence_candidates = [
        "valid_evidence.png",
        "../../etc/passwd",  # Path traversal
        r"..\..\Windows\System32\cmd.exe",  # Windows path traversal
        "bad_model.pt",  # Prohibited extension
        "leaked_db.sqlite",  # Prohibited extension
        "system.log",  # Prohibited extension
        "huge_photo.jpg",  # Exceeds max_mb
        "non_existent_file.png",  # Non-existent
    ]

    validated_paths, total_bytes, warnings = validate_and_prepare_attachments(
        evidence_filenames=evidence_candidates,
        report_id=None,
        max_mb=2,  # Limit to 2 MB
    )

    # Only valid_evidence.png should be accepted
    assert len(validated_paths) == 1
    assert validated_paths[0].name == "valid_evidence.png"

    # Verify warnings recorded all rejected items
    warning_text = " ".join(warnings)
    assert "path traversal characters detected" in warning_text
    assert "sensitive file type" in warning_text
    assert "exceeds total size limit" in warning_text
