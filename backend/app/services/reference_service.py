import os
from pathlib import Path
from typing import List, Optional, Tuple
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.config import (
    ALLOWED_REFERENCE_EXTENSIONS,
    MAX_REFERENCE_FILE_SIZE,
    MAX_REFERENCE_IMAGES_PER_ITEM,
    REFERENCE_DIR,
    STORAGE_DIR,
)
from app.database.models import Order, OrderItem, ProductReference

CHUNK_SIZE = 1024 * 1024  # 1 MB chunk


def validate_reference_signature(header: bytes) -> str:
    """Validate initial header magic bytes against expected image signatures (JPEG, PNG, WEBP)."""
    if not header or len(header) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Corrupt or unreadable image file header.",
        )

    is_jpeg = header.startswith(b"\xff\xd8\xff")
    is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
    is_webp = header.startswith(b"RIFF") and b"WEBP" in header[:16]

    if is_jpeg:
        return "image/jpeg"
    elif is_png:
        return "image/png"
    elif is_webp:
        return "image/webp"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image format. Only JPEG, PNG, and WEBP reference images are accepted.",
        )


def _safe_resolve_order_dir(order_identifier: str) -> Path:
    """Safely resolve reference storage directory for an order, preventing path traversal."""
    # Sanitize order identifier to alphanumeric, hyphen, underscore
    sanitized = "".join(c for c in str(order_identifier) if c.isalnum() or c in ("-", "_")).strip()
    if not sanitized or ".." in str(order_identifier):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid order identifier for reference storage.",
        )

    target_dir = (REFERENCE_DIR / sanitized).resolve()
    base_ref_dir = REFERENCE_DIR.resolve()

    # Ensure target_dir is strictly inside REFERENCE_DIR
    try:
        target_dir.relative_to(base_ref_dir)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path traversal attempt detected in order reference storage path.",
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


async def save_single_reference_file(
    file: UploadFile,
    order_identifier: str,
) -> Tuple[str, str, int, str]:
    """Save an uploaded reference file to disk and return (relative_path, original_filename, file_size, mime_type)."""
    raw_filename = file.filename or "reference.jpg"
    # Basic filename sanitize
    clean_name = Path(raw_filename).name
    extension = Path(clean_name).suffix.lower()

    if extension not in ALLOWED_REFERENCE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported reference image extension '{extension}'. Allowed extensions: {ALLOWED_REFERENCE_EXTENSIONS}",
        )

    order_dir = _safe_resolve_order_dir(order_identifier)
    unique_filename = f"{uuid4().hex}{extension}"
    destination = order_dir / unique_filename

    total_bytes = 0
    header_bytes = bytearray()

    try:
        with open(destination, "wb") as out_file:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_REFERENCE_FILE_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Reference image exceeds maximum allowed limit of {MAX_REFERENCE_FILE_SIZE // (1024 * 1024)} MB.",
                    )
                if len(header_bytes) < 32:
                    needed = 32 - len(header_bytes)
                    header_bytes.extend(chunk[:needed])
                out_file.write(chunk)

        if total_bytes == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded reference image is empty.",
            )

        mime_type = validate_reference_signature(bytes(header_bytes))

    except Exception:
        if destination.exists():
            try:
                destination.unlink()
            except Exception:
                pass
        raise

    # Store relative path (e.g. references/<order_id>/<uuid>.jpg) using forward slashes
    relative_path = f"references/{order_dir.name}/{unique_filename}"
    return relative_path, clean_name, total_bytes, mime_type


