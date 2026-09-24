from app.schemas.enums import FieldComparisonStatus, LabelVerificationStatus, Platform
from app.services.label_parser import parse_shipping_label
from app.services.label_verification_service import cross_check_barcode_and_label
from app.services.order_service import find_order_by_barcode


def test_barcode_and_label_agreement(seeded_db):
    # Fictional Flipkart order FMPC6414359435
    order = find_order_by_barcode(seeded_db, "FMPC6414359435")
    assert order is not None

    detections = [
        {"text": "Ordered through Flpkart", "confidence": 0.95},
        {"text": "Tracking ID: FMPC6414359435", "confidence": 0.96},
        {"text": "Order ID: DEMO-OD-FLIPKART-88214", "confidence": 0.95},
        {"text": "Name: Demo Student User (Testing)", "confidence": 0.92},
        {"text": "Pune 411037", "confidence": 0.90},
        {"text": "Description: Rocsil Analog Watch", "confidence": 0.88},
    ]
    parsed_label = parse_shipping_label(detections)

    result = cross_check_barcode_and_label(
        db=seeded_db,
        parsed_label=parsed_label,
        matched_order=order,
    )

    assert result.status == LabelVerificationStatus.VERIFIED
    # Check field comparisons
    fields = {c.field_name: c for c in result.field_comparisons}
    assert fields["tracking_id"].status == FieldComparisonStatus.MATCH
    assert fields["order_id"].status == FieldComparisonStatus.MATCH
    assert fields["platform"].status == FieldComparisonStatus.MATCH
    assert fields["customer_name"].status == FieldComparisonStatus.MATCH
    assert fields["pin_code"].status == FieldComparisonStatus.MATCH


def test_barcode_and_label_contradiction(seeded_db):
    order = find_order_by_barcode(seeded_db, "FMPC6414359435")
    assert order is not None

    # Printed label displays a completely different tracking ID and platform
    detections = [
        {"text": "Amazon Shipping", "confidence": 0.95},
        {"text": "Tracking ID: AMZN-IN-981240182", "confidence": 0.98},
        {"text": "Order ID: DEMO-AMZ-402-9981245-11002", "confidence": 0.95},
    ]
    parsed_label = parse_shipping_label(detections)

    result = cross_check_barcode_and_label(
        db=seeded_db,
        parsed_label=parsed_label,
        matched_order=order,
    )

    assert result.status == LabelVerificationStatus.MISMATCH
    fields = {c.field_name: c for c in result.field_comparisons}
    assert fields["tracking_id"].status == FieldComparisonStatus.MISMATCH
    assert fields["platform"].status == FieldComparisonStatus.MISMATCH
    assert any("contradicts" in w.lower() for w in result.warnings)


def test_missing_label_text(seeded_db):
    order = find_order_by_barcode(seeded_db, "FMPC6414359435")
    assert order is not None

    # Label contains only generic uninformative text
    detections = [
        {"text": "FRAGILE", "confidence": 0.90},
        {"text": "HANDLE WITH CARE", "confidence": 0.92},
    ]
    parsed_label = parse_shipping_label(detections)

    result = cross_check_barcode_and_label(
        db=seeded_db,
        parsed_label=parsed_label,
        matched_order=order,
    )

    assert result.status == LabelVerificationStatus.INSUFFICIENT_EVIDENCE
    fields = {c.field_name: c for c in result.field_comparisons}
    assert fields["tracking_id"].status == FieldComparisonStatus.NOT_DETECTED
    assert fields["order_id"].status == FieldComparisonStatus.NOT_DETECTED


def test_unknown_barcode_with_matching_label(seeded_db):
    # Barcode was not detected or matched (matched_order is None)
    # But printed label contains a valid tracking ID corresponding to Flipkart order
    detections = [
        {"text": "Tracking ID: FMPC6414359435", "confidence": 0.95},
        {"text": "Ordered through Flipkart", "confidence": 0.92},
    ]
    parsed_label = parse_shipping_label(detections)

    result = cross_check_barcode_and_label(
        db=seeded_db,
        parsed_label=parsed_label,
        matched_order=None,  # Barcode gave no order
    )

    # Per Step 7 rule 4:
    # Barcode is not found, but a readable label identifier matches an order -> manual_review_required
    assert result.status == LabelVerificationStatus.MANUAL_REVIEW_REQUIRED
    assert any("manual review required" in w.lower() for w in result.warnings)


def test_prevention_of_pii_leakage(seeded_db):
    order = find_order_by_barcode(seeded_db, "FMPC6414359435")
    assert order is not None

    detections = [
        {"text": "Customer: Demo Student User", "confidence": 0.9},
        {"text": "Pune 411037", "confidence": 0.9},
        {"text": "Ph: 9876543210", "confidence": 0.9},
    ]
    parsed_label = parse_shipping_label(detections)
    result = cross_check_barcode_and_label(db=seeded_db, parsed_label=parsed_label, matched_order=order)

    # Convert to JSON dict
    output = result.model_dump(mode="json")
    output_str = str(output)

    # Full address must NOT be present in field comparisons
    assert order.delivery_address not in output_str
    # Customer email must NOT be present in label comparisons
    assert order.customer_email not in output_str
    # Phone number must be masked
    assert "9876543210" not in output_str
    if output["parsed_fields"]["phone_number"]:
        assert "XXXXXX" in output["parsed_fields"]["phone_number"]["value"]


def test_verify_label_api_endpoint(client, seeded_db):
    payload = {
        "ocr_detections": [
            {"text": "Ordered through Flipkart", "confidence": 0.95},
            {"text": "Tracking ID: FMPC6414359435", "confidence": 0.96},
            {"text": "Order ID: DEMO-OD-FLIPKART-88214", "confidence": 0.95},
        ],
        "expected_tracking_id": "FMPC6414359435",
        "expected_platform": "flipkart",
    }
    response = client.post("/api/verification/label", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "verified"
    assert data["parsed_fields"]["tracking_id"]["value"] == "FMPC6414359435"
    assert len(data["field_comparisons"]) > 0
