import io
from pathlib import Path
import pytest
from PIL import Image

import app.config
from app.database.models import Order, OrderItem, ProductReference


def _create_sample_image_bytes(format_name="JPEG", color="blue", size=(100, 100)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format=format_name)
    buf.seek(0)
    return buf.read()


def test_upload_valid_reference_images(client):
    order_id = "OD290920260550001234"
    img1 = _create_sample_image_bytes("JPEG", "blue")
    img2 = _create_sample_image_bytes("PNG", "black")

    files = [
        ("files", ("trimmer_front.jpg", img1, "image/jpeg")),
        ("files", ("trimmer_box.png", img2, "image/png")),
    ]
    data = {
        "product_name": "VGR V-055 Professional Hair Trimmer",
        "brand": "VGR",
        "model": "V-055",
        "colour": "Black/Blue",
    }

    response = client.post(f"/api/orders/{order_id}/references", files=files, data=data)
    assert response.status_code == 201
    res_data = response.json()
    assert res_data["order_id"] == order_id
    assert res_data["total_references"] >= 2
    for ref in res_data["references"]:
        assert not ref["relative_path"].startswith("C:")
        assert "references/" in ref["relative_path"]
        assert (app.config.STORAGE_DIR / ref["relative_path"]).exists()


def test_list_order_references(client):
    order_id = "OD290920260550001234"
    img1 = _create_sample_image_bytes("JPEG", "blue")
    img2 = _create_sample_image_bytes("PNG", "black")

    files = [
        ("files", ("trimmer_front.jpg", img1, "image/jpeg")),
        ("files", ("trimmer_box.png", img2, "image/png")),
    ]
    upload_res = client.post(f"/api/orders/{order_id}/references", files=files)
    assert upload_res.status_code == 201

    response = client.get(f"/api/orders/{order_id}/references")
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["order_id"] == order_id
    assert len(res_data["references"]) >= 2


def test_reject_unsupported_reference_file(client):
    order_id = "OD290920260550001234"
    fake_txt = b"This is not an image file content."
    files = [("files", ("test.txt", fake_txt, "text/plain"))]

    response = client.post(f"/api/orders/{order_id}/references", files=files)
    assert response.status_code == 400


def test_reject_nonexistent_order_references(client):
    img = _create_sample_image_bytes("JPEG", "red")
    files = [("files", ("ref.jpg", img, "image/jpeg"))]

    response = client.post("/api/orders/NONEXISTENT-ORDER-9999/references", files=files)
    assert response.status_code == 404


def test_enforce_reference_image_limit(client):
    order_id = "DEMO-BLK-99381023"
    # Try uploading 6 images when max is 5
    files = [
        ("files", (f"img_{i}.jpg", _create_sample_image_bytes("JPEG", "green"), "image/jpeg"))
        for i in range(6)
    ]
    response = client.post(f"/api/orders/{order_id}/references", files=files)
    assert response.status_code == 400
    assert "max allowed" in response.json()["detail"]


def test_delete_reference_safely(client):
    order_id = "DEMO-AMZ-402-9981245-11002"
    img = _create_sample_image_bytes("JPEG", "yellow")
    files = [("files", ("ref_del.jpg", img, "image/jpeg"))]

    # Upload
    upload_res = client.post(f"/api/orders/{order_id}/references", files=files)
    assert upload_res.status_code == 201
    ref_id = upload_res.json()["references"][0]["id"]
    rel_path = upload_res.json()["references"][0]["relative_path"]
    physical_path = app.config.STORAGE_DIR / rel_path
    assert physical_path.exists()

    # Delete
    del_res = client.delete(f"/api/orders/{order_id}/references/{ref_id}")
    assert del_res.status_code == 200
    assert not physical_path.exists()

    # Verify no longer listed
    list_res = client.get(f"/api/orders/{order_id}/references")
    assert not any(r["id"] == ref_id for r in list_res.json()["references"])


def test_prevent_path_traversal_in_reference_upload(client):
    traversal_order_id = "../../../etc/passwd"
    img = _create_sample_image_bytes("JPEG", "purple")
    files = [("files", ("ref.jpg", img, "image/jpeg"))]

    response = client.post(f"/api/orders/{traversal_order_id}/references", files=files)
    # Must be rejected (404 not found or 400 bad request)
    assert response.status_code in (400, 404)
