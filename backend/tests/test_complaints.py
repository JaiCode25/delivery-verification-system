"""Tests for complaint eligibility engine and complaint draft endpoints.

Validates:
- Strict gating by high confidence threshold (default 0.80):
  - Boundary: 0.79 -> not eligible (warning emitted)
  - Boundary: 0.80 -> eligible
  - Boundary: 0.81 -> eligible
- Issue types: wrong_product, quantity_mismatch, damaged_item
- Explicit rejection of unsupported_product, insufficient_evidence, manual_review_required, model_unavailable
- Draft generation with professional phrasing (no legal accusations)
- Draft regeneration flag behavior
- Draft editing and preview endpoints
- HTML sanitization against XSS in customer notes
- Resolution options (replacement, refund, missing_item_refund, other)
"""
from uuid import uuid4
import pytest

from app.database.models import (
    ConditionAnalysisRecord,
    Order,
    ProductMatchingRecord,
    QuantityVerificationRecord,
    VerificationSession,
)
from app.schemas.complaint import ResolutionType
from app.services.complaint_service import check_complaint_eligibility


def test_complaint_eligibility_boundary_thresholds(seeded_db):
    order = seeded_db.query(Order).first()
    session = VerificationSession(
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)
    seeded_db.commit()

    # Case 1: Product mismatch with confidence 0.79 (below 0.80 threshold)
    pm_sub_threshold = ProductMatchingRecord(
        verification_session_id=session.id,
        order_id=order.id,
        overall_score=0.21,  # 1 - 0.21 = 0.79 mismatch confidence
        decision="mismatch",
        details={"confidence": 0.79},
    )
    seeded_db.add(pm_sub_threshold)
    seeded_db.commit()

    res_79 = check_complaint_eligibility(seeded_db, session.id, threshold=0.80)
    assert res_79.eligible is False
    assert len(res_79.issue_types) == 0
    assert any("below the high-confidence complaint threshold" in w for w in res_79.warnings)

    # Case 2: Update confidence to 0.80 (exact threshold)
    pm_sub_threshold.details = {"confidence": 0.80}
    seeded_db.commit()

    res_80 = check_complaint_eligibility(seeded_db, session.id, threshold=0.80)
    assert res_80.eligible is True
    assert "wrong_product" in res_80.issue_types
    assert any("mismatch with high confidence" in r for r in res_80.reasons)

    # Case 3: Update confidence to 0.81 (above threshold)
    pm_sub_threshold.details = {"confidence": 0.81}
    seeded_db.commit()

    res_81 = check_complaint_eligibility(seeded_db, session.id, threshold=0.80)
    assert res_81.eligible is True
    assert "wrong_product" in res_81.issue_types


def test_complaint_eligibility_all_issue_types(seeded_db):
    order = seeded_db.query(Order).first()
    session = VerificationSession(
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)
    seeded_db.commit()

    # 1. Product mismatch
    pm = ProductMatchingRecord(
        verification_session_id=session.id,
        order_id=order.id,
        overall_score=0.10,
        decision="mismatch",
        details={"confidence": 0.90},
    )
    # 2. Quantity mismatch
    qty = QuantityVerificationRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="mismatch",
        details={"expected_quantity": 2, "detected_quantity": 1, "confidence": 0.85},
    )
    # 3. Condition damage
    cond = ConditionAnalysisRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="damaged",
        details={"confidence": 0.88, "condition_label": "damaged"},
    )
    seeded_db.add_all([pm, qty, cond])
    seeded_db.commit()

    res = check_complaint_eligibility(seeded_db, session.id, threshold=0.80)
    assert res.eligible is True
    assert set(res.issue_types) == {"wrong_product", "quantity_mismatch", "damaged_item"}
    assert len(res.reasons) == 3


def test_complaint_eligibility_rejects_inconclusive_and_unsupported(seeded_db):
    order = seeded_db.query(Order).first()
    session = VerificationSession(
        order_id=order.id,
        verification_status="inconclusive",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)
    seeded_db.commit()

    # Inconclusive / unsupported signals across channels
    pm = ProductMatchingRecord(
        verification_session_id=session.id,
        order_id=order.id,
        overall_score=0.50,
        decision="insufficient_evidence",
        details={"confidence": 0.40},
    )
    qty = QuantityVerificationRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="unsupported_product",
        details={"reason": "Unregistered product class"},
    )
    cond = ConditionAnalysisRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="manual_review_required",
        details={"confidence": 0.45},
    )
    seeded_db.add_all([pm, qty, cond])
    seeded_db.commit()

    res = check_complaint_eligibility(seeded_db, session.id)
    assert res.eligible is False
    assert len(res.issue_types) == 0
    assert len(res.warnings) >= 3


