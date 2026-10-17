import io
import pytest
from PIL import Image, ImageDraw

from app.database.models import Order, VerificationSession
from app.schemas.enums import (
    LabelVerificationStatus,
    OverallVerificationDecision,
    ProductMatchDecision,
    VerificationStatus,
)


def test_generate_verified_report(client, db_session):
    order = db_session.query(Order).filter(Order.order_id == "OD290920260550001234").first()
    # Create a verified session
    session = VerificationSession(
        order_id=order.id,
        decoded_code=order.barcode_value,
        barcode_type="QR_CODE",
        verification_status=VerificationStatus.VERIFIED.value,
        label_analysis_status=LabelVerificationStatus.VERIFIED.value,
        label_summary={
            "platform": "Flipkart",
            "courier": "Ekart",
            "order_id": order.order_id,
            "tracking_number": order.tracking_id,
            "pin_code": "411037",
            "product": "VGR V-055 Hair Trimmer",
            "colour_model": "Black/Blue",
            "quantity": 1,
        },
        mismatch_reasons=[],
        evidence_frame="frame_0001.jpg",
        input_type="image",
        original_filename="sample_label.png",
        video_timestamp_seconds=None,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    session_id = session.id

    # Generate Report
    response = client.post(f"/api/reports/generate/{session_id}")
    assert response.status_code == 201
    report_data = response.json()
    assert report_data["verification_session_id"] == session_id
    assert report_data["final_decision"] in (
        OverallVerificationDecision.VERIFIED.value,
        OverallVerificationDecision.LIKELY_VERIFIED.value,
    )
    assert report_data["input_type"] == "image"
    assert report_data["original_filename"] == "sample_label.png"

    # Check PII Masking
    assert "Demo Customer" not in report_data["masked_customer_name"]
    assert "D***" in report_data["masked_customer_name"]
    assert "@example.com" in report_data["masked_customer_email"]
    assert "demo-vgr" not in report_data["masked_customer_email"]

    report_id = report_data["report_id"]

    # Test GET JSON report
    get_json_res = client.get(f"/api/reports/{report_id}")
    assert get_json_res.status_code == 200
    assert get_json_res.json()["report_id"] == report_id

    # Test GET HTML report
    get_html_res = client.get(f"/api/reports/{report_id}/html")
    assert get_html_res.status_code == 200
    assert "text/html" in get_html_res.headers["content-type"]
    assert "<!DOCTYPE html>" in get_html_res.text
    assert "Delivery Verification Report" in get_html_res.text
    assert report_id in get_html_res.text


def test_generate_mismatch_report(client, db_session):
    session = VerificationSession(
        order_id=None,
        decoded_code="UNKNOWN_BARCODE_12345",
        barcode_type="CODE_128",
        verification_status=VerificationStatus.DETAILS_MISMATCH.value,
        mismatch_reasons=["Barcode value does not match expected order."],
        evidence_frame="mismatch_frame.jpg",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    session_id = session.id

    response = client.post(f"/api/reports/generate/{session_id}")
    assert response.status_code == 201
    report_data = response.json()
    assert report_data["final_decision"] == OverallVerificationDecision.MISMATCH.value
    assert len(report_data["warnings"]) > 0


def test_generate_report_missing_session_returns_404(client):
    response = client.post("/api/reports/generate/nonexistent-session-id-000")
    assert response.status_code == 404


def test_get_nonexistent_report_returns_404(client):
    response = client.get("/api/reports/nonexistent-report-uuid-999")
    assert response.status_code == 404

    html_res = client.get("/api/reports/nonexistent-report-uuid-999/html")
    assert html_res.status_code == 404


def test_report_with_insufficient_label_and_product_match(client, db_session):
    """Test scenario where QR code and visual product match, but shipping-label OCR was insufficient."""
    order_id = "OD290920260550001234"
    order = db_session.query(Order).filter(Order.order_id == order_id).first()

    session = VerificationSession(
        analysis_id="ded4cf1ee34844bd9ecbd5e7edd06777",
        input_type="image",
        original_filename="user_delivered_photo.png",
        order_id=order.id,
        decoded_code=order.barcode_value,
        barcode_type="QR_CODE",
        verification_status=VerificationStatus.VERIFIED.value,
        label_analysis_status=LabelVerificationStatus.INSUFFICIENT_EVIDENCE.value,
        label_summary={
            "platform": None,
            "courier": None,
            "order_id": None,
            "tracking_number": None,
            "pin_code": None,
            "product": None,
            "colour_model": None,
            "quantity": None,
        },
        label_analysis_data={
            "status": "insufficient_evidence",
            "warnings": ["No shipping-label fields could be reliably matched against the verified order."],
        },
        mismatch_reasons=[],
        evidence_frame="frame_0001.jpg",
        video_timestamp_seconds=None,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    session_id = session.id

    # Upload reference image for the order
    ref_img = Image.new("RGB", (300, 300), color="blue")
    draw = ImageDraw.Draw(ref_img)
    draw.rectangle([50, 50, 250, 250], fill="cyan")
    draw.text((80, 140), "VGR V-055", fill="white")

    ref_buf = io.BytesIO()
    ref_img.save(ref_buf, format="JPEG")
    ref_buf.seek(0)
    upload_res = client.post(
        f"/api/orders/{order_id}/references",
        files=[("files", ("ref_trimmer.jpg", ref_buf.read(), "image/jpeg"))],
        data={"product_name": "VGR V-055 Professional Hair Trimmer", "brand": "VGR", "model": "V-055", "colour": "Black/Blue"},
    )
    assert upload_res.status_code in (200, 201)

    # Run product matching
    cand_buf = io.BytesIO()
    ref_img.save(cand_buf, format="JPEG")
    cand_buf.seek(0)
    match_res = client.post(
        "/api/product-matching/analyze",
        data={"order_id": order_id, "verification_session_id": session_id},
        files={"file": ("delivered.jpg", cand_buf.read(), "image/jpeg")},
    )
    assert match_res.status_code == 200
    assert match_res.json()["decision"] in (ProductMatchDecision.MATCH.value, ProductMatchDecision.LIKELY_MATCH.value)

    # Generate Report
    report_res = client.post(f"/api/reports/generate/{session_id}")
    assert report_res.status_code == 201
    report = report_res.json()

    # Verify preserved metadata
    assert report["analysis_id"] == "ded4cf1ee34844bd9ecbd5e7edd06777"
    assert report["input_type"] == "image"
    assert report["original_filename"] == "user_delivered_photo.png"

    # Verify 8-field summary preserved
    assert report["label_summary"] is not None
    assert report["label_summary"]["platform"] is None
    assert report["label_summary"]["quantity"] is None

    # Verify final synthesis
    assert report["final_decision"] == OverallVerificationDecision.LIKELY_VERIFIED.value
    assert "Shipping-label text was unavailable or insufficient" in report["decision_explanation"]
    assert any("shipping-label" in w.lower() or "insufficient" in w.lower() for w in report["warnings"])


def test_report_idempotent_regeneration(client, db_session):
    """Test that generating report multiple times for same session updates existing record idempotently."""
    order_id = "OD290920260550001234"
    order = db_session.query(Order).filter(Order.order_id == order_id).first()
    session = VerificationSession(
        order_id=order.id,
        decoded_code=order.barcode_value,
        barcode_type="QR_CODE",
        verification_status=VerificationStatus.VERIFIED.value,
        evidence_frame="frame_0001.jpg",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    session_id = session.id

    # First generation
    res1 = client.post(f"/api/reports/generate/{session_id}")
    assert res1.status_code == 201
    data1 = res1.json()
    report_id1 = data1["report_id"]

    # Second generation
    res2 = client.post(f"/api/reports/generate/{session_id}")
    assert res2.status_code == 201
    data2 = res2.json()
    report_id2 = data2["report_id"]

    # Must reuse same report ID without creating duplicate entries
    assert report_id1 == report_id2


def test_end_to_end_session_linked_report_and_isolation(client, db_session):
    order_id = "OD290920260550001234"
    order = db_session.query(Order).filter(Order.order_id == order_id).first()

    # Create Session A
    session_a = VerificationSession(
        order_id=order.id,
        decoded_code=order.barcode_value,
        barcode_type="QR_CODE",
        verification_status=VerificationStatus.VERIFIED.value,
        evidence_frame="evidence_a.jpg",
        video_timestamp_seconds=2.5,
    )
    db_session.add(session_a)

    # Create Session B for same order
    session_b = VerificationSession(
        order_id=order.id,
        decoded_code=order.barcode_value,
        barcode_type="QR_CODE",
        verification_status=VerificationStatus.VERIFIED.value,
        evidence_frame="evidence_b.jpg",
        video_timestamp_seconds=5.0,
    )
    db_session.add(session_b)
    db_session.commit()
    db_session.refresh(session_a)
    db_session.refresh(session_b)
    session_a_id = session_a.id
    session_b_id = session_b.id

    # Upload reference image
    img = Image.new("RGB", (300, 300), color="blue")
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 50, 250, 250], fill="cyan")
    draw.text((80, 140), "VGR V-055", fill="white")

    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    upload_res = client.post(
        f"/api/orders/{order_id}/references",
        files=[("files", ("ref_trimmer.jpg", buf.read(), "image/jpeg"))],
        data={"product_name": "VGR V-055 Professional Hair Trimmer", "brand": "VGR", "model": "V-055", "colour": "Black/Blue"},
    )
    assert upload_res.status_code in (200, 201)

    # Run product matching strictly for Session A
    cand_buf = io.BytesIO()
    img.save(cand_buf, format="JPEG")
    cand_buf.seek(0)
    match_res = client.post(
        "/api/product-matching/analyze",
        data={"order_id": order_id, "verification_session_id": session_a_id},
        files={"file": ("delivered.jpg", cand_buf.read(), "image/jpeg")},
    )
    assert match_res.status_code == 200

    # Generate Report for Session A
    report_a_res = client.post(f"/api/reports/generate/{session_a_id}")
    assert report_a_res.status_code == 201
    report_a_data = report_a_res.json()
    assert report_a_data["verification_session_id"] == session_a_id
    assert report_a_data["product_matching"] is not None
    assert report_a_data["product_matching"]["overall_match_score"] >= 0.65
    assert report_a_data["final_decision"] in (OverallVerificationDecision.VERIFIED.value, OverallVerificationDecision.LIKELY_VERIFIED.value)

    # Generate Report for Session B (which had NO product matching run)
    report_b_res = client.post(f"/api/reports/generate/{session_b_id}")
    assert report_b_res.status_code == 201
    report_b_data = report_b_res.json()
    assert report_b_data["verification_session_id"] == session_b_id
    # Session B MUST NOT inherit or reuse Session A's product matching record
    assert report_b_data["product_matching"] is None


def test_new_channels_regenerate_without_stale_data(client, evidence_case, monkeypatch):
    from app.services import model_adapters
    from tests.test_quantity_verification import objects, upload as quantity_upload
    from tests.test_condition_analysis import upload as condition_upload
    session_id = evidence_case.session.id
    first = client.post(f"/api/reports/generate/{session_id}").json()
    assert first["quantity_verification"] is None and first["condition_analysis"] is None
    assert first["channel_statuses"]["condition_analysis"] == "not_run"
    assert any("condition_analysis: not_run" in warning for warning in first["warnings"])
    assert "Quantity verification:" not in first["decision_explanation"]
    assert "Visible condition:" not in first["decision_explanation"]
    monkeypatch.setattr(model_adapters, "detect_objects", lambda *args: objects(1))
    quantity_upload(client, evidence_case)
    mismatch = client.post(f"/api/reports/generate/{session_id}").json()
    assert mismatch["report_id"] == first["report_id"]
    assert mismatch["final_decision"] == "mismatch"
    assert mismatch["quantity_verification"]["detected_quantity"] == 1
    monkeypatch.setattr(model_adapters, "detect_objects", lambda *args: objects(2))
    quantity_upload(client, evidence_case, regenerate="true")
    monkeypatch.setattr(model_adapters, "classify_condition", lambda *args: (.02, .98))
    condition_upload(client, evidence_case)
    updated = client.post(f"/api/reports/generate/{session_id}").json()
    assert updated["quantity_verification"]["status"] == "match"
    assert updated["condition_analysis"]["status"] == "no_visible_damage"
    assert updated["final_decision"] != "verified"  # product matching has not run
    assert "Product visual matching:" not in updated["decision_explanation"]
    html = client.get(updated["html_report_url"]).text
    assert "Quantity Verification" in html and "Detected:" in html
    assert "no visible damage detected" in html
    assert "Model:" in html


def test_strong_damage_in_report(client, db_session, evidence_case, mock_video_frames, monkeypatch):
    from app.services import model_adapters, condition_analysis_service
    from app.schemas.condition_analysis import EvidenceType
    from tests.test_condition_analysis import mock_cue
    mock_video_frames(3)
    mock_cue(monkeypatch)
    monkeypatch.setattr(model_adapters, "classify_condition", lambda *args: (.98, .02))
    condition_analysis_service.evaluate_condition(db_session, evidence_case.order.order_id,
                                                  evidence_case.session.id, evidence_case.path, "video", EvidenceType.PRODUCT)
    result = client.post(f"/api/reports/generate/{evidence_case.session.id}").json()
    assert result["final_decision"] == "issue_detected"
    assert result["condition_analysis"]["damage_detected"] is True
    assert "damaged" in client.get(result["html_report_url"]).text


@pytest.mark.parametrize("quantity,condition,expected", [
    ("match", "no_visible_damage", "verified"),
    ("unsupported_product", "no_visible_damage", "likely_verified"),
    (None, None, "likely_verified"),
    ("mismatch", "damaged", "mismatch"),
    ("match", "damaged", "issue_detected"),
    ("match", "likely_damaged", "manual_review_required"),
    ("model_unavailable", None, "manual_review_required"),
    ("match", "model_unavailable", "manual_review_required"),
])
def test_conservative_report_matrix(quantity, condition, expected):
    from app.services.report_service import synthesize_overall_decision
    decision, explanation, _ = synthesize_overall_decision("verified", "verified", "match", [], quantity, condition)
    assert decision.value == expected
    if quantity is None:
        assert "Quantity verification:" not in explanation
    if condition is None:
        assert "Visible condition:" not in explanation


def test_old_serialized_report_and_html_escaping(client, db_session, evidence_case):
    from app.database.models import VerificationReport
    response = client.post(f"/api/reports/generate/{evidence_case.session.id}")
    report_id = response.json()["report_id"]
    record = db_session.get(VerificationReport, report_id)
    legacy = dict(record.report_data)
    for key in ("quantity_verification", "condition_analysis", "channel_statuses"):
        legacy.pop(key)
    legacy["final_decision"] = "verified"
    legacy["original_filename"] = '<script>alert("evidence")</script>'
    record.report_data = legacy
    db_session.commit()
    result = client.get(f"/api/reports/{report_id}").json()
    assert result["final_decision"] == "verified"
    assert result["condition_analysis"] is None
    html = client.get(f"/api/reports/{report_id}/html").text
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "Visible condition has not been assessed" in html


def test_new_channels_remain_session_scoped(client, db_session, evidence_case):
    from tests.test_quantity_verification import upload
    upload(client, evidence_case)
    session_b = VerificationSession(order_id=evidence_case.order.id, verification_status="verified")
    db_session.add(session_b)
    db_session.commit()
    report = client.post(f"/api/reports/generate/{session_b.id}").json()
    assert report["quantity_verification"] is None and report["condition_analysis"] is None


def test_corrupt_saved_analysis_is_distinguished_from_not_run(client, db_session, evidence_case):
    from app.database.models import QuantityVerificationRecord
    db_session.add(QuantityVerificationRecord(order_id=evidence_case.order.id,
        verification_session_id=evidence_case.session.id, status="match", details={}))
    db_session.commit()
    report = client.post(f"/api/reports/generate/{evidence_case.session.id}").json()
    assert report["channel_statuses"]["quantity_verification"] == "missing_analysis"
    assert report["final_decision"] == "manual_review_required"


def test_nested_report_recipient_data_is_masked(client, db_session, evidence_case):
    evidence_case.session.label_analysis_data = {
        "status": "verified",
        "parsed_fields": {"customer_name": {"value": "Private Recipient", "normalized_value": "private recipient"},
                          "raw_evidence": ["Private Recipient, 99 Secret Road"]},
        "field_comparisons": [{"field_name": "customer_name", "status": "match",
                               "label_value": "Private Recipient", "order_value": evidence_case.order.customer_name,
                               "details": "Private Recipient matched"}],
    }
    db_session.commit()
    response = client.post(f"/api/reports/generate/{evidence_case.session.id}")
    assert response.status_code == 201
    assert "Private Recipient" not in response.text
    assert "Secret Road" not in response.text
    assert evidence_case.order.customer_name not in response.text
    report_id = response.json()["report_id"]
    assert "Private Recipient" not in client.get(f"/api/reports/{report_id}").text
    assert "Private Recipient" not in client.get(f"/api/reports/{report_id}/html").text
    # The source session stays intact; privacy filtering belongs to report output.
    assert evidence_case.session.label_analysis_data["parsed_fields"]["customer_name"]["value"] == "Private Recipient"
