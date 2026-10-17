"""Comprehensive tests for Phase 2.1 shipping label 8-field summary extraction.

Tests spatial table parsing, row-number vs quantity disambiguation, address PIN extraction,
missing field safety, warnings generation, summary structure consistency, and Flipkart Order ID normalization.
"""

from pathlib import Path
import pytest

from app.schemas.enums import Platform, VerificationStatus
from app.schemas.label import BoundingBox, LabelSummary, ParsedShippingLabel
from app.schemas.order import OrderSummary
from app.schemas.verification import OrderVerificationResult
from app.services.image_preprocessor import preprocess_image_for_ocr
from app.services.label_parser import (
    build_label_summary,
    normalize_flipkart_order_id,
    parse_shipping_label,
)
from app.services.label_verification_service import cross_check_barcode_and_label
from scripts.inspect_label import inspect_image


def _make_det(text: str, conf: float, x1: int, y1: int, x2: int, y2: int) -> dict:
    """Helper to generate mock OCR detection dictionary with bounding box."""
    return {
        "text": text,
        "confidence": conf,
        "bounding_box": {
            "top_left": {"x": x1, "y": y1},
            "top_right": {"x": x2, "y": y1},
            "bottom_right": {"x": x2, "y": y2},
            "bottom_left": {"x": x1, "y": y2},
        },
    }


def _flipkart_detections():
    return [
        _make_det("EKart Loaustics_", 0.95, 219, 174, 394, 212),
        _make_det("00338382395285043100", 0.95, 219, 206, 535, 245),
        _make_det("Ordered through", 0.90, 136, 245, 347, 287),
        _make_det("Flpkart", 0.98, 196, 289, 324, 345),
        _make_det("Shipping/Customer address:", 0.95, 392, 670, 790, 710),
        _make_det("Name: Demo Customer,", 0.95, 392, 705, 730, 740),
        _make_det("Swami Blocks, Shelar Road,", 0.90, 392, 763, 807, 801),
        _make_det("Bibwewadi, Pune", 0.95, 392, 793, 532, 828),
        _make_det("411037, IN-MH", 0.95, 471, 823, 661, 856),
        # Table Header
        _make_det("SKU ID", 0.98, 369, 997, 457, 1026),
        _make_det("Description", 0.99, 468, 996, 609, 1031),
        _make_det("QTyl", 0.90, 802, 988, 863, 1024),
        # Table Row (Row number 1 at far left, Model 105, Colour Black, Product, Qty 1)
        _make_det("1", 0.99, 153, 1037, 165, 1056),
        _make_det("105", 0.98, 171, 1030, 219, 1062),
        _make_det("Black | Rocsil Analog Watch", 0.95, 229, 1027, 518, 1061),
        _make_det("For Women 105", 0.95, 527, 1025, 696, 1053),
        _make_det("1", 1.0, 805, 1025, 816, 1044),
        # AWB
        _make_det("FMPC6414359435", 0.95, 171, 1181, 393, 1223),
    ]


def test_flipkart_table_parsing_and_summary():
    """Verify complete 8-field extraction on realistic Flipkart label layout."""
    mock_detections = _flipkart_detections()

    parsed = parse_shipping_label(mock_detections)
    summary = build_label_summary(parsed)

    assert summary.platform == "Flipkart"
    assert summary.courier == "Ekart Logistics"
    assert summary.order_id == "OD338382395285043100"
    assert summary.tracking_number == "FMPC6414359435"
    assert summary.pin_code == "411037"
    assert "Rocsil Analog Watch For Women" in summary.product
    assert "105 - Black" in summary.colour_model
    assert summary.quantity == 1
    # Raw evidence must preserve the original OCR string
    assert "00338382395285043100" in parsed.raw_evidence


def test_amazon_layout_and_summary():
    """Verify extraction on Amazon ATS shipping label layout."""
    mock_detections = [
        _make_det("Amazon Shipping", 0.95, 100, 50, 300, 90),
        _make_det("Order ID: 402-9981245-11002", 0.98, 100, 100, 450, 140),
        _make_det("TBA992182741002", 0.99, 100, 150, 400, 190),
        _make_det("Ship to: Test User", 0.95, 100, 220, 350, 260),
        _make_det("Indiranagar, Bangalore", 0.90, 100, 270, 400, 310),
        _make_det("PIN: 560038 Karnataka", 0.95, 100, 320, 350, 360),
        # Table Header
        _make_det("Item Description", 0.95, 100, 420, 400, 450),
        _make_det("Qty", 0.95, 500, 420, 580, 450),
        # Data row
        _make_det("Echo Dot 4th Gen Smart Speaker", 0.95, 100, 470, 450, 510),
        _make_det("2", 0.99, 520, 470, 550, 510),
    ]

    parsed = parse_shipping_label(mock_detections)
    summary = build_label_summary(parsed)

    assert summary.platform == "Amazon"
    assert summary.courier == "Amazon Shipping"
    assert summary.order_id == "402-9981245-11002"
    assert summary.tracking_number == "TBA992182741002"
    assert summary.pin_code == "560038"
    assert "Echo Dot" in summary.product
    assert summary.quantity == 2


