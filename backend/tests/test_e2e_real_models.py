"""End-to-end real-model verification test suite.

Exercises real YOLOv8n and OpenCLIP models from local cache against isolated storage
and in-memory database to validate all quantity, condition, report, and API edge cases.
"""
import io
import os
from pathlib import Path
import pytest
from PIL import Image, ImageDraw, ImageFilter

from app.config import settings
from app.database.models import Order, ProductReference, VerificationSession


@pytest.mark.skipif(
    os.environ.get("RUN_MODEL_INTEGRATION") != "1",
    reason="Set RUN_MODEL_INTEGRATION=1 and MODEL_SMOKE_CACHE to run real-model end-to-end suite"
)
class TestE2ERealModels:

    @pytest.fixture(autouse=True)
    def setup_real_models(self, monkeypatch):
        cache = os.environ.get("MODEL_SMOKE_CACHE") or str(settings.model_cache_dir)
        monkeypatch.setattr(settings, "model_cache_dir", Path(cache))
        monkeypatch.setattr(settings, "enable_quantity_model", True)
        monkeypatch.setattr(settings, "enable_condition_model", True)
        monkeypatch.setattr(settings, "model_offline", True)
        monkeypatch.setattr(settings, "model_device", "cpu")

    def _create_image_file(self, blur=False, size=(320, 320)) -> bytes:
        img = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(img)
        # Draw high-contrast shapes to ensure Laplacian variance / sharpness >= 30
        for x in range(10, size[0], 20):
            draw.line([(x, 0), (x, size[1])], fill="black", width=3)
        for y in range(10, size[1], 20):
            draw.line([(0, y), (size[0], y)], fill="navy", width=3)
        if blur:
            img = img.filter(ImageFilter.GaussianBlur(radius=15))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_e2e_full_verification_flow(self, client, seeded_db):
        """Complete verification flow: QR/label -> reference -> match -> quantity -> condition -> report."""
        # 1. Setup order with product mapped to COCO class
        order = seeded_db.query(Order).filter_by(order_id="OD290920260550001234").one()
        order.items[0].product_name = "cup"
        order.items[0].expected_quantity = 1
        seeded_db.commit()

        session = VerificationSession(
            order_id=order.id,
            verification_status="verified",
            decoded_code=order.barcode_value,
            label_analysis_status="verified",
            original_filename="label.png",
            input_type="image",
        )
        seeded_db.add(session)
        seeded_db.commit()

        img_bytes = self._create_image_file()

        # Upload a reference image so product matching can succeed
        ref_res = client.post(
            f"/api/orders/{order.order_id}/references",
            files={"files": ("ref1.png", img_bytes, "image/png")},
        )
        assert ref_res.status_code == 201

        # 2. Product matching
        pm_res = client.post(
            "/api/product-matching/analyze",
            data={"order_id": order.order_id, "verification_session_id": session.id},
            files={"file": ("product.png", img_bytes, "image/png")},
        )
        assert pm_res.status_code in (200, 201)

        # 3. Quantity verification
        qty_res = client.post(
            "/api/quantity-verification/analyze",
            data={"order_id": order.order_id, "verification_session_id": session.id},
            files={"file": ("qty.png", img_bytes, "image/png")},
        )
        assert qty_res.status_code in (200, 201)
        qty_data = qty_res.json()
        assert qty_data["target_class"] == "cup"
        assert qty_data["expected_quantity"] == 1
        assert qty_data["status"] in ("match", "mismatch", "insufficient_evidence", "manual_review_required")

        # 4. Condition analysis
        cond_res = client.post(
            "/api/condition-analysis/analyze",
            data={
                "order_id": order.order_id,
                "verification_session_id": session.id,
                "evidence_type": "product",
            },
            files={"file": ("cond.png", img_bytes, "image/png")},
        )
        assert cond_res.status_code in (200, 201)
        cond_data = cond_res.json()
        assert cond_data["status"] in ("no_visible_damage", "damaged", "likely_damaged", "manual_review_required", "insufficient_evidence")

        # 5. Report generation
        rep_res = client.post(f"/api/reports/generate/{session.id}")
        assert rep_res.status_code in (200, 201)
        report = rep_res.json()

        assert report["verification_session_id"] == session.id
        assert report["order_id"] == order.order_id
        assert report["quantity_verification"] is not None
        assert report["condition_analysis"] is not None
        assert report["channel_statuses"]["quantity_verification"] == qty_data["status"]
        assert report["channel_statuses"]["condition_analysis"] == cond_data["status"]
        assert "masked_customer_name" in report
        assert "***" in report["masked_customer_name"]

        # 6. Retrieve JSON & HTML report
        rep_id = report["report_id"]
        get_json = client.get(f"/api/reports/{rep_id}")
        assert get_json.status_code == 200
        assert get_json.json()["report_id"] == rep_id

        get_html = client.get(f"/api/reports/{rep_id}/html")
        assert get_html.status_code == 200
        assert "Delivery Verification Report" in get_html.text

        # 7. Report regeneration
        regen_rep = client.post(f"/api/reports/generate/{session.id}")
        assert regen_rep.status_code in (200, 201)
        assert regen_rep.json()["report_id"] == rep_id

    def test_quantity_unsupported_product_vgr(self, client, seeded_db):
        """VGR Hair Trimmer (unsupported product) yields unsupported_product status."""
        order = seeded_db.query(Order).filter_by(order_id="OD290920260550001234").one()
        session = VerificationSession(
            order_id=order.id, verification_status="verified", label_analysis_status="verified"
        )
        seeded_db.add(session)
        seeded_db.commit()

        img_bytes = self._create_image_file()
        qty_res = client.post(
            "/api/quantity-verification/analyze",
            data={"order_id": order.order_id, "verification_session_id": session.id},
            files={"file": ("trimmer.png", img_bytes, "image/png")},
        )
        assert qty_res.status_code in (200, 201)
        data = qty_res.json()
        assert data["status"] == "unsupported_product"
        assert data["detected_quantity"] is None
        assert data["target_class"] is None

    def test_quantity_blurred_evidence(self, client, seeded_db):
        """Blurred evidence yields insufficient_evidence."""
        order = seeded_db.query(Order).filter_by(order_id="OD290920260550001234").one()
        order.items[0].product_name = "cup"
        session = VerificationSession(
            order_id=order.id, verification_status="verified", label_analysis_status="verified"
        )
        seeded_db.add(session)
        seeded_db.commit()

        blur_bytes = self._create_image_file(blur=True)
        qty_res = client.post(
            "/api/quantity-verification/analyze",
            data={"order_id": order.order_id, "verification_session_id": session.id},
            files={"file": ("blur.png", blur_bytes, "image/png")},
        )
        assert qty_res.status_code in (200, 201)
        data = qty_res.json()
        assert data["status"] == "insufficient_evidence"

    def test_condition_normal_product(self, client, seeded_db):
        """Clear normal product image yields no_visible_damage or valid score."""
        order = seeded_db.query(Order).filter_by(order_id="OD290920260550001234").one()
        session = VerificationSession(
            order_id=order.id, verification_status="verified", label_analysis_status="verified"
        )
        seeded_db.add(session)
        seeded_db.commit()

        img_bytes = self._create_image_file()
        cond_res = client.post(
            "/api/condition-analysis/analyze",
            data={
                "order_id": order.order_id,
                "verification_session_id": session.id,
                "evidence_type": "product",
            },
            files={"file": ("clean_product.png", img_bytes, "image/png")},
        )
        assert cond_res.status_code in (200, 201)
        data = cond_res.json()
        assert data["status"] in ("no_visible_damage", "manual_review_required", "insufficient_evidence")

    def test_condition_unknown_evidence_type(self, client, seeded_db):
        """Unknown evidence type requires manual review."""
        order = seeded_db.query(Order).filter_by(order_id="OD290920260550001234").one()
        session = VerificationSession(
            order_id=order.id, verification_status="verified", label_analysis_status="verified"
        )
        seeded_db.add(session)
        seeded_db.commit()

        img_bytes = self._create_image_file()
        cond_res = client.post(
            "/api/condition-analysis/analyze",
            data={
                "order_id": order.order_id,
                "verification_session_id": session.id,
                "evidence_type": "unknown",
            },
            files={"file": ("unknown.png", img_bytes, "image/png")},
        )
        assert cond_res.status_code in (200, 201)
        data = cond_res.json()
        assert data["status"] == "manual_review_required"

    def test_disabled_model_returns_model_unavailable(self, client, seeded_db, monkeypatch):
        """Disabled models yield model_unavailable without 500 error."""
        monkeypatch.setattr(settings, "enable_quantity_model", False)
        monkeypatch.setattr(settings, "enable_condition_model", False)

        order = seeded_db.query(Order).filter_by(order_id="OD290920260550001234").one()
        order.items[0].product_name = "cup"
        session = VerificationSession(
            order_id=order.id, verification_status="verified", label_analysis_status="verified"
        )
        seeded_db.add(session)
        seeded_db.commit()

        img_bytes = self._create_image_file()

        qty_res = client.post(
            "/api/quantity-verification/analyze",
            data={"order_id": order.order_id, "verification_session_id": session.id},
            files={"file": ("qty.png", img_bytes, "image/png")},
        )
        assert qty_res.status_code in (200, 201)
        assert qty_res.json()["status"] == "model_unavailable"

        cond_res = client.post(
            "/api/condition-analysis/analyze",
            data={
                "order_id": order.order_id,
                "verification_session_id": session.id,
                "evidence_type": "product",
            },
            files={"file": ("cond.png", img_bytes, "image/png")},
        )
        assert cond_res.status_code in (200, 201)
        assert cond_res.json()["status"] == "model_unavailable"

    def test_wrong_order_session_pairing_rejected(self, client, seeded_db):
        """Using a session ID from another order returns 409 Conflict."""
        order1 = seeded_db.query(Order).filter_by(order_id="OD290920260550001234").one()
        order2 = Order(
            order_id="OD9999999999",
            tracking_id="TRACK999",
            platform="flipkart",
            customer_name="Test User",
            customer_email="test@example.com",
            delivery_address="123 Test St",
            barcode_value="TEST999",
        )
        seeded_db.add(order2)
        seeded_db.commit()

        session = VerificationSession(
            order_id=order1.id, verification_status="verified", label_analysis_status="verified"
        )
        seeded_db.add(session)
        seeded_db.commit()

        img_bytes = self._create_image_file()
        qty_res = client.post(
            "/api/quantity-verification/analyze",
            data={"order_id": "OD9999999999", "verification_session_id": session.id},
            files={"file": ("test.png", img_bytes, "image/png")},
        )
        assert qty_res.status_code == 409
