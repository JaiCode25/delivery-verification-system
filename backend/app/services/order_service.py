from datetime import datetime, timezone
from typing import List, Optional, Tuple
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import Order, OrderItem, VerificationSession
from app.schemas.enums import Platform, VerificationStatus
from app.schemas.order import OrderCreate, OrderSummary


def create_order(db: Session, order_in: OrderCreate) -> Order:
    """Create a new order with items, validating unique constraints."""
    existing = db.query(Order).filter(
        (Order.order_id == order_in.order_id)
        | (Order.tracking_id == order_in.tracking_id)
        | (Order.barcode_value == order_in.barcode_value)
    ).first()

    if existing:
        if existing.order_id == order_in.order_id:
            raise ValueError(f"Order with order_id '{order_in.order_id}' already exists")
        if existing.tracking_id == order_in.tracking_id:
            raise ValueError(f"Order with tracking_id '{order_in.tracking_id}' already exists")
        if existing.barcode_value == order_in.barcode_value:
            raise ValueError(f"Order with barcode_value '{order_in.barcode_value}' already exists")

    order = Order(
        order_id=order_in.order_id,
        tracking_id=order_in.tracking_id,
        barcode_value=order_in.barcode_value,
        platform=order_in.platform.value if isinstance(order_in.platform, Platform) else order_in.platform,
        customer_name=order_in.customer_name,
        customer_email=order_in.customer_email,
        delivery_address=order_in.delivery_address,
        order_status=order_in.order_status.value if hasattr(order_in.order_status, "value") else order_in.order_status,
        delivered_at=order_in.delivered_at,
    )
    for item_in in order_in.items:
        item = OrderItem(
            product_name=item_in.product_name,
            brand=item_in.brand,
            model=item_in.model,
            colour=item_in.colour,
            expected_quantity=item_in.expected_quantity,
            product_url=item_in.product_url,
            reference_image_path=item_in.reference_image_path,
        )
        order.items.append(item)

    db.add(order)
    try:
        db.commit()
        db.refresh(order)
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("Database integrity constraint violated while creating order") from exc

    return order


def get_order_by_id(db: Session, id: int) -> Optional[Order]:
    return db.query(Order).filter(Order.id == id).first()


def get_order_by_order_id(db: Session, order_id: str) -> Optional[Order]:
    return db.query(Order).filter(Order.order_id == order_id).first()


def find_order_by_tracking_id(db: Session, tracking_id: str) -> Optional[Order]:
    return db.query(Order).filter(Order.tracking_id == tracking_id).first()


def find_order_by_barcode(db: Session, barcode_value: str) -> Optional[Order]:
    """Find order matching barcode_value, tracking_id, or order_id."""
    clean_val = barcode_value.strip()
    return db.query(Order).filter(
        (Order.barcode_value == clean_val)
        | (Order.tracking_id == clean_val)
        | (Order.order_id == clean_val)
    ).first()


def list_orders(db: Session, limit: int = 50, offset: int = 0) -> Tuple[List[Order], int]:
    safe_limit = max(1, min(100, limit))
    safe_offset = max(0, offset)
    total = db.query(Order).count()
    orders = db.query(Order).order_by(Order.id.desc()).offset(safe_offset).limit(safe_limit).all()
    return orders, total


def has_finalized_claim(db: Session, order_id: int) -> bool:
    """Check whether a previous claim for this order was explicitly finalized."""
    existing_claim = db.query(VerificationSession).filter(
        VerificationSession.order_id == order_id,
        VerificationSession.is_claim_finalized == True,
    ).first()
    return existing_claim is not None


def store_verification_session(
    db: Session,
    verification_status: VerificationStatus,
    order_id: Optional[int] = None,
    decoded_code: Optional[str] = None,
    barcode_type: Optional[str] = None,
    mismatch_reasons: Optional[List[str]] = None,
    evidence_frame: Optional[str] = None,
    video_timestamp_seconds: Optional[float] = None,
    is_claim_finalized: bool = False,
) -> VerificationSession:
    """Persist a verification session in the SQLite database."""
    session_id = uuid4().hex
    status_val = verification_status.value if hasattr(verification_status, "value") else str(verification_status)
    session = VerificationSession(
        id=session_id,
        order_id=order_id,
        decoded_code=decoded_code,
        barcode_type=barcode_type,
        verification_status=status_val,
        mismatch_reasons=mismatch_reasons or [],
        evidence_frame=evidence_frame,
        video_timestamp_seconds=video_timestamp_seconds,
        is_claim_finalized=is_claim_finalized,
        created_at=datetime.now(timezone.utc),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def finalize_claim(db: Session, session_id: str) -> Optional[VerificationSession]:
    """Finalize a verification session as a submitted complaint / claim."""
    session = db.query(VerificationSession).filter(VerificationSession.id == session_id).first()
    if session:
        session.is_claim_finalized = True
        session.claim_finalized_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(session)
    return session


def compare_order_details(
    order: Order,
    expected_platform: Optional[str] = None,
    expected_order_id: Optional[str] = None,
    expected_customer_name: Optional[str] = None,
    expected_product_name: Optional[str] = None,
) -> List[str]:
    """Compare user-supplied claims against the persisted order record."""
    mismatches: List[str] = []

    if expected_platform:
        exp_plat = expected_platform.value if hasattr(expected_platform, "value") else str(expected_platform)
        if exp_plat.lower() != order.platform.lower():
            mismatches.append(f"Platform mismatch: claimed '{exp_plat}', but order is for '{order.platform}'")

    if expected_order_id and expected_order_id.strip():
        if expected_order_id.strip().lower() != order.order_id.strip().lower():
            mismatches.append(f"Order ID mismatch: claimed '{expected_order_id}', order is '{order.order_id}'")

    if expected_customer_name and expected_customer_name.strip():
        # Case-insensitive substring/inclusion check
        claimed_norm = expected_customer_name.strip().lower()
        actual_norm = order.customer_name.strip().lower()
        if claimed_norm not in actual_norm and actual_norm not in claimed_norm:
            mismatches.append(
                f"Customer name mismatch: claimed '{expected_customer_name}', order belongs to '{order.customer_name}'"
            )

    if expected_product_name and expected_product_name.strip():
        exp_prod = expected_product_name.strip().lower()
        matched_item = False
        for item in order.items:
            prod_name = item.product_name.lower()
            brand = item.brand.lower()
            model = item.model.lower()
            if exp_prod in prod_name or exp_prod in f"{brand} {model}" or prod_name in exp_prod:
                matched_item = True
                break
        if not matched_item:
            mismatches.append(f"Product mismatch: expected product '{expected_product_name}' not found in order items")

    return mismatches


def build_order_summary(order: Order) -> OrderSummary:
    """Build a sanitized, safe summary of an order for verification output."""
    items_summary = [f"{item.product_name} ({item.brand} {item.model}) x{item.expected_quantity}" for item in order.items]
    return OrderSummary(
        order_id=order.order_id,
        tracking_id=order.tracking_id,
        barcode_value=order.barcode_value,
        platform=Platform(order.platform) if order.platform in [p.value for p in Platform] else Platform.OTHER,
        customer_name=order.customer_name,
        order_status=order.order_status,
        delivered_at=order.delivered_at,
        item_count=len(order.items),
        items_summary=items_summary,
    )
