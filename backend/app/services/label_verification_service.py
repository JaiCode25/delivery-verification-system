import re
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.database.models import Order
from app.schemas.enums import (
    FieldComparisonStatus,
    LabelVerificationStatus,
    VerificationStatus,
)
from app.schemas.label import (
    FieldComparison,
    LabelAnalysisResult,
    ParsedShippingLabel,
)
from app.schemas.verification import OrderVerificationResult
from app.services.label_parser import (
    PIN_CODE_RE,
    build_label_summary,
    normalize_comparison_string,
    normalize_name,
    normalize_tracking_id,
)
from app.services.order_service import find_order_by_barcode, find_order_by_tracking_id, get_order_by_order_id


def compare_label_with_order(
    parsed_label: ParsedShippingLabel,
    order: Order,
    expected_product_name: Optional[str] = None,
) -> Tuple[List[FieldComparison], LabelVerificationStatus, List[str]]:
    """Compare extracted label fields with an authoritative Order record.

    Produces field-level comparisons and determines the label verification outcome.
    Never exposes complete delivery address or customer email.
    """
    comparisons: List[FieldComparison] = []
    warnings: List[str] = []

    # 1. Tracking ID Comparison
    if parsed_label.tracking_id:
        lbl_trk_norm = parsed_label.tracking_id.normalized_value
        ord_trk_norm = normalize_tracking_id(order.tracking_id)
        ord_bar_norm = normalize_tracking_id(order.barcode_value)

        if lbl_trk_norm in (ord_trk_norm, ord_bar_norm):
            comparisons.append(
                FieldComparison(
                    field_name="tracking_id",
                    status=FieldComparisonStatus.MATCH,
                    label_value=parsed_label.tracking_id.value,
                    order_value=order.tracking_id,
                    confidence=parsed_label.tracking_id.confidence,
                    details="Printed tracking ID matches order tracking record.",
                )
            )
        else:
            comparisons.append(
                FieldComparison(
                    field_name="tracking_id",
                    status=FieldComparisonStatus.MISMATCH,
                    label_value=parsed_label.tracking_id.value,
                    order_value=order.tracking_id,
                    confidence=parsed_label.tracking_id.confidence,
                    details=f"Printed tracking ID '{parsed_label.tracking_id.value}' contradicts order tracking ID '{order.tracking_id}'.",
                )
            )
    else:
        comparisons.append(
            FieldComparison(
                field_name="tracking_id",
                status=FieldComparisonStatus.NOT_DETECTED,
                details="Tracking ID was not detected on the shipping label.",
            )
        )

    # 2. Order ID Comparison
    if parsed_label.order_id:
        lbl_ord_norm = parsed_label.order_id.normalized_value
        ord_id_norm = normalize_tracking_id(order.order_id)

        if lbl_ord_norm == ord_id_norm:
            comparisons.append(
                FieldComparison(
                    field_name="order_id",
                    status=FieldComparisonStatus.MATCH,
                    label_value=parsed_label.order_id.value,
                    order_value=order.order_id,
                    confidence=parsed_label.order_id.confidence,
                    details="Printed order ID matches order record.",
                )
            )
        else:
            comparisons.append(
                FieldComparison(
                    field_name="order_id",
                    status=FieldComparisonStatus.MISMATCH,
                    label_value=parsed_label.order_id.value,
                    order_value=order.order_id,
                    confidence=parsed_label.order_id.confidence,
                    details=f"Printed order ID '{parsed_label.order_id.value}' contradicts database order ID '{order.order_id}'.",
                )
            )
    else:
        comparisons.append(
            FieldComparison(
                field_name="order_id",
                status=FieldComparisonStatus.NOT_DETECTED,
                details="Order ID was not detected on the shipping label.",
            )
        )

    # 3. Platform Comparison
    if parsed_label.platform:
        lbl_plat = parsed_label.platform.normalized_value.lower()
        ord_plat = order.platform.lower()

        if lbl_plat == ord_plat:
            comparisons.append(
                FieldComparison(
                    field_name="platform",
                    status=FieldComparisonStatus.MATCH,
                    label_value=parsed_label.platform.value,
                    order_value=order.platform,
                    confidence=parsed_label.platform.confidence,
                    details=f"Platform matches: {order.platform.capitalize()}",
                )
            )
        else:
            comparisons.append(
                FieldComparison(
                    field_name="platform",
                    status=FieldComparisonStatus.MISMATCH,
                    label_value=parsed_label.platform.value,
                    order_value=order.platform,
                    confidence=parsed_label.platform.confidence,
                    details=f"Platform mismatch: label displays '{parsed_label.platform.value}', order is registered under '{order.platform}'.",
                )
            )
    else:
        comparisons.append(
            FieldComparison(
                field_name="platform",
                status=FieldComparisonStatus.NOT_DETECTED,
                details="Platform name was not detected on the shipping label.",
            )
        )

    # 4. Customer / Recipient Name Comparison
    if parsed_label.customer_name:
        lbl_name_clean = normalize_name(parsed_label.customer_name.value)
        lbl_norm = normalize_comparison_string(lbl_name_clean)
        ord_norm = normalize_comparison_string(order.customer_name)

        lbl_tokens = set(lbl_norm.split())
        ord_tokens = set(ord_norm.split())
        token_overlap = lbl_tokens & ord_tokens

        # Check token overlap or substring containment
        if token_overlap or lbl_norm in ord_norm or ord_norm in lbl_norm:
            comparisons.append(
                FieldComparison(
                    field_name="customer_name",
                    status=FieldComparisonStatus.MATCH,
                    label_value=lbl_name_clean,
                    order_value=order.customer_name,
                    confidence=parsed_label.customer_name.confidence,
                    details=f"Recipient name matches: {order.customer_name}",
                )
            )
        else:
            comparisons.append(
                FieldComparison(
                    field_name="customer_name",
                    status=FieldComparisonStatus.MISMATCH,
                    label_value=lbl_name_clean,
                    order_value=order.customer_name,
                    confidence=parsed_label.customer_name.confidence,
                    details=f"Recipient name mismatch: label is addressed to '{lbl_name_clean}', expected '{order.customer_name}'.",
                )
            )
    else:
        comparisons.append(
            FieldComparison(
                field_name="customer_name",
                status=FieldComparisonStatus.NOT_DETECTED,
                details="Recipient name was not detected on the shipping label.",
            )
        )

    # 5. PIN Code Comparison
    if parsed_label.pin_code:
        lbl_pin = parsed_label.pin_code.normalized_value
        # Extract PIN from order delivery address
        pin_match = PIN_CODE_RE.search(order.delivery_address)
        ord_pin = re.sub(r"\s+", "", pin_match.group(1)) if pin_match else None

        if ord_pin:
            if lbl_pin == ord_pin:
                comparisons.append(
                    FieldComparison(
                        field_name="pin_code",
                        status=FieldComparisonStatus.MATCH,
                        label_value=lbl_pin,
                        order_value=ord_pin,
                        confidence=parsed_label.pin_code.confidence,
                        details=f"Delivery PIN code matches: {ord_pin}",
                    )
                )
            else:
                comparisons.append(
                    FieldComparison(
                        field_name="pin_code",
                        status=FieldComparisonStatus.MISMATCH,
                        label_value=lbl_pin,
                        order_value=ord_pin,
                        confidence=parsed_label.pin_code.confidence,
                        details=f"Delivery PIN code mismatch: label has '{lbl_pin}', order destination is '{ord_pin}'.",
                    )
                )
        else:
            comparisons.append(
                FieldComparison(
                    field_name="pin_code",
                    status=FieldComparisonStatus.UNCERTAIN,
                    label_value=lbl_pin,
                    order_value=None,
                    confidence=parsed_label.pin_code.confidence,
                    details="Order delivery address does not have a standard 6-digit PIN code for comparison.",
                )
            )
    else:
        comparisons.append(
            FieldComparison(
                field_name="pin_code",
                status=FieldComparisonStatus.NOT_DETECTED,
                details="PIN code was not detected on the shipping label.",
            )
        )

    # 6. Product Description Comparison (Optional - absence never fails verification)
    if parsed_label.product_description:
        lbl_desc_norm = parsed_label.product_description.normalized_value
        lbl_tokens = set(lbl_desc_norm.split())

        matched_item = None
        for item in order.items:
            item_tokens = set(normalize_comparison_string(f"{item.product_name} {item.brand} {item.model}").split())
            if len(lbl_tokens & item_tokens) >= 1:
                matched_item = item
                break

        if matched_item:
            comparisons.append(
                FieldComparison(
                    field_name="product_description",
                    status=FieldComparisonStatus.MATCH,
                    label_value=parsed_label.product_description.value,
                    order_value=matched_item.product_name,
                    confidence=parsed_label.product_description.confidence,
                    details=f"Printed item description corresponds to order item '{matched_item.product_name}'.",
                )
            )
        else:
            comparisons.append(
                FieldComparison(
                    field_name="product_description",
                    status=FieldComparisonStatus.UNCERTAIN,
                    label_value=parsed_label.product_description.value,
                    order_value=None,
                    confidence=parsed_label.product_description.confidence,
                    details="Printed product text could not be positively matched with expected order items.",
                )
            )
    else:
        comparisons.append(
            FieldComparison(
                field_name="product_description",
                status=FieldComparisonStatus.NOT_DETECTED,
                details="Product description was not detected on label (normal for privacy-shielded couriers).",
            )
        )

    # Overall Status Evaluation
    statuses = {c.field_name: c.status for c in comparisons}

    # Severe mismatch in primary keys
    if (
        statuses.get("tracking_id") == FieldComparisonStatus.MISMATCH
        or statuses.get("order_id") == FieldComparisonStatus.MISMATCH
        or statuses.get("platform") == FieldComparisonStatus.MISMATCH
    ):
        overall_status = LabelVerificationStatus.MISMATCH
        warnings.append("Critical contradiction found between shipping label text and order record.")

    elif (
        statuses.get("tracking_id") == FieldComparisonStatus.MATCH
        or statuses.get("order_id") == FieldComparisonStatus.MATCH
    ):
        # Primary identifier matched! Check customer name
        if statuses.get("customer_name") == FieldComparisonStatus.MISMATCH:
            overall_status = LabelVerificationStatus.MANUAL_REVIEW_REQUIRED
            warnings.append("Order identifier matches, but recipient name contradicts order registration.")
        else:
            overall_status = LabelVerificationStatus.VERIFIED

    elif (
        statuses.get("platform") == FieldComparisonStatus.MATCH
        or statuses.get("customer_name") == FieldComparisonStatus.MATCH
        or statuses.get("pin_code") == FieldComparisonStatus.MATCH
    ):
        # Only secondary attributes matched without primary tracking/order ID
        overall_status = LabelVerificationStatus.PARTIALLY_VERIFIED
        warnings.append("Label attributes partially match, but primary tracking/order ID was not readable.")

    else:
        # Nothing readable
        overall_status = LabelVerificationStatus.INSUFFICIENT_EVIDENCE
        warnings.append("Shipping label does not contain sufficient legible text for verification.")

    return comparisons, overall_status, warnings


