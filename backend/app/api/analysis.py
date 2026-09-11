from typing import Optional
from anyio import to_thread
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.services.analysis_service import analyze_media
from app.services.file_handler import save_upload

router = APIRouter(prefix="/api", tags=["Analysis"])


@router.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    expected_platform: Optional[str] = Form(None),
    expected_order_id: Optional[str] = Form(None),
    expected_customer_name: Optional[str] = Form(None),
    expected_product_name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Upload a package image or video for barcode detection, OCR, and automatic order verification.

    CPU-heavy processing is safely offloaded from the async event loop to a worker thread.
    """
    saved_path, media_type = await save_upload(file)
    try:
        # Offload CPU-bound computer vision and OCR analysis to a thread pool
        result = await to_thread.run_sync(
            analyze_media,
            saved_path,
            media_type,
            file.filename or saved_path.name,
            expected_platform,
            expected_order_id,
            expected_customer_name,
            expected_product_name,
            db,
        )
        return result
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {error}",
        ) from error