def test_missing_fields_never_invented():
    """Verify unreadable or absent fields remain strictly None."""
    mock_detections = [
        _make_det("Flipkart", 0.95, 100, 100, 250, 140),
        _make_det("FMPC1234567890", 0.95, 100, 200, 350, 240),
    ]

    parsed = parse_shipping_label(mock_detections)
    summary = build_label_summary(parsed)

    assert summary.platform == "Flipkart"
    assert summary.tracking_number == "FMPC1234567890"
    assert summary.courier is None
    assert summary.order_id is None
    assert summary.pin_code is None
    assert summary.product is None
    assert summary.colour_model is None
    assert summary.quantity is None


def test_row_number_vs_quantity_disambiguation():
    """Verify table row number at the left edge is never confused with quantity."""
    mock_detections = [
        # Table header
        _make_det("SKU ID", 0.95, 300, 200, 400, 230),
        _make_det("Description", 0.95, 420, 200, 600, 230),
        _make_det("Qty", 0.95, 750, 200, 820, 230),
        # Row 1 with row index '1' at far left margin (x=100), SKU '505', Product, and Qty '3' at x=780
        _make_det("1", 0.99, 100, 250, 120, 280),
        _make_det("505", 0.95, 320, 250, 380, 280),
        _make_det("Wireless Ergonomic Mouse", 0.95, 420, 250, 650, 280),
        _make_det("3", 0.99, 770, 250, 790, 280),
    ]

    parsed = parse_shipping_label(mock_detections)
    summary = build_label_summary(parsed)

    assert summary.quantity == 3
    assert "Wireless Ergonomic Mouse" in summary.product


def test_address_number_vs_quantity_disambiguation():
    """Verify address house or flat numbers are never parsed as item quantity."""
    mock_detections = [
        _make_det("Shipping/Customer address:", 0.95, 100, 100, 400, 130),
        _make_det("Flat 101, Road 2, Block 4", 0.95, 100, 140, 400, 170),
        _make_det("Pune 411037", 0.95, 100, 180, 300, 210),
        _make_det("Qty: 1", 0.95, 100, 300, 200, 330),
    ]

    parsed = parse_shipping_label(mock_detections)
    summary = build_label_summary(parsed)

    assert summary.quantity == 1
    assert summary.pin_code == "411037"


def test_address_region_pin_code_isolation():
    """Verify PIN code is extracted specifically from address block, ignoring invoice/other 6-digit numbers."""
    mock_detections = [
        # Invoice number with 6 digits (should be ignored for PIN code)
        _make_det("Tax Invoice: INV-654321", 0.95, 100, 50, 400, 80),
        _make_det("GSTIN: 27AABCS1429B1Z0", 0.95, 100, 90, 400, 120),
        # Customer address block
        _make_det("Shipping Address:", 0.95, 100, 200, 300, 230),
        _make_det("Student Hostel, Kothrud", 0.95, 100, 240, 400, 270),
        _make_det("Pune 411038 Maharashtra", 0.95, 100, 280, 400, 310),
    ]

    parsed = parse_shipping_label(mock_detections)
    summary = build_label_summary(parsed)

    assert summary.pin_code == "411038"


# ---------------------------------------------------------------------------
# Flipkart Order ID Normalization Tests (Required for Phase 2.1 Correction)
# ---------------------------------------------------------------------------


def test_flipkart_order_id_canonical_od():
    """Verify canonical OD prefix followed by 18 digits is recognized."""
    canonical_id = "OD338382395285043100"
    norm = normalize_flipkart_order_id(canonical_id, has_flipkart_context=True, has_order_context=True)
    assert norm == "OD338382395285043100"


def test_flipkart_order_id_00_misread_corrected():
    """Verify OCR misread '00' followed by 18 digits is normalized to 'OD'."""
    ocr_misread = "00338382395285043100"
    norm = normalize_flipkart_order_id(ocr_misread, has_flipkart_context=True, has_order_context=True)
    assert norm == "OD338382395285043100"


def test_flipkart_order_id_0d_misread_corrected():
    """Verify OCR misread '0D' or 'O0' is normalized to 'OD'."""
    misread_0d = "0D338382395285043100"
    misread_o0 = "O0338382395285043100"
    assert normalize_flipkart_order_id(misread_0d, has_flipkart_context=True, has_order_context=True) == "OD338382395285043100"
    assert normalize_flipkart_order_id(misread_o0, has_flipkart_context=True, has_order_context=True) == "OD338382395285043100"


