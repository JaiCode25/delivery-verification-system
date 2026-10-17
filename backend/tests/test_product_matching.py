import io
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageEnhance

from app.database.connection import SessionLocal, init_db
from app.database.models import Order, VerificationSession
from app.database.seed import seed_demonstration_orders
from app.main import app
from app.schemas.enums import ProductMatchDecision

from app.services.image_similarity import (
    compute_colour_similarity,
    compute_orb_feature_similarity,
    compute_perceptual_hash_similarity,
    compute_semantic_similarity,
)
from app.services.product_matching_service import (
    compute_text_similarity,
    evaluate_candidate_matching,
)


def _generate_synthetic_product_image(text="VGR V-055", base_color="navy", shape_color="cyan") -> Image.Image:
    """Generate a synthetic patterned product image with recognizable features and text."""
    img = Image.new("RGB", (300, 300), color=base_color)
    draw = ImageDraw.Draw(img)
    # Add geometry and contrast patterns for ORB and pHash
    draw.rectangle([30, 30, 270, 270], outline="gold", width=4)
    draw.ellipse([60, 60, 240, 240], fill=shape_color, outline="white", width=3)
    draw.line([30, 30, 270, 270], fill="red", width=3)
    draw.line([30, 270, 270, 30], fill="green", width=3)
    draw.text((80, 140), text, fill="white")
    return img


def test_perceptual_hash_same_and_different_images():
    img1 = _generate_synthetic_product_image("VGR V-055", "navy", "cyan")
    img2 = _generate_synthetic_product_image("VGR V-055", "navy", "cyan")
    img_diff = _generate_synthetic_product_image("Other Product", "yellow", "red")

    score_same = compute_perceptual_hash_similarity(img1, img2)
    score_diff = compute_perceptual_hash_similarity(img1, img_diff)

    assert score_same >= 0.95
    assert score_diff < score_same
    assert 0.0 <= score_diff <= 1.0


def test_colour_similarity_hsv():
    img1 = _generate_synthetic_product_image("VGR", "navy", "cyan")
    img2 = _generate_synthetic_product_image("VGR", "navy", "cyan")
    img_yellow = Image.new("RGB", (300, 300), color="yellow")

    score_same = compute_colour_similarity(img1, img2)
    score_diff = compute_colour_similarity(img1, img_yellow)

    assert score_same >= 0.90
    assert score_diff < score_same
    assert 0.0 <= score_diff <= 1.0


def test_orb_features_and_blank_image_safety():
    img1 = _generate_synthetic_product_image("Pattern A", "blue", "white")
    img2 = _generate_synthetic_product_image("Pattern A", "blue", "white")
    blank = Image.new("RGB", (300, 300), color="white")

    score_same = compute_orb_feature_similarity(img1, img2)
    score_blank = compute_orb_feature_similarity(img1, blank)

    assert score_same > 0.50
    assert score_blank == 0.0
    assert 0.0 <= score_same <= 1.0


def test_text_similarity_computation():
    detected = ["VGR Professional Hair Trimmer", "Model V-055", "Made in India"]
    score, matches = compute_text_similarity(
        detected_texts=detected,
        expected_brand="VGR",
        expected_model="V-055",
        expected_name="VGR V-055 Professional Hair Trimmer",
        expected_colour="Black/Blue",
    )
    assert score >= 0.80
    assert len(matches) >= 2

    # Missing OCR text
    empty_score, empty_matches = compute_text_similarity(
        detected_texts=[],
        expected_brand="VGR",
        expected_model="V-055",
        expected_name="VGR V-055",
        expected_colour="Black",
    )
    assert empty_score == 0.0
    assert empty_matches == []


def test_missing_references_yields_insufficient_evidence(seeded_db, tmp_path):
    order_id = "DEMO-OD-FLIPKART-88214"
    image = _generate_synthetic_product_image()
    candidate = tmp_path / "test_cand.jpg"
    image.save(candidate)
    image.close()
    result = evaluate_candidate_matching(
        db=seeded_db, order_id=order_id, media_path=candidate, media_type="image",
    )
    assert result.decision == ProductMatchDecision.INSUFFICIENT_EVIDENCE
    assert result.overall_match_score == 0.0
    assert len(result.warnings) > 0
    # tmp_path and seeded_db own their cleanup. Immediate unlinking can race
    # Windows/OneDrive file scanning; never delete development evidence here.



