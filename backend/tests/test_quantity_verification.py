import io
import pytest
from PIL import Image
from app.config import settings
from app.database.models import QuantityVerificationRecord, VerificationSession
from app.schemas.evidence import ObjectDetection
from app.services import model_adapters, quantity_verification_service as service


def objects(count, confidence=0.95):
    return [{"box": [20 + i * 80, 40, 75 + i * 80, 250], "class_name": "cup", "confidence": confidence} for i in range(count)]


def upload(client, case, **extra):
    return client.post("/api/quantity-verification/analyze", data={**case.data, **extra},
                       files={"file": ("../../evidence.png", case.payload, "image/png")})


@pytest.mark.parametrize("count,expected,status", [(2, 2, "match"), (1, 2, "mismatch"), (3, 2, "mismatch")])
def test_counts_persist_and_retrieve(client, db_session, evidence_case, monkeypatch, count, expected, status):
    monkeypatch.setattr(model_adapters, "detect_objects", lambda *args: objects(count))
    response = upload(client, evidence_case, expected_quantity=999)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["expected_quantity"] == expected
    assert result["detected_quantity"] == count
    assert result["status"] == status
    assert len({d["track_id"] for d in result["detections"]}) == count
    identifier = result["quantity_analysis_id"]
    assert db_session.get(QuantityVerificationRecord, identifier).verification_session_id == evidence_case.session.id
    assert client.get(f"/api/quantity-verification/{identifier}").json() == result
    assert "C:\\" not in response.text and "../" not in response.text


def test_unsupported_trimmer_never_becomes_expected_count(client, evidence_case, db_session, monkeypatch):
    evidence_case.order.items[0].product_name = "VGR V-055 Professional Hair Trimmer"
    db_session.commit()
    monkeypatch.setattr(model_adapters, "detect_objects", lambda *args: pytest.fail("unsupported class must not infer"))
    result = upload(client, evidence_case).json()
    assert result["status"] == "unsupported_product"
    assert result["detected_quantity"] is None


@pytest.mark.parametrize("raw,status", [(objects(2, .3), "insufficient_evidence"), ([], "insufficient_evidence"),
                                      (objects(1) + objects(1, .2), "manual_review_required")])
def test_unreliable_counts(client, evidence_case, monkeypatch, raw, status):
    monkeypatch.setattr(model_adapters, "detect_objects", lambda *args: raw)
    result = upload(client, evidence_case).json()
    assert result["status"] == status
    assert result["detected_quantity"] is None


def test_blurred_image_does_not_infer(client, evidence_case, monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (300, 300), "gray").save(buffer, format="PNG")
    evidence_case.payload = buffer.getvalue()
    monkeypatch.setattr(model_adapters, "detect_objects", lambda *args: pytest.fail("poor quality"))
    result = upload(client, evidence_case).json()
    assert result["status"] == "insufficient_evidence"
    assert result["detected_quantity"] is None


def test_model_unavailable_is_persisted(client, evidence_case):
    response = upload(client, evidence_case)
    assert response.status_code == 201
    assert response.json()["status"] == "model_unavailable"


@pytest.mark.parametrize("count", [1, 2])
def test_video_tracks_are_never_summed(db_session, evidence_case, mock_video_frames, monkeypatch, count):
    mock_video_frames(5)
    monkeypatch.setattr(model_adapters, "detect_objects", lambda *args: objects(count))
    result = service.evaluate_quantity(db_session, evidence_case.order.order_id, evidence_case.session.id, evidence_case.path, "video")
    assert result.detected_quantity == count
    assert len({d.track_id for d in result.detections}) == count
    assert len(result.detections) == count * 5


def test_variable_video_counts_require_review(db_session, evidence_case, mock_video_frames, monkeypatch):
    mock_video_frames(3)
    values = iter([objects(1), objects(2), objects(1)])
    monkeypatch.setattr(model_adapters, "detect_objects", lambda *args: next(values))
    result = service.evaluate_quantity(db_session, evidence_case.order.order_id, evidence_case.session.id, evidence_case.path, "video")
    assert result.status == "manual_review_required" and result.detected_quantity is None


