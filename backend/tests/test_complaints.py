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
    CustomerProductSubmission,
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

    # Add high confidence damage record to qualify for draft
    cond = ConditionAnalysisRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="damaged",
        details={"confidence": 0.92},
    )
    seeded_db.add(cond)
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
    assert preview["eligible"] is True
    assert preview["manual_inquiry"] is False
    assert "damaged_item" in preview["issue_types"]
    assert isinstance(preview["warnings"], list)
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

    cond = ConditionAnalysisRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="damaged",
        details={"confidence": 0.92},
    )
    seeded_db.add(cond)
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

    cond = ConditionAnalysisRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="damaged",
        details={"confidence": 0.92},
    )
    seeded_db.add(cond)
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
    assert get_res.json()["eligible"] is True
    assert get_res.json()["manual_inquiry"] is False

    # List with session filter
    list_res = client.get(f"/api/complaints/drafts?verification_session_id={session.id}")
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) >= 1
    assert items[0]["draft_id"] == draft_id


def test_observed_case_safeguards_regression(client, seeded_db):
    """Reproduces observed test case ec2fda8519474c0ca4b18ec5b5914b30:
    - Customer platform: Amazon
    - Verified order platform: Flipkart
    - Product: match (score 0.804)
    - Quantity: unsupported_product
    - Condition: no_visible_damage
    - Report ID: None

    Verifies:
    1. Eligibility is False, issue_types is empty.
    2. POST /api/complaints/drafts is rejected with HTTP 409 by default.
    3. 409 detail contains eligibility result, warnings, reasons, and hint.
    4. Passing allow_manual_inquiry=True generates a non-accusatory manual inquiry draft.
    5. Subject is 'Manual Delivery Verification Inquiry – [Product]'.
    6. Does NOT claim damaged or incorrect merchandise.
    7. Requests human review and phrases resolution conditionally.
    8. Platform contradiction leaves recipient unresolved and emits warning.
    9. Unresolved recipient blocks sending with HTTP 400.
    10. Report is not claimed to be attached.
    11. Disclaimers do not claim evidence audit for data integrity.
    """
    order = seeded_db.query(Order).first()
    order.platform = "flipkart"
    seeded_db.commit()

    observed_session_id = "ec2fda8519474c0ca4b18ec5b5914b30"
    session = VerificationSession(
        id=observed_session_id,
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)

    # Customer submission specifying Amazon
    submission = CustomerProductSubmission(
        id=uuid4().hex,
        verification_session_id=session.id,
        order_id=order.id,
        product_name="Logitech G502 HERO Gaming Mouse",
        platform="amazon",
        product_url="https://www.amazon.in/dp/B07GBZ4Q68",
        normalized_product_url="https://www.amazon.in/dp/B07GBZ4Q68",
        expected_quantity=1,
        customer_name="Jane Doe",
        customer_email="jane.doe@example.com",
    )
    seeded_db.add(submission)

    # Recorded analysis signals: product match, unsupported quantity, no visible damage
    pm = ProductMatchingRecord(
        verification_session_id=session.id,
        order_id=order.id,
        overall_score=0.804,
        decision="match",
        details={"confidence": 0.804},
    )
    qty = QuantityVerificationRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="unsupported_product",
        details={"reason": "detection model unsupported"},
    )
    cond = ConditionAnalysisRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="no_visible_damage",
        details={"confidence": 0.95},
    )
    seeded_db.add_all([pm, qty, cond])
    seeded_db.commit()

    # 1. Eligibility check endpoint
    elig_res = client.get(f"/api/complaints/eligibility/{observed_session_id}")
    assert elig_res.status_code == 200
    elig_data = elig_res.json()
    assert elig_data["eligible"] is False
    assert elig_data["issue_types"] == []
    assert len(elig_data["warnings"]) >= 2

    # 2. Rejection by default with HTTP 409
    draft_res_409 = client.post(
        "/api/complaints/drafts",
        json={"verification_session_id": observed_session_id},
    )
    assert draft_res_409.status_code == 409
    err_detail = draft_res_409.json()["detail"]
    assert err_detail["eligible"] is False
    assert err_detail["issue_types"] == []
    assert len(err_detail["warnings"]) > 0
    assert isinstance(err_detail["reasons"], list)
    assert "allow_manual_inquiry=true" in err_detail["allow_manual_inquiry_hint"]

    # 3. Explicit manual inquiry allowed
    draft_res_201 = client.post(
        "/api/complaints/drafts",
        json={
            "verification_session_id": observed_session_id,
            "allow_manual_inquiry": True,
            "requested_resolution": "replacement",
        },
    )
    assert draft_res_201.status_code == 201
    draft = draft_res_201.json()
    draft_id = draft["draft_id"]

    assert draft["eligible"] is False
    assert draft["manual_inquiry"] is True
    assert draft["issue_types"] == []
    assert draft["subject"] == "Manual Delivery Verification Inquiry – Logitech G502 HERO Gaming Mouse"
    assert "damaged or incorrect merchandise" not in draft["body_text"].lower()
    assert "damaged or incorrect merchandise" not in draft["body_html"].lower()
    assert "Automated package verification checks were inconclusive" in draft["body_text"]
    assert "If an issue is confirmed after review, I request a replacement" in draft["body_text"]
    assert "If an issue is confirmed after review" in draft["body_html"]

    # Platform contradiction checks
    assert draft["recipient_email"] == ""
    assert any("Platform contradiction detected" in w for w in draft["warnings"])
    assert "Customer Support Team" in draft["body_text"]

    # Report and attachment checks (report_id is None, no files on disk)
    assert draft["report_id"] is None
    assert "- Verification Report: None generated" in draft["body_text"]
    assert "ATTACHED EVIDENCE: None" in draft["body_text"]
    assert "audited for data integrity" not in draft["body_text"]
    assert "audited for data integrity" not in draft["body_html"]

    # 4. Attempt to send with unresolved recipient email fails with HTTP 400
    send_fail = client.post(
        f"/api/complaints/drafts/{draft_id}/send",
        json={"confirmed": True},
    )
    assert send_fail.status_code == 400
    assert "unresolved" in send_fail.json()["detail"].lower()

    # 5. Patching only recipient email without confirming platform still leaves contradiction unresolved
    patch_partial = client.patch(
        f"/api/complaints/drafts/{draft_id}",
        json={"recipient_email": "support@flipkart.com"},
    )
    assert patch_partial.status_code == 200
    assert patch_partial.json()["recipient_email"] == "support@flipkart.com"
    assert patch_partial.json()["destination_confirmed"] is False

    send_still_fail = client.post(
        f"/api/complaints/drafts/{draft_id}/send",
        json={"confirmed": True},
    )
    assert send_still_fail.status_code == 400
    assert "platform contradiction" in send_still_fail.json()["detail"].lower()

    # 6. Explicit confirmed PATCH resolves platform contradiction
    patch_confirmed = client.patch(
        f"/api/complaints/drafts/{draft_id}",
        json={
            "intended_platform": "Flipkart",
            "recipient_email": "support@flipkart.com",
            "confirmed_destination": True,
        },
    )
    assert patch_confirmed.status_code == 200
    assert patch_confirmed.json()["intended_platform"] == "Flipkart"
    assert patch_confirmed.json()["destination_confirmed"] is True
    assert patch_confirmed.json()["recipient_email"] == "support@flipkart.com"

    send_ok = client.post(
        f"/api/complaints/drafts/{draft_id}/send",
        json={"confirmed": True},
    )
    assert send_ok.status_code == 200
    assert send_ok.json()["delivery_status"] == "dry_run"


