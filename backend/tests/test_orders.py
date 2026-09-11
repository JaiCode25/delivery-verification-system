def test_create_order_and_lookup(client):
    order_payload = {
        "order_id": "TEST-OD-99001",
        "tracking_id": "TRK-99001",
        "barcode_value": "BAR-99001",
        "platform": "amazon",
        "customer_name": "Test Customer",
        "customer_email": "test@example.com",
        "delivery_address": "Test Street 123, Pune",
        "order_status": "delivered",
        "items": [
            {
                "product_name": "Test Wireless Mouse",
                "brand": "Logitech",
                "model": "M330",
                "colour": "Black",
                "expected_quantity": 1,
                "product_url": "https://example.com/mouse",
            }
        ],
    }

    # 1. Create order
    create_res = client.post("/api/orders", json=order_payload)
    assert create_res.status_code == 201
    created_data = create_res.json()
    assert created_data["order_id"] == "TEST-OD-99001"
    assert len(created_data["items"]) == 1

    # 2. Duplicate order rejection
    dup_res = client.post("/api/orders", json=order_payload)
    assert dup_res.status_code == 400
    assert "already exists" in dup_res.json()["detail"]

    # 3. Lookup by order_id
    get_res = client.get("/api/orders/TEST-OD-99001")
    assert get_res.status_code == 200
    assert get_res.json()["tracking_id"] == "TRK-99001"

    # 4. Lookup by barcode
    barcode_res = client.get("/api/orders/barcode/BAR-99001")
    assert barcode_res.status_code == 200
    assert barcode_res.json()["order_id"] == "TEST-OD-99001"

    # 5. Lookup by tracking ID
    tracking_res = client.get("/api/orders/tracking/TRK-99001")
    assert tracking_res.status_code == 200
    assert tracking_res.json()["order_id"] == "TEST-OD-99001"


def test_order_not_found(client):
    res = client.get("/api/orders/NON-EXISTENT-ID")
    assert res.status_code == 404


def test_list_orders_pagination(client, seeded_db):
    res = client.get("/api/orders?limit=2&offset=0")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 3
    assert len(data["orders"]) == 2
    assert data["limit"] == 2
    assert data["offset"] == 0