async def add_order_references(
    db: Session,
    order_id: str,
    files: List[UploadFile],
    product_name: Optional[str] = None,
    brand: Optional[str] = None,
    model: Optional[str] = None,
    colour: Optional[str] = None,
    product_url: Optional[str] = None,
    expected_quantity: Optional[int] = None,
) -> List[ProductReference]:
    """Add one or more reference images to an existing order, updating or creating the order item if needed."""
    order = db.query(Order).filter(Order.order_id == order_id).first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found.",
        )

    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one reference image file must be uploaded.",
        )

    # Locate primary order item or create/update it
    if order.items:
        item = order.items[0]
        if product_name:
            item.product_name = product_name
        if brand:
            item.brand = brand
        if model:
            item.model = model
        if colour:
            item.colour = colour
        if product_url:
            item.product_url = product_url
        if expected_quantity and expected_quantity > 0:
            item.expected_quantity = expected_quantity
    else:
        item = OrderItem(
            order_id=order.id,
            product_name=product_name or "Delivered Item",
            brand=brand or "Generic",
            model=model or "Standard",
            colour=colour or "Default",
            expected_quantity=expected_quantity or 1,
            product_url=product_url,
        )
        db.add(item)
        db.flush()

    # Check total references count limit
    current_count = db.query(ProductReference).filter(ProductReference.order_item_id == item.id).count()
    if current_count + len(files) > MAX_REFERENCE_IMAGES_PER_ITEM:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot add {len(files)} reference image(s). Order item already has {current_count} references (max allowed: {MAX_REFERENCE_IMAGES_PER_ITEM}).",
        )

    created_references: List[ProductReference] = []
    saved_paths_for_cleanup: List[Path] = []

    try:
        for file in files:
            rel_path, orig_name, size_bytes, mime_type = await save_single_reference_file(
                file=file,
                order_identifier=order.order_id,
            )
            saved_paths_for_cleanup.append(STORAGE_DIR / rel_path)

            ref_record = ProductReference(
                order_id=order.id,
                order_item_id=item.id,
                relative_path=rel_path,
                original_filename=orig_name,
                file_size_bytes=size_bytes,
                mime_type=mime_type,
            )
            db.add(ref_record)
            created_references.append(ref_record)

        # Update order item's primary reference image path if unset
        if not item.reference_image_path and created_references:
            item.reference_image_path = created_references[0].relative_path

        db.commit()
        for ref in created_references:
            db.refresh(ref)

        return created_references

    except Exception:
        db.rollback()
        # Clean up files written to disk if db transaction fails
        for p in saved_paths_for_cleanup:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
        raise


def list_order_references(db: Session, order_id: str) -> List[ProductReference]:
    """Retrieve all reference images associated with an order."""
    order = db.query(Order).filter(Order.order_id == order_id).first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found.",
        )
    return db.query(ProductReference).filter(ProductReference.order_id == order.id).all()


def delete_order_reference(db: Session, order_id: str, reference_id: str) -> bool:
    """Delete a specific product reference from DB and remove its physical file."""
    order = db.query(Order).filter(Order.order_id == order_id).first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found.",
        )

    ref = (
        db.query(ProductReference)
        .filter(ProductReference.order_id == order.id, ProductReference.id == reference_id)
        .first()
    )
    if not ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reference image '{reference_id}' not found for order '{order_id}'.",
        )

    # Resolve physical path and ensure it's inside STORAGE_DIR / REFERENCE_DIR
    phys_path = (STORAGE_DIR / ref.relative_path).resolve()
    base_ref_dir = REFERENCE_DIR.resolve()

    if phys_path.exists() and base_ref_dir in phys_path.parents:
        try:
            phys_path.unlink()
        except Exception:
            pass

    # Update item primary reference if needed
    item = db.query(OrderItem).filter(OrderItem.id == ref.order_item_id).first()
    if item and item.reference_image_path == ref.relative_path:
        # Pick another reference or set to None
        remaining = (
            db.query(ProductReference)
            .filter(ProductReference.order_item_id == item.id, ProductReference.id != ref.id)
            .first()
        )
        item.reference_image_path = remaining.relative_path if remaining else None

    db.delete(ref)
    db.commit()
    return True


def get_reference_absolute_paths(db: Session, order_id: str) -> List[Tuple[str, Path]]:
    """Return list of tuples (reference_id, absolute_path) for an order."""
    refs = list_order_references(db, order_id)
    results: List[Tuple[str, Path]] = []
    for r in refs:
        abs_p = (STORAGE_DIR / r.relative_path).resolve()
        if abs_p.exists():
            results.append((r.id, abs_p))
    return results