def cross_check_barcode_and_label(
    db: Session,
    parsed_label: ParsedShippingLabel,
    barcode_result: Optional[OrderVerificationResult] = None,
    matched_order: Optional[Order] = None,
    expected_platform: Optional[str] = None,
    expected_order_id: Optional[str] = None,
    expected_customer_name: Optional[str] = None,
    expected_product_name: Optional[str] = None,
) -> LabelAnalysisResult:
    """Combine barcode verification and shipping-label OCR as two independent evidence sources.

    Implements cross-check scenarios:
    1. Barcode & label both match same order -> Strong verification
    2. Barcode matches, but label is unreadable -> Barcode verified, label insufficient
    3. Barcode matches order, but printed label ID contradicts it -> mismatch / manual_review_required
    4. Barcode not found, but readable label ID matches order -> manual_review_required
    5. Neither identifies an order -> insufficient_evidence / order_not_found
    """
    supporting_frames: List[str] = []
    # Collect frames contributing to label fields
    for field in [
        parsed_label.tracking_id,
        parsed_label.order_id,
        parsed_label.platform,
        parsed_label.courier_name,
        parsed_label.customer_name,
        parsed_label.pin_code,
        parsed_label.product_description,
    ]:
        if field and field.source_frame:
            supporting_frames.append(field.source_frame)
    supporting_frames = list(dict.fromkeys(supporting_frames))

    # Initialize warnings and build concise 8-field summary
    warnings: List[str] = []
    summary = build_label_summary(parsed_label)

    # Cross-check barcode tracking vs label tracking contradiction
    if (
        barcode_result
        and barcode_result.decoded_value
        and parsed_label.tracking_id
        and parsed_label.tracking_id.value
    ):
        dec_norm = normalize_tracking_id(barcode_result.decoded_value)
        lbl_norm = parsed_label.tracking_id.normalized_value
        if dec_norm and lbl_norm and dec_norm != lbl_norm:
            warnings.append(
                f"Contradiction alert: Barcode decoded as '{barcode_result.decoded_value}', but label text displays tracking number '{parsed_label.tracking_id.value}'."
            )

    # Check for low OCR confidence (< 0.60) on any detected field
    for f_name, f_cand in [
        ("platform", parsed_label.platform),
        ("courier", parsed_label.courier_name),
        ("order_id", parsed_label.order_id),
        ("tracking_number", parsed_label.tracking_id),
        ("pin_code", parsed_label.pin_code),
        ("product", parsed_label.product_description),
        ("colour_model", parsed_label.colour_model),
        ("quantity", parsed_label.quantity),
    ]:
        if f_cand and f_cand.confidence < 0.60:
            warnings.append(f"Low OCR confidence ({f_cand.confidence:.2f}) for detected field '{f_name}'.")

    # Scenario A: Barcode matched an order in the database
    if matched_order:
        comparisons, label_status, cmp_warnings = compare_label_with_order(
            parsed_label=parsed_label,
            order=matched_order,
            expected_product_name=expected_product_name,
        )
        warnings.extend(cmp_warnings)

        # Cross-check Scenario 3: Barcode matched an order, but label ID contradicts it
        if label_status == LabelVerificationStatus.MISMATCH:
            warnings.append(
                "Cross-check alert: The physical shipping label text contradicts the barcode-matched order."
            )

        return LabelAnalysisResult(
            status=label_status,
            summary=summary,
            parsed_fields=parsed_label,
            field_comparisons=comparisons,
            supporting_frames=supporting_frames,
            warnings=list(dict.fromkeys(warnings)),
        )

    # Scenario B: Barcode did NOT match an order.
    # Check if a label identifier (tracking ID or order ID) can find an order in SQLite
    found_order = None
    if parsed_label.tracking_id:
        found_order = find_order_by_tracking_id(db, parsed_label.tracking_id.normalized_value)
    if not found_order and parsed_label.order_id:
        found_order = get_order_by_order_id(db, parsed_label.order_id.normalized_value)
    if not found_order and parsed_label.tracking_id:
        found_order = find_order_by_barcode(db, parsed_label.tracking_id.normalized_value)

    if found_order:
        # Cross-check Scenario 4: Barcode is not found, but a readable label identifier matches an order
        comparisons, _, cmp_warnings = compare_label_with_order(
            parsed_label=parsed_label,
            order=found_order,
            expected_product_name=expected_product_name,
        )
        warnings.extend(cmp_warnings)
        warnings.append(
            "Cross-check notice: Barcode was not detected or matched, but printed label ID corresponds to order. Manual review required."
        )
        return LabelAnalysisResult(
            status=LabelVerificationStatus.MANUAL_REVIEW_REQUIRED,
            summary=summary,
            parsed_fields=parsed_label,
            field_comparisons=comparisons,
            supporting_frames=supporting_frames,
            warnings=list(dict.fromkeys(warnings)),
        )

    # Scenario C: Neither barcode nor label identifies an order in the database
    # Build default comparisons against expected values if provided
    comparisons = []
    warnings.append("No registered order was found matching either the barcode or the shipping label text.")

    if parsed_label.tracking_id:
        comparisons.append(
            FieldComparison(
                field_name="tracking_id",
                status=FieldComparisonStatus.NOT_DETECTED,
                label_value=parsed_label.tracking_id.value,
                details="Tracking ID detected on label, but no corresponding order exists in the database.",
            )
        )

    return LabelAnalysisResult(
        status=LabelVerificationStatus.INSUFFICIENT_EVIDENCE,
        summary=summary,
        parsed_fields=parsed_label,
        field_comparisons=comparisons,
        supporting_frames=supporting_frames,
        warnings=list(dict.fromkeys(warnings)),
    )
