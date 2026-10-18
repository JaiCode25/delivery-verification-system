"""Tests for customer-facing product intake service and endpoints."""
import pytest
from app.database.models import Order, OrderItem, ProductReference, VerificationSession
from app.services.customer_intake_service import normalize_product_url


def test_normalize_product_url():
    raw = "https://www.amazon.in/dp/B08N5WRWNW/?ref=xyz&utm_source=google&tag=affil"
    norm = normalize_product_url(raw)
    assert norm == "https://www.amazon.in/dp/B08N5WRWNW"
    assert "utm_source" not in norm
    assert "ref" not in norm

    raw2 = "HTTPS://Flipkart.com/item/p-123/?b=2&a=1#section"
    norm2 = normalize_product_url(raw2)
    assert norm2.startswith("https://flipkart.com/item/p-123")
    assert "a=1" in norm2 and "b=2" in norm2
    assert "#section" not in norm2


def test_create_customer_submission_success(client):
    payload = {
        "product_name": "VGR V-055 Professional Hair Trimmer",
        "platform": "Flipkart",
        "product_url": "https://www.flipkart.com/vgr-v-055-trimmer/p/itm12345",
        "expected_quantity": 1,
        "colour_or_variant": "Black",
        "customer_name": "Demo Customer",
        "customer_email": "customer@example.com",
        "notes": "Left near door",
    }
    res = client.post("/api/customer/submissions", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert "submission_id" in data
    assert data["product_name"] == "VGR V-055 Professional Hair Trimmer"
    assert data["platform"] == "flipkart"
    assert data["expected_quantity"] == 1
    assert data["normalized_product_url"].startswith("https://www.flipkart.com/vgr-v-055-trimmer/p/itm12345")
    assert data["customer_name"] == "Demo Customer"
    assert data["customer_email"] == "customer@example.com"


def test_create_customer_submission_url_validation(client):
    # Non-HTTPS
    res1 = client.post("/api/customer/submissions", json={
        "product_name": "Test Product",
        "platform": "amazon",
        "product_url": "http://amazon.in/dp/123",
        "expected_quantity": 1,
    })
    assert res1.status_code == 422

    # Malformed URL without domain
    res2 = client.post("/api/customer/submissions", json={
        "product_name": "Test Product",
        "platform": "amazon",
        "product_url": "https://invalid",
        "expected_quantity": 1,
    })
    assert res2.status_code == 422


def test_create_customer_submission_quantity_bounds(client):
    # Quantity 0
    res1 = client.post("/api/customer/submissions", json={
        "product_name": "Test Product",
        "platform": "amazon",
        "product_url": "https://amazon.in/dp/123.html",
        "expected_quantity": 0,
    })
    assert res1.status_code == 422

    # Quantity > 100
    res2 = client.post("/api/customer/submissions", json={
        "product_name": "Test Product",
        "platform": "amazon",
        "product_url": "https://amazon.in/dp/123.html",
        "expected_quantity": 101,
    })
    assert res2.status_code == 422


def test_get_and_update_customer_submission(client):
    create_res = client.post("/api/customer/submissions", json={
        "product_name": "Initial Name",
        "platform": "amazon",
        "product_url": "https://amazon.in/dp/B001.html",
        "expected_quantity": 2,
    })
    assert create_res.status_code == 201
    sub_id = create_res.json()["submission_id"]

    # Get
    get_res = client.get(f"/api/customer/submissions/{sub_id}")
    assert get_res.status_code == 200
    assert get_res.json()["product_name"] == "Initial Name"

    # Patch
    patch_res = client.patch(f"/api/customer/submissions/{sub_id}", json={
        "product_name": "Updated Name",
        "expected_quantity": 3,
        "notes": "Updated note",
    })
    assert patch_res.status_code == 200
    updated_data = patch_res.json()
    assert updated_data["product_name"] == "Updated Name"
    assert updated_data["expected_quantity"] == 3
    assert updated_data["notes"] == "Updated note"


def test_demo_catalogue_reference_resolution(client, seeded_db):
    # The seeded order OD290920260550001234 has "VGR V-055 Professional Hair Trimmer" on flipkart
    # Upload a reference image to it so it has a reference
    order = seeded_db.query(Order).filter_by(order_id="OD290920260550001234").one()
    ref = ProductReference(
        order_id=order.id,
        order_item_id=order.items[0].id,
        relative_path="references/test.png",
        original_filename="vgr_ref.png",
        mime_type="image/png",
        file_size_bytes=1024,
    )
    seeded_db.add(ref)
    seeded_db.commit()

    # Submission with matching product and platform
    res = client.post("/api/customer/submissions", json={
        "product_name": "VGR V-055 Hair Trimmer",
        "platform": "flipkart",
        "product_url": "https://flipkart.com/vgr-055.html",
        "expected_quantity": 1,
    })
    assert res.status_code == 201
    data = res.json()
    resolved = data.get("resolved_reference")
    assert resolved is not None
    assert resolved["reference_source"] == "demo_catalogue"
    assert resolved["reference_id"] == ref.id
    assert resolved["match_score"] > 0.40

    # Submission for an unsupported product
    res2 = client.post("/api/customer/submissions", json={
        "product_name": "Completely Unrelated Luxury Watch",
        "platform": "blinkit",
        "product_url": "https://blinkit.com/item/watch.html",
        "expected_quantity": 1,
    })
    assert res2.status_code == 201
    resolved2 = res2.json().get("resolved_reference")
    assert resolved2 is not None
    assert resolved2["reference_source"] == "reference_unavailable"


def test_link_submission_to_session_with_contradictions(client, seeded_db):
    order = seeded_db.query(Order).filter_by(order_id="OD290920260550001234").one()
    session = VerificationSession(
        order_id=order.id,
        verification_status="verified",
        decoded_code=order.barcode_value,
        label_analysis_status="verified",
    )
    seeded_db.add(session)
    seeded_db.commit()

    # Create submission with contradictory platform and quantity
    sub_res = client.post("/api/customer/submissions", json={
        "product_name": "VGR V-055 Hair Trimmer",
        "platform": "amazon",  # Contradiction: order is flipkart
        "product_url": "https://amazon.in/dp/vgr.html",
        "expected_quantity": 4,  # Contradiction: order expected quantity is 1
        "customer_email": "other@example.com",  # Contradiction: order has demo email
    })
    sub_id = sub_res.json()["submission_id"]

    # Link session
    link_res = client.post(
        f"/api/customer/submissions/{sub_id}/link-session",
        json={"verification_session_id": session.id},
    )
    assert link_res.status_code == 200
    linked_data = link_res.json()
    assert linked_data["verification_session_id"] == session.id
    assert linked_data["order_id"] == order.id

    contradictions = linked_data["contradictions"]
    assert len(contradictions) >= 2
    fields = [c["field"] for c in contradictions]
    assert "platform" in fields
    assert "expected_quantity" in fields

    # Verify both customer and order values remain intact
    assert linked_data["platform"] == "amazon"
    assert linked_data["expected_quantity"] == 4
