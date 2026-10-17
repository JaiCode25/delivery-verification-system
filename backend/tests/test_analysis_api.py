import io
import cv2
import numpy as np
import zxingcpp

from app.services import file_handler


def test_unsupported_file_extension(client):
    file_bytes = b"Sample text content"
    response = client.post(
        "/api/analyze",
        files={"file": ("document.txt", io.BytesIO(file_bytes), "text/plain")},
    )
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


def test_invalid_file_signature(client):
    # A .jpg file with random text rather than JPEG magic bytes
    fake_jpeg = b"This is not a real JPEG image"
    response = client.post(
        "/api/analyze",
        files={"file": ("corrupt.jpg", io.BytesIO(fake_jpeg), "image/jpeg")},
    )
    assert response.status_code == 400
    assert "signature" in response.json()["detail"].lower()


def test_empty_file_upload(client):
    response = client.post(
        "/api/analyze",
        files={"file": ("empty.jpg", io.BytesIO(b""), "image/jpeg")},
    )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_oversized_upload_rejection(client, monkeypatch):
    # Enforce a small limit for testing chunked stream rejection
    monkeypatch.setattr(file_handler, "MAX_FILE_SIZE", 500)
    # Valid PNG header followed by 1000 bytes
    oversized_payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000
    response = client.post(
        "/api/analyze",
        files={"file": ("oversized.png", io.BytesIO(oversized_payload), "image/png")},
    )
    assert response.status_code == 413
    assert "exceeds" in response.json()["detail"]


def test_analyze_image_with_barcode(client, seeded_db):
    # Generate synthetic Code 128 barcode image for demo order FMPC6414359435
    barcode = zxingcpp.create_barcode("FMPC6414359435", zxingcpp.BarcodeFormat.Code128)
    zxing_img = zxingcpp.write_barcode_to_image(barcode)
    np_arr = np.array(zxing_img, copy=True)
    _, encoded_png = cv2.imencode(".png", np_arr)
    png_bytes = encoded_png.tobytes()

    response = client.post(
        "/api/analyze",
        files={"file": ("test_label.png", io.BytesIO(png_bytes), "image/png")},
        data={"expected_platform": "flipkart"},
    )
    assert response.status_code == 200
    data = response.json()

    # Verify preserved fields
    assert "analysis_id" in data
    assert data["status"] == "completed"
    assert data["original_filename"] == "test_label.png"
    assert data["input_type"] == "image"
    assert data["frames_processed"] == 1
    assert "codes" in data
    assert len(data["codes"]) >= 1

    # Verify detected barcode details
    code_entry = data["codes"][0]
    assert code_entry["text"] == "FMPC6414359435"
    assert code_entry["sharpness"] > 0
    assert "position" in code_entry

    # Verify order verification integration
    assert "order_verification" in data
    ov = data["order_verification"]
    assert ov["verification_status"] == "verified"
    assert ov["matched_order"]["order_id"] == "DEMO-OD-FLIPKART-88214"
    assert ov["decoded_value"] == "FMPC6414359435"

    # Verify structured label analysis integration
    assert "label_analysis" in data
    la = data["label_analysis"]
    assert "status" in la
    assert "parsed_fields" in la
    assert "field_comparisons" in la

    # Verify session persisted analysis metadata
    session_id = ov["verification_session_id"]
    assert session_id is not None

    # Generate Report from this session
    report_res = client.post(f"/api/reports/generate/{session_id}")
    assert report_res.status_code == 201
    report = report_res.json()
    assert report["analysis_id"] == data["analysis_id"]
    assert report["input_type"] == "image"
    assert report["original_filename"] == "test_label.png"
    assert report["label_summary"] is not None
    assert report["label_summary"]["platform"] is None or report["label_summary"]["platform"] == "flipkart"
