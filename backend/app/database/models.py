from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database.connection import Base


def utc_now():
    return datetime.now(timezone.utc)


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    order_id = Column(String(100), unique=True, nullable=False, index=True)
    tracking_id = Column(String(100), unique=True, nullable=False, index=True)
    barcode_value = Column(String(100), unique=True, nullable=False, index=True)
    platform = Column(String(50), nullable=False, index=True)  # amazon, flipkart, blinkit, other
    customer_name = Column(String(200), nullable=False)
    customer_email = Column(String(200), nullable=False)
    delivery_address = Column(Text, nullable=False)
    order_status = Column(String(50), nullable=False, default="delivered")
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        UniqueConstraint("order_id", name="uq_order_order_id"),
        UniqueConstraint("tracking_id", name="uq_order_tracking_id"),
        UniqueConstraint("barcode_value", name="uq_order_barcode_value"),
    )

    items = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="joined",
    )
    references = relationship(
        "ProductReference",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    verification_sessions = relationship(
        "VerificationSession",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_name = Column(String(255), nullable=False)
    brand = Column(String(100), nullable=False)
    model = Column(String(100), nullable=False)
    colour = Column(String(50), nullable=False)
    expected_quantity = Column(Integer, nullable=False, default=1)
    product_url = Column(Text, nullable=True)
    reference_image_path = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    order = relationship("Order", back_populates="items")
    references = relationship(
        "ProductReference",
        back_populates="order_item",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ProductReference(Base):
    __tablename__ = "product_references"

    id = Column(String(36), primary_key=True, default=lambda: uuid4().hex, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    order_item_id = Column(Integer, ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False, index=True)
    relative_path = Column(String(500), nullable=False)
    original_filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size_bytes = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    order = relationship("Order", back_populates="references")
    order_item = relationship("OrderItem", back_populates="references")


class VerificationSession(Base):
    __tablename__ = "verification_sessions"

    id = Column(String(36), primary_key=True, default=lambda: uuid4().hex, index=True)
    analysis_id = Column(String(36), nullable=True, index=True)
    input_type = Column(String(20), nullable=True)
    original_filename = Column(String(255), nullable=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    decoded_code = Column(String(200), nullable=True, index=True)
    barcode_type = Column(String(50), nullable=True)
    verification_status = Column(String(50), nullable=False, index=True)
    label_analysis_status = Column(String(50), nullable=True)
    label_summary = Column(JSON, nullable=True)
    label_analysis_data = Column(JSON, nullable=True)
    mismatch_reasons = Column(JSON, nullable=False, default=list)
    evidence_frame = Column(String(255), nullable=True)
    video_timestamp_seconds = Column(Float, nullable=True)
    is_claim_finalized = Column(Boolean, nullable=False, default=False, index=True)
    claim_finalized_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    order = relationship("Order", back_populates="verification_sessions")


class ProductMatchingRecord(Base):
    __tablename__ = "product_matching_records"

    id = Column(String(36), primary_key=True, default=lambda: uuid4().hex, index=True)
    verification_session_id = Column(String(36), ForeignKey("verification_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    decision = Column(String(50), nullable=False, index=True)
    overall_score = Column(Float, nullable=False)
    component_scores = Column(JSON, nullable=False, default=dict)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class VerificationReport(Base):
    __tablename__ = "verification_reports"

    id = Column(String(36), primary_key=True, default=lambda: uuid4().hex, index=True)
    verification_session_id = Column(String(36), ForeignKey("verification_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    final_decision = Column(String(50), nullable=False, index=True)
    report_data = Column(JSON, nullable=False, default=dict)
    json_path = Column(String(500), nullable=True)
    html_path = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class EvidenceAnalysisColumns:
    """Additive tables keep legacy sessions and serialized reports unchanged."""

    id = Column(String(36), primary_key=True, default=lambda: uuid4().hex)
    verification_session_id = Column(String(36), ForeignKey("verification_sessions.id"), nullable=False, unique=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    status = Column(String(50), nullable=False)
    details = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class QuantityVerificationRecord(EvidenceAnalysisColumns, Base):
    __tablename__ = "quantity_verification_records"


class ConditionAnalysisRecord(EvidenceAnalysisColumns, Base):
    __tablename__ = "condition_analysis_records"
