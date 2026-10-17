from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.config import settings

# For SQLite, check_same_thread=False allows FastAPI to use sessions across async request threads
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(target_engine=None) -> None:
    """Create all database tables if they do not exist and ensure schema compatibility."""
    active_engine = target_engine or engine
    Base.metadata.create_all(bind=active_engine)

    # Safe SQLite column migration for existing database files
    try:
        with active_engine.connect() as conn:
            # Check order_items table for created_at
            res = conn.exec_driver_sql("PRAGMA table_info(order_items)").fetchall()
            existing_cols = {row[1] for row in res}
            if existing_cols and "created_at" not in existing_cols:
                conn.exec_driver_sql("ALTER TABLE order_items ADD COLUMN created_at DATETIME")
                conn.commit()

            # Check verification_sessions table for newly added columns
            res_vs = conn.exec_driver_sql("PRAGMA table_info(verification_sessions)").fetchall()
            existing_vs_cols = {row[1] for row in res_vs}
            if existing_vs_cols:
                vs_migrations = [
                    ("analysis_id", "ALTER TABLE verification_sessions ADD COLUMN analysis_id VARCHAR(36)"),
                    ("input_type", "ALTER TABLE verification_sessions ADD COLUMN input_type VARCHAR(20)"),
                    ("original_filename", "ALTER TABLE verification_sessions ADD COLUMN original_filename VARCHAR(255)"),
                    ("label_analysis_status", "ALTER TABLE verification_sessions ADD COLUMN label_analysis_status VARCHAR(50)"),
                    ("label_summary", "ALTER TABLE verification_sessions ADD COLUMN label_summary JSON"),
                    ("label_analysis_data", "ALTER TABLE verification_sessions ADD COLUMN label_analysis_data JSON"),
                ]
                for col_name, sql in vs_migrations:
                    if col_name not in existing_vs_cols:
                        conn.exec_driver_sql(sql)
                conn.commit()
    except Exception:
        pass