def test_random_20_digit_not_converted():
    """Verify random 20-digit numbers without Flipkart OD confusion are never converted to Order ID."""
    random_num = "99887766554433221100"
    assert normalize_flipkart_order_id(random_num, has_flipkart_context=True, has_order_context=True) is None

    # Even with 00 prefix, if outside Flipkart context, should not convert
    assert normalize_flipkart_order_id("00112233445566778899", has_flipkart_context=False, has_order_context=True) is None


def test_tracking_number_not_treated_as_order_id():
    """Verify tracking numbers (e.g. FMPC..., TBA...) are never treated as Order IDs."""
    tracking_fmpc = "FMPC6414359435"
    tracking_tba = "TBA992182741002"
    assert normalize_flipkart_order_id(tracking_fmpc, has_flipkart_context=True, has_order_context=True) is None
    assert normalize_flipkart_order_id(tracking_tba, has_flipkart_context=True, has_order_context=True) is None


def test_realistic_flipkart_line_order_id():
    """Verify parsing realistic Flipkart line with OD338382395285043100."""
    mock_detections = [
        _make_det("Ordered through Flipkart", 0.95, 100, 100, 400, 140),
        _make_det("OD338382395285043100", 0.98, 100, 150, 450, 190),
        _make_det("COD", 0.90, 500, 150, 560, 190),
    ]
    parsed = parse_shipping_label(mock_detections)
    summary = build_label_summary(parsed)
    assert summary.platform == "Flipkart"
    assert summary.order_id == "OD338382395285043100"


def test_barcode_and_ocr_contradiction_warning(db_session):
    """Verify cross-check warns when barcode tracking differs from OCR tracking."""
    from datetime import datetime, timezone

    # Create dummy barcode result
    b_res = OrderVerificationResult(
        verification_status=VerificationStatus.VERIFIED,
        decoded_value="FMPC1111111111",
        barcode_type="Code 128",
        created_at=datetime.now(timezone.utc),
        matched_order=OrderSummary(
            order_id="OD123",
            tracking_id="FMPC1111111111",
            barcode_value="FMPC1111111111",
            platform="flipkart",
            customer_name="Test User",
            order_status="delivered",
            item_count=1,
            items_summary=[],
        ),
    )

    # Label text has contradicting tracking number
    mock_detections = [
        _make_det("FMPC9999999999", 0.95, 100, 100, 300, 130),
        _make_det("Flipkart", 0.95, 100, 150, 200, 180),
    ]
    parsed = parse_shipping_label(mock_detections)
    res = cross_check_barcode_and_label(
        db=db_session,
        parsed_label=parsed,
        barcode_result=b_res,
        matched_order=None,
    )

    assert any("Contradiction alert" in w for w in res.warnings)


def test_low_confidence_warning(db_session):
    """Verify warnings are produced when OCR confidence is below 0.60."""
    mock_detections = [
        _make_det("EKart oaustics", 0.45, 100, 100, 300, 130),
        _make_det("FMPC6414359435", 0.95, 100, 200, 350, 230),
    ]
    parsed = parse_shipping_label(mock_detections)
    res = cross_check_barcode_and_label(
        db=db_session,
        parsed_label=parsed,
        barcode_result=None,
        matched_order=None,
    )

    assert any("Low OCR confidence" in w for w in res.warnings)


def test_image_preprocessor_does_not_modify_disk(tmp_path):
    """Verify image preprocessing runs purely in-memory and never alters files on disk."""
    from PIL import Image

    test_img = Image.new("RGB", (600, 800), color=(200, 200, 200))
    img_path = tmp_path / "original_test.png"
    test_img.save(img_path)
    original_mtime = img_path.stat().st_mtime
    original_size = img_path.stat().st_size

    enhanced, scale = preprocess_image_for_ocr(img_path)

    assert enhanced is not None
    assert scale > 1.0  # Was upscaled because 800 < 1800
    assert img_path.stat().st_mtime == original_mtime
    assert img_path.stat().st_size == original_size


def test_cli_inspect_label(capsys, tmp_path, monkeypatch):
    """Exercise the actual CLI parser/formatter without user uploads or OCR weights."""
    from PIL import Image
    from scripts import inspect_label
    sample_path = tmp_path / "synthetic_label.png"
    Image.new("RGB", (320, 320), "white").save(sample_path)
    monkeypatch.setattr(inspect_label, "extract_text", lambda path: _flipkart_detections())
    exit_code = inspect_image(sample_path, output_json=False)
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Platform: Flipkart" in captured.out
    assert "Courier: Ekart Logistics" in captured.out
    assert "Order ID: OD338382395285043100" in captured.out
    assert "AWB/Tracking number: FMPC6414359435" in captured.out
    assert "PIN code: 411037" in captured.out
    assert "Product: Rocsil Analog Watch For Women" in captured.out
    assert "Colour/Model: 105 - Black" in captured.out
    assert "Quantity: 1" in captured.out