def test_regenerated_draft_clears_recipient_on_platform_contradiction(client, seeded_db):
    """Test that regenerating an existing draft that had a recipient clears recipient_email

    and rejects sending when platforms contradict, until an explicit confirmed PATCH is executed.
    Reproduces Swagger retest scenario:
    1. Create an order on Flipkart.
    2. Verification session has damage detected (eligible).
    3. Generate initial complaint draft: recipient_email defaults to support@flipkart.com.
    4. Link a CustomerProductSubmission specifying platform Amazon (creating platform contradiction).
    5. Regenerate draft with:
       regenerate=true, allow_manual_inquiry=true, recipient_email="customer-support@example.com"
    6. Verify recipient_email in draft response is "" (empty/unresolved), NOT "customer-support@example.com",
       and NOT old "support@flipkart.com".
    7. Verify send endpoint rejects with HTTP 400 while platform contradiction is unresolved.
    8. Verify attempting to confirm destination with an @example.com email fails with HTTP 400.
    9. Verify explicit confirmed PATCH with intended_platform, confirmed_destination=True, and valid production email allows dry-run sending.
    """
    order = seeded_db.query(Order).first()
    order.platform = "flipkart"
    seeded_db.commit()

    session_id = uuid4().hex
    session = VerificationSession(
        id=session_id,
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session)

    # Initial defect so draft generation succeeds normally
    cond = ConditionAnalysisRecord(
        verification_session_id=session.id,
        order_id=order.id,
        status="damaged",
        details={"confidence": 0.95},
    )
    seeded_db.add(cond)
    seeded_db.commit()

    # Initial formal draft (no customer submission yet, no contradiction)
    draft_init_res = client.post(
        "/api/complaints/drafts",
        json={"verification_session_id": session_id, "requested_resolution": "replacement"},
    )
    assert draft_init_res.status_code == 201
    init_draft = draft_init_res.json()
    assert init_draft["recipient_email"] == "support@flipkart.com"
    draft_id = init_draft["draft_id"]

    # Now customer submits product intake claiming platform is Amazon
    submission = CustomerProductSubmission(
        id=uuid4().hex,
        verification_session_id=session_id,
        order_id=order.id,
        product_name="Rocsil Analog Watch",
        platform="amazon",
        product_url="https://www.amazon.in/dp/B012345678",
        normalized_product_url="https://www.amazon.in/dp/B012345678",
        expected_quantity=1,
        customer_name="Test User",
        customer_email="test.user@customer.in",
    )
    seeded_db.add(submission)
    seeded_db.commit()

    # Regenerate draft as manual inquiry with contradictory platforms and supplied recipient
    regen_res = client.post(
        "/api/complaints/drafts",
        json={
            "verification_session_id": session_id,
            "regenerate": True,
            "allow_manual_inquiry": True,
            "requested_resolution": "replacement",
            "recipient_email": "customer-support@example.com",
        },
    )
    assert regen_res.status_code == 201
    regen_draft = regen_res.json()

    # 1. recipient_email must be cleared to empty/unresolved value
    assert regen_draft["recipient_email"] == ""
    assert regen_draft["manual_inquiry"] is True
    assert regen_draft["destination_confirmed"] is False
    assert any("Platform contradiction detected" in w for w in regen_draft["warnings"])
    assert "Customer Support Team" in regen_draft["body_text"]

    # 2. send endpoint must reject sending with HTTP 400
    send_reject = client.post(
        f"/api/complaints/drafts/{draft_id}/send",
        json={"confirmed": True},
    )
    assert send_reject.status_code == 400
    assert "platform contradiction" in send_reject.json()["detail"].lower() or "unresolved" in send_reject.json()["detail"].lower()

    # 3. Destination confirmation with @example.com address is rejected
    example_patch = client.patch(
        f"/api/complaints/drafts/{draft_id}",
        json={
            "intended_platform": "Amazon",
            "recipient_email": "escalations@example.com",
            "confirmed_destination": True,
        },
    )
    assert example_patch.status_code == 400
    assert "example.com" in example_patch.json()["detail"].lower()

    # 4. Explicit confirmed PATCH with valid production recipient
    patch_ok = client.patch(
        f"/api/complaints/drafts/{draft_id}",
        json={
            "intended_platform": "Amazon",
            "recipient_email": "cs-reply@amazon.in",
            "confirmed_destination": True,
        },
    )
    assert patch_ok.status_code == 200
    patched_data = patch_ok.json()
    assert patched_data["recipient_email"] == "cs-reply@amazon.in"
    assert patched_data["intended_platform"] == "Amazon"
    assert patched_data["destination_confirmed"] is True

    # 5. Sending succeeds afterward in dry-run mode
    send_ok = client.post(
        f"/api/complaints/drafts/{draft_id}/send",
        json={"confirmed": True},
    )
    assert send_ok.status_code == 200
    assert send_ok.json()["delivery_status"] == "dry_run"


