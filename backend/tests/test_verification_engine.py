from app.schemas.enums import Platform, VerificationStatus
from app.schemas.verification import BarcodeVerificationRequest
from app.services.verification_engine import verify_barcode


def test_unreadable_barcode(seeded_db):
    req = BarcodeVerificationRequest(barcode_value="")
    res = verify_barcode(db=seeded_db, request=req)
    assert res.verification_status == VerificationStatus.UNREADABLE
    assert len(res.mismatch_reasons) > 0
    assert res.matched_order is None


def test_order_not_found_barcode(seeded_db):
    req = BarcodeVerificationRequest(barcode_value="UNKNOWN-BARCODE-99999")
    res = verify_barcode(db=seeded_db, request=req)
    assert res.verification_status == VerificationStatus.ORDER_NOT_FOUND
    assert res.matched_order is None
    assert "does not match any registered order" in res.mismatch_reasons[0]


def test_valid_verified_barcode(seeded_db):
    req = BarcodeVerificationRequest(
        barcode_value="FMPC6414359435",
        expected_platform=Platform.FLIPKART,
        expected_product_name="Rocsil Analog Watch",
        expected_customer_name="Demo Student User",
    )
    res = verify_barcode(db=seeded_db, request=req)
    assert res.verification_status == VerificationStatus.VERIFIED
    assert res.matched_order is not None
    assert res.matched_order.order_id == "DEMO-OD-FLIPKART-88214"
    assert len(res.mismatch_reasons) == 0


def test_details_mismatch_platform(seeded_db):
    req = BarcodeVerificationRequest(
        barcode_value="FMPC6414359435",
        expected_platform=Platform.AMAZON,  # Claiming Amazon for a Flipkart order
    )
    res = verify_barcode(db=seeded_db, request=req)
    assert res.verification_status == VerificationStatus.DETAILS_MISMATCH
    assert any("Platform mismatch" in r for r in res.mismatch_reasons)


def test_details_mismatch_customer_name(seeded_db):
    req = BarcodeVerificationRequest(
        barcode_value="FMPC6414359435",
        expected_customer_name="Completely Different Person",
    )
    res = verify_barcode(db=seeded_db, request=req)
    assert res.verification_status == VerificationStatus.DETAILS_MISMATCH
    assert any("Customer name mismatch" in r for r in res.mismatch_reasons)


def test_repeated_scan_allowed_before_claim_finalization(client, seeded_db):
    # First verification request
    payload = {
        "barcode_value": "FMPC6414359435",
        "expected_platform": "flipkart",
    }
    first_res = client.post("/api/verification/barcode", json=payload)
    assert first_res.status_code == 200
    assert first_res.json()["verification_status"] == "verified"

    # Second verification request (repeated scan before any finalized complaint)
    second_res = client.post("/api/verification/barcode", json=payload)
    assert second_res.status_code == 200
    assert second_res.json()["verification_status"] == "verified"


def test_already_used_after_simulated_claim_finalization(client, seeded_db):
    payload = {
        "barcode_value": "FMPC6414359435",
        "expected_platform": "flipkart",
    }
    first_res = client.post("/api/verification/barcode", json=payload)
    assert first_res.status_code == 200
    session_id = first_res.json()["verification_session_id"]

    # Finalize the claim for this session
    finalize_res = client.post(f"/api/verification/claim/finalize/{session_id}")
    assert finalize_res.status_code == 200
    assert finalize_res.json()["is_claim_finalized"] is True

    # Subsequent verification attempt for the same order should yield ALREADY_USED
    third_res = client.post("/api/verification/barcode", json=payload)
    assert third_res.status_code == 200
    assert third_res.json()["verification_status"] == "already_used"
    assert any("already been finalized" in r for r in third_res.json()["mismatch_reasons"])
