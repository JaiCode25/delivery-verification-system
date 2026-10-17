from pathlib import Path
from sqlalchemy import create_engine, inspect
from app.config import settings
from app.database import connection
from app.database.models import Order
from app.database.connection import init_db
from app.services import file_handler, reference_service, report_service, frame_extractor


def test_all_storage_and_database_are_isolated(db_session):
    assert db_session.get_bind().url.database == ":memory:"
    assert connection.engine.url.database == ":memory:"
    root = Path(__file__).resolve().parents[2] / ".verification"
    for path in (settings.upload_dir, settings.frame_dir, settings.result_dir,
                 settings.report_dir, settings.reference_dir, settings.model_cache_dir,
                 file_handler.UPLOAD_DIR, reference_service.REFERENCE_DIR,
                 report_service.REPORT_DIR, frame_extractor.FRAME_DIR):
        assert path.resolve().is_relative_to(root.resolve())


def test_additive_initialization_preserves_existing_data(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "legacy.db").as_posix())
    Order.__table__.create(engine)
    with engine.begin() as db:
        db.exec_driver_sql("INSERT INTO orders (order_id, tracking_id, barcode_value, platform, customer_name, customer_email, delivery_address, order_status, created_at) VALUES ('preserve', 'tracking', 'code', 'other', 'test', 'test@example.com', 'test address', 'delivered', CURRENT_TIMESTAMP)")
    init_db(engine)
    init_db(engine)
    assert {"quantity_verification_records", "condition_analysis_records"} <= set(inspect(engine).get_table_names())
    with engine.connect() as db:
        assert db.exec_driver_sql("SELECT order_id FROM orders").scalar() == "preserve"
    engine.dispose()