def test_eligible_high_confidence_issue_drafts(client, seeded_db):
    """Test eligible high-confidence wrong-product, quantity-mismatch, and damage drafts."""
    order = seeded_db.query(Order).first()
    order.platform = "flipkart"
    seeded_db.commit()

    # Case A: Wrong Product
    session_wp = VerificationSession(
        id=uuid4().hex,
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session_wp)
    pm_mismatch = ProductMatchingRecord(
        verification_session_id=session_wp.id,
        order_id=order.id,
        overall_score=0.10,
        decision="mismatch",
        details={"confidence": 0.90},
    )
    seeded_db.add(pm_mismatch)
    seeded_db.commit()

    res_wp = client.post(
        "/api/complaints/drafts",
        json={"verification_session_id": session_wp.id, "requested_resolution": "replacement"},
    )
    assert res_wp.status_code == 201
    draft_wp = res_wp.json()
    assert draft_wp["eligible"] is True
    assert draft_wp["manual_inquiry"] is False
    assert draft_wp["issue_types"] == ["wrong_product"]
    assert "Incorrect product received" in draft_wp["subject"]
    assert "Full replacement of the incorrect merchandise" in draft_wp["body_text"]

    # Case B: Quantity Mismatch
    session_qty = VerificationSession(
        id=uuid4().hex,
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session_qty)
    qty_mismatch = QuantityVerificationRecord(
        verification_session_id=session_qty.id,
        order_id=order.id,
        status="mismatch",
        details={"confidence": 0.88, "detected_quantity": 1, "expected_quantity": 2},
    )
    seeded_db.add(qty_mismatch)
    seeded_db.commit()

    res_qty = client.post(
        "/api/complaints/drafts",
        json={"verification_session_id": session_qty.id, "requested_resolution": "replacement"},
    )
    assert res_qty.status_code == 201
    draft_qty = res_qty.json()
    assert draft_qty["eligible"] is True
    assert draft_qty["manual_inquiry"] is False
    assert draft_qty["issue_types"] == ["quantity_mismatch"]
    assert "Quantity discrepancy" in draft_qty["subject"]
    assert "Full replacement of the affected merchandise" in draft_qty["body_text"]

    # Case C: Damaged Item
    session_dmg = VerificationSession(
        id=uuid4().hex,
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
    )
    seeded_db.add(session_dmg)
    cond_dmg = ConditionAnalysisRecord(
        verification_session_id=session_dmg.id,
        order_id=order.id,
        status="damaged",
        details={"confidence": 0.93},
    )
    seeded_db.add(cond_dmg)
    seeded_db.commit()

    res_dmg = client.post(
        "/api/complaints/drafts",
        json={"verification_session_id": session_dmg.id, "requested_resolution": "replacement"},
    )
    assert res_dmg.status_code == 201
    draft_dmg = res_dmg.json()
    assert draft_dmg["eligible"] is True
    assert draft_dmg["manual_inquiry"] is False
    assert draft_dmg["issue_types"] == ["damaged_item"]
    assert "Damaged item received" in draft_dmg["subject"]
    assert "Full replacement of the damaged merchandise" in draft_dmg["body_text"]
