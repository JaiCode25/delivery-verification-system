from app.schemas.enums import Platform
from app.services.label_parser import (
    mask_phone_number,
    normalize_name,
    normalize_tracking_id,
    parse_shipping_label,
)


def test_tracking_id_extraction():
    # Explicit prefix
    detections_prefix = [
        {"text": "Ekart Logistics", "confidence": 0.9},
        {"text": "Tracking ID: FMPC6414359435", "confidence": 0.95},
    ]
    parsed = parse_shipping_label(detections_prefix)
    assert parsed.tracking_id is not None
    assert parsed.tracking_id.value == "FMPC6414359435"
    assert parsed.tracking_id.normalized_value == "FMPC6414359435"
    assert parsed.tracking_id.confidence >= 0.90

    # Standalone courier pattern (Flipkart FMPC)
    detections_standalone = [
        {"text": "FMPC6414359435", "confidence": 0.92},
    ]
    parsed2 = parse_shipping_label(detections_standalone)
    assert parsed2.tracking_id is not None
    assert parsed2.tracking_id.normalized_value == "FMPC6414359435"


def test_awb_extraction():
    # AWB prefix
    detections = [
        {"text": "AWB No: 981240182345", "confidence": 0.88},
    ]
    parsed = parse_shipping_label(detections)
    assert parsed.tracking_id is not None
    assert parsed.tracking_id.value == "981240182345"
    assert parsed.tracking_id.normalized_value == "981240182345"

    # Amazon TBA pattern
    amazon_dets = [
        {"text": "TBA123456789012", "confidence": 0.91},
    ]
    parsed_amz = parse_shipping_label(amazon_dets)
    assert parsed_amz.tracking_id is not None
    assert parsed_amz.tracking_id.value == "TBA123456789012"


def test_order_id_extraction():
    # Flipkart format
    detections_fk = [
        {"text": "Ordered through Flipkart", "confidence": 0.9},
        {"text": "Order ID: DEMO-OD-FLIPKART-88214", "confidence": 0.95},
    ]
    parsed_fk = parse_shipping_label(detections_fk)
    assert parsed_fk.order_id is not None
    assert parsed_fk.order_id.value == "DEMO-OD-FLIPKART-88214"
    assert parsed_fk.order_id.normalized_value == "DEMOODFLIPKART88214"

    # Amazon 3-7-7 format
    detections_amz = [
        {"text": "Order # 402-9981245-11002", "confidence": 0.93},
    ]
    parsed_amz = parse_shipping_label(detections_amz)
    assert parsed_amz.order_id is not None
    assert "402-9981245-11002" in parsed_amz.order_id.value


def test_platform_detection():
    # Flipkart keywords
    p_fk = parse_shipping_label([{"text": "Ordered through Flpkart", "confidence": 0.95}])
    assert p_fk.platform is not None
    assert p_fk.platform.normalized_value == Platform.FLIPKART.value

    # Amazon keywords
    p_amz = parse_shipping_label([{"text": "Fulfilled by Amazon", "confidence": 0.92}])
    assert p_amz.platform is not None
    assert p_amz.platform.normalized_value == Platform.AMAZON.value

    # Blinkit keywords
    p_blk = parse_shipping_label([{"text": "Blinkit Express Delivery", "confidence": 0.90}])
    assert p_blk.platform is not None
    assert p_blk.platform.normalized_value == Platform.BLINKIT.value


def test_pin_code_extraction():
    detections = [
        {"text": "Delivery Address: Kothari Blocks, Shelar Road", "confidence": 0.85},
        {"text": "Bibwewadi, Pune 411037 IN-MH", "confidence": 0.92},
    ]
    parsed = parse_shipping_label(detections)
    assert parsed.pin_code is not None
    assert parsed.pin_code.value == "411037"
    assert parsed.pin_code.normalized_value == "411037"


def test_phone_number_masking():
    # Direct function test
    masked = mask_phone_number("9876543210")
    assert masked == "XXXXXX3210"
    assert "987654" not in masked

    # Extraction test
    detections = [
        {"text": "Contact No: +91 9876543210", "confidence": 0.88},
    ]
    parsed = parse_shipping_label(detections)
    assert parsed.phone_number is not None
    assert parsed.phone_number.value == "XXXXXX3210"
    assert "987654" not in parsed.phone_number.value


def test_missing_fields_return_none():
    # Only tracking ID present
    detections = [
        {"text": "FMPC6414359435", "confidence": 0.9},
    ]
    parsed = parse_shipping_label(detections)
    assert parsed.tracking_id is not None
    assert parsed.order_id is None
    assert parsed.customer_name is None
    assert parsed.pin_code is None
    assert parsed.phone_number is None
    assert parsed.seller_name is None


def test_noisy_ocr_text():
    detections = [
        {"text": "!@#$%^&*", "confidence": 0.3},
        {"text": "   ", "confidence": 0.2},
        {"text": "---===---", "confidence": 0.4},
        {"text": "Tracking ID: FMPC6414359435", "confidence": 0.85},
        {"text": "STD", "confidence": 0.99},
        {"text": "COD", "confidence": 0.95},
    ]
    parsed = parse_shipping_label(detections)
    assert parsed.tracking_id is not None
    assert parsed.tracking_id.normalized_value == "FMPC6414359435"
    assert parsed.order_id is None


def test_multiple_frame_consensus():
    # Simulate a video with 3 frames where frame_0001 is sharper than frame_0000
    detections = [
        {
            "text": "Tracking ID: FMPC6414359435",
            "confidence": 0.80,
            "source_frame": "frame_0000.jpg",
        },
        {
            "text": "Tracking ID: FMPC6414359435",
            "confidence": 0.92,
            "source_frame": "frame_0001.jpg",
        },
        {
            "text": "Tracking ID: FMPC6414359435",
            "confidence": 0.85,
            "source_frame": "frame_0002.jpg",
        },
    ]
    frames_meta = [
        {"frame_name": "frame_0000.jpg", "timestamp_seconds": 0.0, "sharpness": 80.0},
        {"frame_name": "frame_0001.jpg", "timestamp_seconds": 1.0, "sharpness": 250.0},
        {"frame_name": "frame_0002.jpg", "timestamp_seconds": 2.0, "sharpness": 110.0},
    ]
    parsed = parse_shipping_label(detections=detections, frames_metadata=frames_meta)

    assert parsed.tracking_id is not None
    assert parsed.tracking_id.normalized_value == "FMPC6414359435"
    # Multi-frame consensus should record 3 supporting frames
    assert parsed.tracking_id.frame_count == 3
    # Sharpest frame (frame_0001) should be chosen as source_frame
    assert parsed.tracking_id.source_frame == "frame_0001.jpg"
    assert parsed.tracking_id.video_timestamp_seconds == 1.0
    # Confidence should be boosted due to multi-frame consensus
    assert parsed.tracking_id.confidence > 0.92