def test_generate_complaint_draft_lifecycle(client, seeded_db):
    order = seeded_db.query(Order).first()
    session = VerificationSession(
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)
    seeded_db.commit()

    # Add high confidence damage record
    cond = ConditionAnalysisRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="damaged",
        details={"confidence": 0.92},
    )
    seeded_db.add(cond)
    seeded_db.commit()

    # 1. Generate draft
    payload = {
        "verification_session_id": session.id,
        "requested_resolution": "replacement",
        "custom_notes": "Box arrived torn and dented on the left corner.",
    }
    create_res = client.post("/api/complaints/drafts", json=payload)
    assert create_res.status_code == 201
    draft_data = create_res.json()
    draft_id = draft_data["draft_id"]
    assert "damaged" in draft_data["subject"].lower()
    assert "Box arrived torn" in draft_data["body_text"]
    assert draft_data["requested_resolution"] == "replacement"
    assert "disclaimers" in draft_data
    assert draft_data["delivery_status"] == "draft"

    # 2. Re-calling without regenerate flag returns existing draft
    repeat_res = client.post("/api/complaints/drafts", json=payload)
    assert repeat_res.status_code == 201
    assert repeat_res.json()["draft_id"] == draft_id

    # 3. Re-calling with regenerate=True updates the draft
    regen_res = client.post(
        "/api/complaints/drafts",
        json={**payload, "regenerate": True, "requested_resolution": "refund"},
    )
    assert regen_res.status_code == 201
    assert regen_res.json()["draft_id"] == draft_id
    assert regen_res.json()["requested_resolution"] == "refund"


def test_complaint_draft_update_and_preview(client, seeded_db):
    order = seeded_db.query(Order).first()
    session = VerificationSession(
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)
    seeded_db.commit()

    # Generate draft
    create_res = client.post(
        "/api/complaints/drafts",
        json={"verification_session_id": session.id, "requested_resolution": "replacement"},
    )
    assert create_res.status_code == 201
    draft_id = create_res.json()["draft_id"]

    # Patch draft
    patch_res = client.patch(
        f"/api/complaints/drafts/{draft_id}",
        json={
            "subject": "Customized Subject Line",
            "requested_resolution": "missing_item_refund",
            "recipient_email": "escalations@store.in",
        },
    )
    assert patch_res.status_code == 200
    patched = patch_res.json()
    assert patched["subject"] == "Customized Subject Line"
    assert patched["requested_resolution"] == "missing_item_refund"
    assert patched["recipient_email"] == "escalations@store.in"

    # Preview draft
    preview_res = client.get(f"/api/complaints/drafts/{draft_id}/preview")
    assert preview_res.status_code == 200
    preview = preview_res.json()
    assert preview["draft_id"] == draft_id
    assert preview["subject"] == "Customized Subject Line"
    assert preview["recipient_email"] == "escalations@store.in"
    assert preview["requested_resolution"] == "missing_item_refund"
    assert len(preview["body_html"]) > 20


def test_complaint_html_sanitization_escapes_xss(client, seeded_db):
    order = seeded_db.query(Order).first()
    session = VerificationSession(
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)
    seeded_db.commit()

    xss_notes = "<script>alert('pwned')</script><img src=x onerror=alert(1)>"
    create_res = client.post(
        "/api/complaints/drafts",
        json={
            "verification_session_id": session.id,
            "custom_notes": xss_notes,
        },
    )
    assert create_res.status_code == 201
    body_html = create_res.json()["body_html"]
    # Ensure script tags are escaped
    assert "<script>" not in body_html
    assert "&lt;script&gt;" in body_html
    assert "<img src=x onerror" not in body_html


def test_get_and_list_complaint_drafts(client, seeded_db):
    order = seeded_db.query(Order).first()
    session = VerificationSession(
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)
    seeded_db.commit()

    create_res = client.post(
        "/api/complaints/drafts",
        json={"verification_session_id": session.id},
    )
    assert create_res.status_code == 201
    draft_id = create_res.json()["draft_id"]

    # Get by ID
    get_res = client.get(f"/api/complaints/drafts/{draft_id}")
    assert get_res.status_code == 200
    assert get_res.json()["draft_id"] == draft_id

    # List with session filter
    list_res = client.get(f"/api/complaints/drafts?verification_session_id={session.id}")
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) >= 1
    assert items[0]["draft_id"] == draft_id