@pytest.mark.parametrize("order_value,session_value,status", [
    ("missing", None, 404), (None, "missing", 404), ("DEMO-AMZ-402-9981245-11002", None, 409),
])
def test_relationship_validation(client, evidence_case, order_value, session_value, status):
    data = dict(evidence_case.data)
    if order_value:
        data["order_id"] = order_value
    if session_value:
        data["verification_session_id"] = session_value
    response = upload(client, evidence_case, **data)
    assert response.status_code == status


def test_unlinked_session_cannot_be_adopted(client, evidence_case, db_session):
    evidence_case.session.order_id = None
    db_session.commit()
    assert upload(client, evidence_case).status_code == 409


def test_duplicate_and_explicit_regeneration(client, evidence_case, db_session, monkeypatch):
    monkeypatch.setattr(model_adapters, "detect_objects", lambda *args: objects(1))
    first = upload(client, evidence_case).json()
    assert upload(client, evidence_case).status_code == 409
    monkeypatch.setattr(model_adapters, "detect_objects", lambda *args: objects(2))
    updated = upload(client, evidence_case, regenerate="true")
    assert updated.status_code == 200
    assert updated.json()["quantity_analysis_id"] == first["quantity_analysis_id"]
    assert updated.json()["status"] == "match"
    assert db_session.query(QuantityVerificationRecord).count() == 1


def test_invalid_stored_quantity(client, evidence_case, db_session):
    evidence_case.order.items[0].expected_quantity = 0
    db_session.commit()
    assert upload(client, evidence_case).status_code == 400


@pytest.mark.parametrize("filename,mime,code", [("test.exe", "application/octet-stream", 415), ("test.png", "text/plain", 415), ("test.jpg", "image/jpeg", 400)])
def test_media_validation(client, evidence_case, filename, mime, code):
    result = client.post("/api/quantity-verification/analyze", data=evidence_case.data,
                         files={"file": (filename, evidence_case.payload, mime)})
    assert result.status_code == code


def test_size_limit(client, evidence_case, monkeypatch):
    from app.services import file_handler
    monkeypatch.setattr(file_handler, "MAX_FILE_SIZE", 10)
    assert upload(client, evidence_case).status_code == 413


def test_empty_corrupt_and_missing_uploads(client, evidence_case):
    for payload in (b"", b"not an image"):
        evidence_case.payload = payload
        assert upload(client, evidence_case).status_code == 400
    assert client.post("/api/quantity-verification/analyze", data=evidence_case.data).status_code == 422
    assert client.get("/api/quantity-verification/missing").status_code == 404


def test_real_video_upload_pipeline(client, evidence_case, tmp_path, monkeypatch):
    import cv2
    import numpy as np
    path = tmp_path / "input.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (320, 320))
    assert writer.isOpened()
    try:
        for i in range(35):
            frame = np.asarray(evidence_case.image).copy()
            cv2.circle(frame, (40 + i * 5, 150), 25, (255, 255, 255), -1)
            writer.write(frame)
    finally:
        writer.release()
    monkeypatch.setattr(model_adapters, "detect_objects", lambda *args: objects(2))
    response = client.post("/api/quantity-verification/analyze", data=evidence_case.data,
                           files={"file": ("input.avi", path.read_bytes(), "video/x-msvideo")})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "match" and result["detected_quantity"] == 2
    assert len(result["supporting_frames"]) >= 2
    assert result["supporting_frames"][-1]["timestamp_seconds"] > 0


def test_corrupt_video_degrades(client, evidence_case):
    response = client.post("/api/quantity-verification/analyze", data=evidence_case.data,
                           files={"file": ("bad.mp4", b"\x00\x00\x00\x18ftypisom" + b"0" * 40, "video/mp4")})
    assert response.status_code == 201
    assert response.json()["status"] == "insufficient_evidence"


@pytest.mark.parametrize("raw", [
    [{"box": [0, 0, 80, 120], "class_name": "cup", "confidence": .95}],
    objects(1) * 2,
    objects(1) + [{"box": [float("nan"), 0, 40, 50], "class_name": "cup", "confidence": .9}],
])
def test_incomplete_boxes_do_not_verify(client, evidence_case, monkeypatch, raw):
    monkeypatch.setattr(model_adapters, "detect_objects", lambda *args: raw)
    result = upload(client, evidence_case).json()
    assert result["status"] == "manual_review_required"
    assert result["detected_quantity"] is None
