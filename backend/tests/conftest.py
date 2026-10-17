import os
import tempfile
from types import SimpleNamespace
import pytest
from pathlib import Path

# Isolation must precede importing app.main: Settings creates storage directories
# during import, and imported session factories otherwise retain the dev engine.
_test_parent = Path(__file__).resolve().parents[2] / ".verification"
_test_parent.mkdir(exist_ok=True)
_test_root = Path(tempfile.mkdtemp(prefix="suite-", dir=_test_parent))
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
for _kind in ("UPLOAD", "FRAME", "RESULT", "REPORT", "REFERENCE"):
    os.environ[f"{_kind}_DIR"] = str(_test_root / _kind.lower())
os.environ["MODEL_CACHE_DIR"] = str(_test_root / "models")
os.environ["TEMP"] = str(_test_root)
os.environ["TMP"] = str(_test_root)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.config
from app.database import connection
from app.database.connection import Base, get_db, init_db
from app.database.seed import seed_demonstration_orders
from app.main import app as fastapi_app
import app.services.analysis_service
import app.services.file_handler
import app.services.frame_extractor
import app.services.reference_service
import app.services.report_service
import app.database.seed
import app.services.ocr_service


def pytest_configure(config):
    # Never let pytest cleanup touch a caller-supplied development directory.
    config.option.basetemp = str(_test_root / "pytest")


@pytest.fixture(autouse=True)
def offline_models(monkeypatch):
    """Normal tests mock OCR and optional adapters; no model downloads."""
    monkeypatch.setattr(app.services.ocr_service, "get_reader", lambda: SimpleNamespace(readtext=lambda *args, **kwargs: []))
    monkeypatch.setattr(app.config.settings, "enable_quantity_model", False)
    monkeypatch.setattr(app.config.settings, "enable_condition_model", False)
    monkeypatch.setattr(app.config.settings, "model_offline", True)
    monkeypatch.setattr(app.config.settings, "enable_openclip", False)
    if os.environ.get("RUN_MODEL_INTEGRATION") != "1":
        import socket
        original_connect = socket.socket.connect
        def deny_network(sock, address):
            # Windows asyncio builds its wakeup socket pair over loopback.
            if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost"):
                return original_connect(sock, address)
            raise AssertionError("Normal tests must not access the network")
        monkeypatch.setattr(socket.socket, "connect", deny_network)

TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def configure_test_environment(tmp_path_factory):
    """Isolate all database and storage directories for the test suite."""
    test_storage = tmp_path_factory.mktemp("test_storage")
    uploads = test_storage / "uploads"
    frames = test_storage / "frames"
    results = test_storage / "results"
    reports = test_storage / "reports"
    references = test_storage / "references"

    for d in (uploads, frames, results, reports, references):
        d.mkdir(parents=True, exist_ok=True)

    # Patch config paths
    app.config.settings.upload_dir = uploads
    app.config.settings.frame_dir = frames
    app.config.settings.result_dir = results
    app.config.settings.report_dir = reports
    app.config.settings.reference_dir = references

    app.config.STORAGE_DIR = test_storage
    app.config.UPLOAD_DIR = uploads
    app.config.FRAME_DIR = frames
    app.config.RESULT_DIR = results
    app.config.REPORT_DIR = reports
    app.config.REFERENCE_DIR = references

    # Patch module level path references in services
    app.services.analysis_service.RESULT_DIR = results
    app.services.file_handler.STORAGE_DIR = test_storage
    app.services.file_handler.UPLOAD_DIR = uploads
    app.services.file_handler.FRAME_DIR = frames
    app.services.frame_extractor.FRAME_DIR = frames
    app.services.reference_service.STORAGE_DIR = test_storage
    app.services.reference_service.REFERENCE_DIR = references
    app.services.report_service.STORAGE_DIR = test_storage
    app.services.report_service.REPORT_DIR = reports

    # Override database connection globals
    orig_engine = connection.engine
    orig_session_local = connection.SessionLocal

    connection.engine = test_engine
    connection.SessionLocal = TestingSessionLocal
    app.database.seed.SessionLocal = TestingSessionLocal
    app.services.analysis_service.SessionLocal = TestingSessionLocal

    yield

    # Restore
    connection.engine = orig_engine
    connection.SessionLocal = orig_session_local
    app.database.seed.SessionLocal = orig_session_local
    app.services.analysis_service.SessionLocal = orig_session_local


@pytest.fixture(scope="function")
def db_session():
    """Create an isolated in-memory database schema for each test function."""
    init_db(target_engine=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def seeded_db(db_session):
    """Database session pre-populated with demonstration orders."""
    seed_demonstration_orders(session=db_session)
    return db_session


@pytest.fixture(scope="function")
def client(seeded_db):
    """FastAPI TestClient with isolated database session override pre-populated with seed orders."""
    def override_get_db():
        try:
            yield seeded_db
        finally:
            pass

    fastapi_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def evidence_case(client, seeded_db, tmp_path):
    import io
    from PIL import Image, ImageDraw
    from app.database.models import Order, VerificationSession
    order = seeded_db.query(Order).filter_by(order_id="OD290920260550001234").one()
    order.items[0].product_name = "cup"
    order.items[0].expected_quantity = 2
    session = VerificationSession(order_id=order.id, verification_status="verified",
                                  decoded_code=order.barcode_value, label_analysis_status="verified")
    seeded_db.add(session)
    seeded_db.commit()
    image = Image.new("RGB", (320, 320), "gray")
    draw = ImageDraw.Draw(image)
    for x in range(20, 300, 20):
        draw.rectangle((x, 20, x + 8, 290), fill="navy", outline="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    path = tmp_path / "evidence.png"
    path.write_bytes(buffer.getvalue())
    return SimpleNamespace(order=order, session=session, path=path, image=image,
                           payload=buffer.getvalue(), data={"order_id": order.order_id,
                           "verification_session_id": session.id})


@pytest.fixture
def mock_video_frames(monkeypatch, evidence_case):
    from app.services import evidence_service
    from app.services.frame_selection_service import CandidateFrame
    from app.schemas.evidence import SupportingFrame

    def configure(count=3):
        frames = [CandidateFrame(evidence_case.path, i, float(i), 500.0, f"frame_{i:04}.jpg") for i in range(count)]
        metadata = [SupportingFrame(frame_filename=f.frame_name, frame_index=f.frame_index,
                                    timestamp_seconds=f.timestamp_seconds, sharpness=f.sharpness,
                                    quality_adequate=True) for f in frames]
        monkeypatch.setattr(evidence_service, "prepare_evidence", lambda *args: (frames, metadata))
        return frames
    return configure
