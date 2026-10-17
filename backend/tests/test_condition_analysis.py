import io
import pytest
from PIL import Image
from app.database.models import ConditionAnalysisRecord
from app.schemas.condition_analysis import EvidenceType, VisualCue
from app.services import model_adapters, condition_analysis_service as service


def upload(client, case, **extra):
    return client.post("/api/condition-analysis/analyze", data={**case.data, "evidence_type": "product", **extra},
                       files={"file": ("condition.png", case.payload, "image/png")})


def mock_cue(monkeypatch, supports=True):
    monkeypatch.setattr(service, "compare_surface", lambda image, refs, name: VisualCue(
        frame_filename=name, reference_id="ref", reference_aligned=True,
        supports_surface_change=supports, edge_change_fraction=.3 if supports else .01))


def test_strong_consistent_damage(db_session, evidence_case, mock_video_frames, monkeypatch):
    mock_video_frames(3)
    mock_cue(monkeypatch)
    monkeypatch.setattr(model_adapters, "classify_condition", lambda *args: (.97, .03))
    result = service.evaluate_condition(db_session, evidence_case.order.order_id, evidence_case.session.id,
                                        evidence_case.path, "video", EvidenceType.PRODUCT)
    assert result.status == "damaged" and result.damage_detected is True
    assert result.damage_categories == ["visible_surface_change"]
    assert db_session.get(ConditionAnalysisRecord, result.condition_analysis_id).details["status"] == "damaged"


@pytest.mark.parametrize("scores,support,status", [((.8, .2), True, "likely_damaged"),
                                                 ((.97, .03), True, "likely_damaged"),
                                                 ((.05, .95), False, "no_visible_damage"),
                                                 ((.55, .45), False, "manual_review_required"),
                                                 ((.99, .01), False, "manual_review_required")])
def test_image_results_persist(client, db_session, evidence_case, monkeypatch, scores, support, status):
    mock_cue(monkeypatch, support)
    monkeypatch.setattr(model_adapters, "classify_condition", lambda *args: scores)
    response = upload(client, evidence_case)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == status
    assert result["damage_detected"] is (False if status == "no_visible_damage" else None)
    assert client.get("/api/condition-analysis/" + result["condition_analysis_id"]).json() == result
    assert db_session.query(ConditionAnalysisRecord).count() == 1
    assert any("functional condition are not assessed" in w for w in result["warnings"])
    assert all("internal" not in category and "electrical" not in category for category in result["damage_categories"])


def test_blur_is_insufficient(client, evidence_case, monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (320, 320), "gray").save(buffer, format="PNG")
    evidence_case.payload = buffer.getvalue()
    monkeypatch.setattr(model_adapters, "classify_condition", lambda *args: pytest.fail("blurred evidence"))
    result = upload(client, evidence_case).json()
    assert result["status"] == "insufficient_evidence" and result["damage_detected"] is None


def test_contradictory_frames(db_session, evidence_case, mock_video_frames, monkeypatch):
    mock_video_frames(2)
    mock_cue(monkeypatch)
    scores = iter([(.98, .02), (.02, .98)])
    monkeypatch.setattr(model_adapters, "classify_condition", lambda *args: next(scores))
    result = service.evaluate_condition(db_session, evidence_case.order.order_id, evidence_case.session.id,
                                        evidence_case.path, "video", EvidenceType.PRODUCT)
    assert result.status == "manual_review_required" and result.damage_detected is None


def test_model_unavailable(client, evidence_case):
    response = upload(client, evidence_case)
    assert response.status_code == 201
    assert response.json()["status"] == "model_unavailable"
    assert response.json()["damage_detected"] is None


@pytest.mark.parametrize("extra,status", [({"order_id": "DEMO-AMZ-402-9981245-11002"}, 409),
                                        ({"order_id": "missing"}, 404),
                                        ({"verification_session_id": "missing"}, 404),
                                        ({"evidence_type": "electrical"}, 422)])
def test_validation(client, evidence_case, extra, status):
    assert upload(client, evidence_case, **extra).status_code == status


def test_regeneration(client, evidence_case, monkeypatch):
    first = upload(client, evidence_case).json()
    assert upload(client, evidence_case).status_code == 409
    monkeypatch.setattr(model_adapters, "classify_condition", lambda *args: (.02, .98))
    updated = upload(client, evidence_case, regenerate="true")
    assert updated.status_code == 200
    assert updated.json()["condition_analysis_id"] == first["condition_analysis_id"]
    assert updated.json()["status"] == "no_visible_damage"


def test_unknown_requires_review(client, evidence_case):
    result = upload(client, evidence_case, evidence_type="unknown").json()
    assert result["status"] == "manual_review_required"
    assert result["damage_detected"] is None


def test_packaging_is_not_compared_to_product_references(client, evidence_case, monkeypatch):
    monkeypatch.setattr(model_adapters, "classify_condition", lambda *args: (.99, .01))
    monkeypatch.setattr(service, "get_reference_absolute_paths", lambda *args: pytest.fail("product references are irrelevant"))
    result = upload(client, evidence_case, evidence_type="sealed_package").json()
    assert result["status"] == "manual_review_required"
    assert result["damage_detected"] is None


def test_surface_comparison_does_not_invent_damage(evidence_case):
    cue = service.compare_surface(evidence_case.image, [("same", evidence_case.path)], "frame.png")
    assert not cue.supports_surface_change
    assert not service.compare_surface(evidence_case.image, [], "frame.png").reference_aligned


def test_opened_package_prompts_do_not_penalize_normal_opening():
    positive, negative = model_adapters.condition_prompts("opened_package")
    assert not any("seal" in p for p in positive)
    assert any("normally opened" in p for p in negative)