def test_end_to_end_product_matching_flow(client, db_session):
    order_id = "OD290920260550001234"
    ref_img = _generate_synthetic_product_image("VGR V-055", "navy", "cyan")

    order = db_session.query(Order).filter(Order.order_id == order_id).first()
    session = VerificationSession(
        order_id=order.id,
        decoded_code="VGR055-DEMO-2026-01",
        barcode_type="QR_CODE",
        verification_status="verified",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    session_id = session.id

    # 1. Upload reference image
    buf = io.BytesIO()
    ref_img.save(buf, format="JPEG")
    buf.seek(0)
    upload_res = client.post(
        f"/api/orders/{order_id}/references",
        files=[("files", ("ref_vgr.jpg", buf.read(), "image/jpeg"))],
        data={"product_name": "VGR V-055 Professional Hair Trimmer", "brand": "VGR", "model": "V-055", "colour": "Black/Blue"},
    )
    assert upload_res.status_code == 201

    # 2. Match identical/similar candidate image
    cand_buf = io.BytesIO()
    ref_img.save(cand_buf, format="JPEG")
    cand_buf.seek(0)

    match_res = client.post(
        "/api/product-matching/analyze",
        data={"order_id": order_id, "verification_session_id": session_id},
        files={"file": ("delivered_trimmer.jpg", cand_buf.read(), "image/jpeg")},
    )
    assert match_res.status_code == 200
    res_data = match_res.json()
    assert res_data["order_id"] == order_id
    assert res_data["decision"] in (ProductMatchDecision.MATCH.value, ProductMatchDecision.LIKELY_MATCH.value)
    assert res_data["overall_match_score"] >= 0.65
    assert 0.0 <= res_data["perceptual_hash_score"] <= 1.0
    assert 0.0 <= res_data["colour_similarity_score"] <= 1.0
    assert 0.0 <= res_data["feature_match_score"] <= 1.0


def test_product_matching_with_distinct_image_yields_mismatch(client, db_session):
    order_id = "OD290920260550001234"
    order = db_session.query(Order).filter(Order.order_id == order_id).first()
    session = VerificationSession(
        order_id=order.id,
        decoded_code="VGR055-DEMO-2026-01",
        barcode_type="QR_CODE",
        verification_status="verified",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    session_id = session.id

    # Upload reference image for the order
    ref_img = _generate_synthetic_product_image("VGR V-055", "navy", "cyan")
    ref_buf = io.BytesIO()
    ref_img.save(ref_buf, format="JPEG")
    ref_buf.seek(0)
    upload_res = client.post(
        f"/api/orders/{order_id}/references",
        files=[("files", ("ref_vgr.jpg", ref_buf.read(), "image/jpeg"))],
        data={"product_name": "VGR V-055 Professional Hair Trimmer", "brand": "VGR", "model": "V-055", "colour": "Black/Blue"},
    )
    assert upload_res.status_code == 201

    # Totally different candidate (e.g. coffee jar / bright red square)
    diff_img = Image.new("RGB", (300, 300), color="orange")
    draw = ImageDraw.Draw(diff_img)
    draw.rectangle([10, 10, 50, 50], fill="white")

    diff_buf = io.BytesIO()
    diff_img.save(diff_buf, format="JPEG")
    diff_buf.seek(0)

    match_res = client.post(
        "/api/product-matching/analyze",
        data={"order_id": order_id, "verification_session_id": session_id},
        files={"file": ("different_product.jpg", diff_buf.read(), "image/jpeg")},
    )
    assert match_res.status_code == 200
    res_data = match_res.json()
    assert res_data["decision"] == ProductMatchDecision.MISMATCH.value
    assert res_data["overall_match_score"] < 0.55



def test_openclip_disabled_by_default():
    img1 = _generate_synthetic_product_image()
    img2 = _generate_synthetic_product_image()
    score = compute_semantic_similarity(img1, img2)
    assert score is None


def test_one_bit_and_palette_image_support():
    from app.services.image_preprocessor import preprocess_image_for_ocr

    # 1-bit monochrome image (mode 1)
    one_bit_img = Image.new("1", (200, 200), color=1)
    draw = ImageDraw.Draw(one_bit_img)
    draw.text((20, 20), "MONO TEST", fill=0)

    # Preprocessing must succeed and return uint8 ndarray without OpenCV bool error
    processed, scale = preprocess_image_for_ocr(one_bit_img)
    assert isinstance(processed, np.ndarray)
    assert processed.dtype == np.uint8
    assert scale > 0

    # Perceptual hash and colour similarity must succeed without throwing
    phash_score = compute_perceptual_hash_similarity(one_bit_img, one_bit_img)
    assert phash_score >= 0.90

    colour_score = compute_colour_similarity(one_bit_img, one_bit_img)
    assert 0.0 <= colour_score <= 1.0

    # Palette image (mode P)
    palette_img = Image.new("P", (200, 200))
    palette_img.putpalette([255, 0, 0, 0, 255, 0, 0, 0, 255] * 85)
    processed_p, scale_p = preprocess_image_for_ocr(palette_img)
    assert isinstance(processed_p, np.ndarray)
    assert processed_p.dtype == np.uint8

