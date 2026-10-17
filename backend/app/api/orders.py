from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.order import (
    OrderCreate,
    OrderListResponse,
    OrderRead,
    ProductReferenceListResponse,
    ProductReferenceRead,
)
from app.services import order_service, reference_service


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


@router.post("/{order_id}/references", response_model=ProductReferenceListResponse, status_code=status.HTTP_201_CREATED)
async def upload_order_references(
    order_id: str,
    files: list[UploadFile] = File(...),
    product_name: Optional[str] = Form(None),
    brand: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    colour: Optional[str] = Form(None),
    product_url: Optional[str] = Form(None),
    expected_quantity: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    """Upload 1-5 product reference images for an order item."""
    created = await reference_service.add_order_references(
        db=db,
        order_id=order_id,
        files=files,
        product_name=product_name,
        brand=brand,
        model=model,
        colour=colour,
        product_url=product_url,
        expected_quantity=expected_quantity,
    )
    all_refs = reference_service.list_order_references(db, order_id)
    return ProductReferenceListResponse(
        order_id=order_id,
        total_references=len(all_refs),
        references=[ProductReferenceRead.model_validate(r) for r in all_refs],
    )


@router.get("/{order_id}/references", response_model=ProductReferenceListResponse)
def get_order_references(
    order_id: str,
    db: Session = Depends(get_db),
):
    """List all stored reference images for an order."""
    refs = reference_service.list_order_references(db, order_id)
    return ProductReferenceListResponse(
        order_id=order_id,
        total_references=len(refs),
        references=[ProductReferenceRead.model_validate(r) for r in refs],
    )


@router.delete("/{order_id}/references/{reference_id}", status_code=status.HTTP_200_OK)
def delete_reference(
    order_id: str,
    reference_id: str,
    db: Session = Depends(get_db),
):
    """Delete a specific reference image for an order."""
    reference_service.delete_order_reference(db, order_id, reference_id)
    return {"message": f"Reference image '{reference_id}' deleted successfully for order '{order_id}'."}
