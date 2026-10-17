from anyio import to_thread
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import Order, VerificationSession
from app.schemas.product_matching import ProductMatchingResult
from app.services.file_handler import save_upload
from app.services.product_matching_service import evaluate_candidate_matching
from app.services.reference_service import get_reference_absolute_paths

router = APIRouter(prefix="/api/product-matching", tags=["Product Matching"])


@router.post("/analyze", response_model=ProductMatchingResult)
async def analyze_product_media(
    order_id: str = Form(..., description="Customer Order ID against which to verify product"),
    verification_session_id: str = Form(..., description="Active verification session ID to link this matching result"),
    file: UploadFile = File(..., description="Delivered product photograph or unpacking video"),
    db: Session = Depends(get_db),
):
    """Compare uploaded delivered product image or video against stored reference images for an order."""
    order = db.query(Order).filter(Order.order_id == order_id).first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found.",
        )

    # Validate verification session exists and belongs to the supplied order
    session = db.query(VerificationSession).filter(VerificationSession.id == verification_session_id).first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification session '{verification_session_id}' not found.",
        )

    if session.order_id is not None and session.order_id != order.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Verification session '{verification_session_id}' does not belong to order '{order_id}'.",
        )

    # If session order_id is unset, associate it with this order
    if session.order_id is None:
        session.order_id = order.id
        db.commit()

    references = get_reference_absolute_paths(db, order_id)
    if not references:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No reference images uploaded for order '{order_id}'. Upload references via POST /api/orders/{order_id}/references before running product matching.",
        )

    # Save evidence file using safe file handler
    saved_path, media_type = await save_upload(file)

    try:
        result = await to_thread.run_sync(
            evaluate_candidate_matching,
            db,
            order_id,
            saved_path,
            media_type,
            verification_session_id,
        )
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Product matching analysis failed: {exc}",
        ) from exc

