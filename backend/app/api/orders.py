from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.order import OrderCreate, OrderListResponse, OrderRead
from app.services import order_service

router = APIRouter(prefix="/api/orders", tags=["Orders"])


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def create_new_order(order_in: OrderCreate, db: Session = Depends(get_db)):
    """Register a new customer order in the persistent SQLite database."""
    try:
        return order_service.create_order(db=db, order_in=order_in)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("", response_model=OrderListResponse)
def list_orders(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Retrieve paginated list of registered orders."""
    orders, total = order_service.list_orders(db=db, limit=limit, offset=offset)
    return OrderListResponse(total=total, limit=limit, offset=offset, orders=orders)


@router.get("/{order_id}", response_model=OrderRead)
def get_order_by_order_id(order_id: str, db: Session = Depends(get_db)):
    """Look up order by public order ID."""
    order = order_service.get_order_by_order_id(db=db, order_id=order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found.",
        )
    return order


@router.get("/tracking/{tracking_id}", response_model=OrderRead)
def get_order_by_tracking(tracking_id: str, db: Session = Depends(get_db)):
    """Look up order by tracking identifier."""
    order = order_service.find_order_by_tracking_id(db=db, tracking_id=tracking_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with tracking ID '{tracking_id}' not found.",
        )
    return order


@router.get("/barcode/{barcode_value}", response_model=OrderRead)
def get_order_by_barcode(barcode_value: str, db: Session = Depends(get_db)):
    """Look up order by decoded barcode value."""
    order = order_service.find_order_by_barcode(db=db, barcode_value=barcode_value)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with barcode value '{barcode_value}' not found.",
        )
    return order
