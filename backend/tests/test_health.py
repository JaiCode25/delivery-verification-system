from app.database.seed import seed_demonstration_orders


def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "docs" in data


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_seed_idempotency(db_session):
    first_run = seed_demonstration_orders(session=db_session)
    assert len(first_run) == 3

    second_run = seed_demonstration_orders(session=db_session)
    assert len(second_run) == 0
