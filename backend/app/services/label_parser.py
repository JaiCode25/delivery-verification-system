from collections import defaultdict
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import unicodedata

from app.schemas.enums import Platform
from app.schemas.label import (
    BoundingBox,
    OcrDetection,
    ParsedField,
    ParsedShippingLabel,
)

# ---------------------------------------------------------------------------
# Normalization Functions (Step 3)
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Normalize general text by applying Unicode NFKC, stripping, and collapsing whitespace."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_comparison_string(text: str) -> str:
    """Normalize text for case-insensitive, punctuation-tolerant comparison."""
    norm = normalize_text(text).lower()
    # Remove standard punctuation
    norm = re.sub(r"[^\w\s]", "", norm)
    return re.sub(r"\s+", " ", norm).strip()


def normalize_tracking_id(tracking_id: str) -> str:
    """Remove spaces, hyphens, and convert to uppercase for canonical tracking ID comparison."""
    if not tracking_id:
        return ""
    norm = normalize_text(tracking_id).upper()
    return re.sub(r"[\s\-_/:\.#]", "", norm)


def normalize_name(name: str) -> str:
    """Clean recipient name by stripping introductory address/name keywords."""
    if not name:
        return ""
    norm = normalize_text(name)
    # Strip common leading label markers
    norm = re.sub(
        r"^(?:name|recipient|customer|ship\s*to|deliver\s*to|buyer|consignee|to)\s*[:\-\s]\s*",
        "",
        norm,
        flags=re.IGNORECASE,
    )
    # Strip honorifics
    norm = re.sub(r"^(?:mr\.|mrs\.|ms\.|dr\.|prof\.)\s+", "", norm, flags=re.IGNORECASE)
    return norm.strip(",. ")


def mask_phone_number(phone_raw: str) -> str:
    """Mask phone number preserving only the final 4 digits for privacy compliance."""
    digits = re.sub(r"\D", "", phone_raw)
    if len(digits) >= 10:
        last4 = digits[-4:]
        masked_prefix = "X" * (len(digits) - 4)
        return f"{masked_prefix}{last4}"
    elif len(digits) >= 4:
        return "X" * (len(digits) - 4) + digits[-4:]
    return "XXXXXXXXXX"


def fix_ocr_digits(text: str) -> str:
    """Selective substitution of common OCR character confusions for numeric fields."""
    table = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "B": "8", "S": "5", "s": "5"})
    return text.translate(table)


# ---------------------------------------------------------------------------
# Pattern Definitions & Extraction Heuristics (Step 2 & 4)
# ---------------------------------------------------------------------------

# Tracking ID / AWB regex patterns
TRACKING_LABEL_PREFIX_RE = re.compile(
    r"(?:tracking\s*(?:id|no|number|#)?|awb\s*(?:no|number|#)?|shipment\s*(?:id|no|#)?|consignment\s*(?:no|#)?)\s*[:\-\s]\s*([A-Za-z0-9\-_]{6,35})",
    re.IGNORECASE,
)
KNOWN_TRACKING_PATTERNS = [
    # Flipkart / Ekart
    (re.compile(r"\b(FMPC[0-9]{8,14})\b", re.IGNORECASE), 0.95),
    (re.compile(r"\b(FMP[A-Z0-9]{8,15})\b", re.IGNORECASE), 0.90),
    # Amazon ATS
    (re.compile(r"\b(TBA[0-9]{10,14})\b", re.IGNORECASE), 0.95),
    (re.compile(r"\b(AMZN[A-Za-z0-9\-]{8,20})\b", re.IGNORECASE), 0.90),
    # Blinkit
    (re.compile(r"\b(BLK-[A-Za-z0-9\-]{4,15})\b", re.IGNORECASE), 0.95),
    # Delhivery / Bluedart / standard 12-14 digit numeric tracking
    (re.compile(r"\b(\d{12,15})\b"), 0.80),
]

# Order ID regex patterns
ORDER_LABEL_PREFIX_RE = re.compile(
    r"(?:order\s*(?:id|no|number|#)?|od\s*(?:no|#)?|invoice\s*(?:no|#)?)\s*[:\-\s]\s*([A-Za-z0-9\-_]{5,35})",
    re.IGNORECASE,
)
KNOWN_ORDER_PATTERNS = [
    # Flipkart Order format (e.g. OD123456789012345600 or DEMO-OD-...)
    (re.compile(r"\b(OD[0-9]{15,20})\b", re.IGNORECASE), 0.95),
    (re.compile(r"\b(DEMO-OD-[A-Za-z0-9\-]+)\b", re.IGNORECASE), 0.95),
    # Amazon Order format (e.g. 402-9981245-11002 or DEMO-AMZ-...)
    (re.compile(r"\b([0-9]{3}-[0-9]{7}-[0-9]{7})\b"), 0.95),
    (re.compile(r"\b(DEMO-AMZ-[A-Za-z0-9\-]+)\b", re.IGNORECASE), 0.95),
    # Blinkit Order format
    (re.compile(r"\b(DEMO-BLK-[A-Za-z0-9\-]+)\b", re.IGNORECASE), 0.95),
    (re.compile(r"\b(BLK-[0-9]{6,12})\b", re.IGNORECASE), 0.90),
]

# Platform keywords
PLATFORM_KEYWORDS = [
    (Platform.FLIPKART, re.compile(r"\b(?:flipkart|flpkart|fkrt|ekart)\b", re.IGNORECASE)),
    (Platform.AMAZON, re.compile(r"\b(?:amazon|amzn|ats)\b", re.IGNORECASE)),
    (Platform.BLINKIT, re.compile(r"\b(?:blinkit|grofers)\b", re.IGNORECASE)),
]

# Courier keywords
COURIER_KEYWORDS = [
    ("Ekart Logistics", re.compile(r"\b(?:ekart(?:\s+logistics|\s+oaustics)?)\b", re.IGNORECASE)),
    ("Amazon Shipping", re.compile(r"\b(?:amazon\s+shipping|amazon\s+transportation|ats)\b", re.IGNORECASE)),
    ("Delhivery", re.compile(r"\bdelhivery\b", re.IGNORECASE)),
    ("Blue Dart", re.compile(r"\b(?:blue\s*dart|bluedart)\b", re.IGNORECASE)),
    ("Shadowfax", re.compile(r"\bshadowfax\b", re.IGNORECASE)),
    ("Xpressbees", re.compile(r"\bxpressbees\b", re.IGNORECASE)),
    ("Ecom Express", re.compile(r"\becom\s*express\b", re.IGNORECASE)),
    ("DTDC", re.compile(r"\bdtdc\b", re.IGNORECASE)),
]

# PIN Code regex (Indian 6-digit postal code starting with 1-9)
PIN_CODE_RE = re.compile(r"\b([1-9][0-9]{2}\s?[0-9]{3})\b")

# Phone number regex
PHONE_RE = re.compile(r"\b(?:\+?91[\-\s]?)?([6-9]\d{9})\b")

# Candidate Field Container
class _FieldCandidate:
    def __init__(
        self,
        value: str,
        normalized_value: str,
        confidence: float,
        supporting_text: str,
        source_frame: Optional[str] = None,
        bounding_box: Optional[BoundingBox] = None,
        video_timestamp_seconds: Optional[float] = None,
        sharpness: float = 0.0,
    ):
        self.value = value
        self.normalized_value = normalized_value
        self.confidence = confidence
        self.supporting_text = [supporting_text]
        self.source_frame = source_frame
        self.bounding_box = bounding_box
        self.video_timestamp_seconds = video_timestamp_seconds
        self.sharpness = sharpness
        self.frames: Set[str] = {source_frame} if source_frame else set()


# ---------------------------------------------------------------------------
# Single-Frame Extraction Logic
# ---------------------------------------------------------------------------


def _extract_frame_candidates(
    detections: List[Dict[str, Any]],
    frame_name: Optional[str] = None,
    timestamp: Optional[float] = None,
    sharpness: float = 0.0,
) -> Dict[str, List[_FieldCandidate]]:
    """Extract candidate structured fields from a single frame's OCR detections."""
    candidates: Dict[str, List[_FieldCandidate]] = defaultdict(list)
    num_items = len(detections)

    for i, det in enumerate(detections):
        raw_text = det.get("text", "").strip()
        if not raw_text:
            continue

        ocr_conf = float(det.get("confidence", 0.5))
        bbox_dict = det.get("bounding_box")
        bbox = BoundingBox(**bbox_dict) if isinstance(bbox_dict, dict) and "top_left" in bbox_dict else None
        clean_line = normalize_text(raw_text)

        # 1. Tracking ID / AWB
        # Check explicit prefix in line
        prefix_match = TRACKING_LABEL_PREFIX_RE.search(clean_line)
        if prefix_match:
            val = prefix_match.group(1).strip()
            norm = normalize_tracking_id(val)
            if len(norm) >= 6:
                candidates["tracking_id"].append(
                    _FieldCandidate(
                        val, norm, min(1.0, ocr_conf * 1.0), raw_text, frame_name, bbox, timestamp, sharpness
                    )
                )

        # Check known courier tracking patterns
        for pattern, strength in KNOWN_TRACKING_PATTERNS:
            match = pattern.search(clean_line)
            if match:
                val = match.group(1).strip()
                norm = normalize_tracking_id(val)
                conf = min(1.0, ocr_conf * strength)
                candidates["tracking_id"].append(
                    _FieldCandidate(val, norm, conf, raw_text, frame_name, bbox, timestamp, sharpness)
                )

        # Multi-line proximity: if current line is just "Tracking ID:" or "AWB:", check next line
        if re.match(r"^(?:tracking\s*(?:id|no)?|awb\s*(?:no)?|shipment\s*(?:id)?)\s*[:\-\s]*$", clean_line, re.I):
            if i + 1 < num_items:
                next_text = detections[i + 1].get("text", "").strip()
                next_conf = float(detections[i + 1].get("confidence", 0.5))
                norm_next = normalize_tracking_id(next_text)
                if len(norm_next) >= 6:
                    candidates["tracking_id"].append(
                        _FieldCandidate(
                            next_text,
                            norm_next,
                            min(1.0, next_conf * 0.90),
                            f"{raw_text} -> {next_text}",
                            frame_name,
                            bbox,
                            timestamp,
                            sharpness,
                        )
                    )

        # 2. Order ID
        order_prefix_match = ORDER_LABEL_PREFIX_RE.search(clean_line)
        if order_prefix_match:
            val = order_prefix_match.group(1).strip()
            norm = normalize_tracking_id(val)
            if len(norm) >= 5:
                candidates["order_id"].append(
                    _FieldCandidate(
                        val, norm, min(1.0, ocr_conf * 1.0), raw_text, frame_name, bbox, timestamp, sharpness
                    )
                )

        for pattern, strength in KNOWN_ORDER_PATTERNS:
            match = pattern.search(clean_line)
            if match:
                val = match.group(1).strip()
                norm = normalize_tracking_id(val)
                conf = min(1.0, ocr_conf * strength)
                candidates["order_id"].append(
                    _FieldCandidate(val, norm, conf, raw_text, frame_name, bbox, timestamp, sharpness)
                )

        # 3. Platform Detection
        for plat_enum, pattern in PLATFORM_KEYWORDS:
            if pattern.search(clean_line):
                val = plat_enum.value
                conf = min(1.0, ocr_conf * 0.95)
                candidates["platform"].append(
                    _FieldCandidate(val, val, conf, raw_text, frame_name, bbox, timestamp, sharpness)
                )

        # 4. Courier Name
        for courier_name, pattern in COURIER_KEYWORDS:
            if pattern.search(clean_line):
                conf = min(1.0, ocr_conf * 0.90)
                candidates["courier_name"].append(
                    _FieldCandidate(
                        courier_name,
                        courier_name.lower(),
                        conf,
                        raw_text,
                        frame_name,
                        bbox,
                        timestamp,
                        sharpness,
                    )
                )

        # 5. Customer / Recipient Name
        name_match = re.search(
            r"(?:name|customer|recipient|ship\s*to|deliver\s*to)\s*[:\-\s]\s*([A-Za-z\s\.]{3,50})",
            clean_line,
            re.IGNORECASE,
        )
        if name_match:
            raw_name = name_match.group(1).strip()
            clean_name = normalize_name(raw_name)
            if len(clean_name) >= 3 and not any(
                kw in clean_name.lower() for kw in ["address", "hostel", "road", "pune", "mumbai", "delhi", "phone"]
            ):
                norm = normalize_comparison_string(clean_name)
                candidates["customer_name"].append(
                    _FieldCandidate(
                        clean_name, norm, min(1.0, ocr_conf * 0.90), raw_text, frame_name, bbox, timestamp, sharpness
                    )
                )

        # Contextual Name: if line is "Shipping/Customer address:" and next line has "Name: ..."
        if re.search(r"(?:shipping/customer\s*address|customer\s*address|ship\s*to)", clean_line, re.I):
            if i + 1 < num_items:
                next_text = detections[i + 1].get("text", "").strip()
                clean_name = normalize_name(next_text)
                if len(clean_name) >= 3:
                    norm = normalize_comparison_string(clean_name)
                    next_conf = float(detections[i + 1].get("confidence", 0.5))
                    candidates["customer_name"].append(
                        _FieldCandidate(
                            clean_name,
                            norm,
                            min(1.0, next_conf * 0.88),
                            f"{raw_text} -> {next_text}",
                            frame_name,
                            bbox,
                            timestamp,
                            sharpness,
                        )
                    )

        # 6. PIN Code
        pin_match = PIN_CODE_RE.search(clean_line)
        if pin_match:
            pin_digits = re.sub(r"\s+", "", pin_match.group(1))
            # Validate 6 digits
            if len(pin_digits) == 6 and not pin_digits.startswith("0"):
                candidates["pin_code"].append(
                    _FieldCandidate(
                        pin_digits,
                        pin_digits,
                        min(1.0, ocr_conf * 0.90),
                        raw_text,
                        frame_name,
                        bbox,
                        timestamp,
                        sharpness,
                    )
                )

        # 7. Phone Number (Masked)
        phone_match = PHONE_RE.search(clean_line)
        if phone_match:
            raw_phone = phone_match.group(1)
            masked = mask_phone_number(raw_phone)
            candidates["phone_number"].append(
                _FieldCandidate(
                    masked, masked, min(1.0, ocr_conf * 0.90), "[MASKED_PHONE]", frame_name, bbox, timestamp, sharpness
                )
            )

        # 8. Seller Name
        seller_match = re.search(
            r"(?:sold\s*by(?:\s*the)?|seller\s*name|seller|vendor)\s*[:\-\s]?\s*([A-Za-z0-9\s,\.]{3,60})",
            clean_line,
            re.IGNORECASE,
        )
        if seller_match:
            raw_seller = seller_match.group(1).strip()
            # If line is just "Sold By The", check next line
            if len(raw_seller) < 3 and i + 1 < num_items:
                raw_seller = detections[i + 1].get("text", "").strip()
            if len(raw_seller) >= 3:
                norm = normalize_comparison_string(raw_seller)
                candidates["seller_name"].append(
                    _FieldCandidate(
                        raw_seller, norm, min(1.0, ocr_conf * 0.85), raw_text, frame_name, bbox, timestamp, sharpness
                    )
                )

        # 9. Product Description
        desc_match = re.search(
            r"(?:description|item\s*description|product\s*name|title|sku\s*id)\s*[:\-\s]?\s*([A-Za-z0-9\s,\.\-]{4,100})",
            clean_line,
            re.IGNORECASE,
        )
        if desc_match:
            raw_desc = desc_match.group(1).strip()
            if len(raw_desc) >= 4:
                candidates["product_description"].append(
                    _FieldCandidate(
                        raw_desc,
                        normalize_comparison_string(raw_desc),
                        min(1.0, ocr_conf * 0.85),
                        raw_text,
                        frame_name,
                        bbox,
                        timestamp,
                        sharpness,
                    )
                )
        elif any(
            prod_token in clean_line.lower()
            for prod_token in ["watch", "analog", "headphones", "coffee", "mobile", "laptop", "t-shirt", "shoes"]
        ):
            # Known domain product tokens appearing on label
            candidates["product_description"].append(
                _FieldCandidate(
                    clean_line,
                    normalize_comparison_string(clean_line),
                    min(1.0, ocr_conf * 0.75),
                    raw_text,
                    frame_name,
                    bbox,
                    timestamp,
                    sharpness,
                )
            )

        # 10. Quantity
        qty_match = re.search(r"(?:qty|quantity|units?|count)\s*[:\-\s]?\s*([0-9]{1,4})\b", clean_line, re.IGNORECASE)
        if qty_match:
            qty_val = fix_ocr_digits(qty_match.group(1))
            candidates["quantity"].append(
                _FieldCandidate(
                    qty_val, qty_val, min(1.0, ocr_conf * 0.85), raw_text, frame_name, bbox, timestamp, sharpness
                )
            )
        elif re.match(r"^qty\s*$", clean_line, re.I) and i + 1 < num_items:
            next_num = detections[i + 1].get("text", "").strip()
            if next_num.isdigit() and int(next_num) < 1000:
                candidates["quantity"].append(
                    _FieldCandidate(
                        next_num,
                        next_num,
                        min(1.0, ocr_conf * 0.80),
                        f"{raw_text} -> {next_num}",
                        frame_name,
                        bbox,
                        timestamp,
                        sharpness,
                    )
                )

    return candidates


# ---------------------------------------------------------------------------
# Multi-Frame Consensus & Aggregation (Step 5)
# ---------------------------------------------------------------------------


def _aggregate_field_candidates(candidates: List[_FieldCandidate]) -> Optional[ParsedField]:
    """Aggregate candidate observations for a single field across frames using consensus."""
    if not candidates:
        return None

    # Group by normalized value
    groups: Dict[str, List[_FieldCandidate]] = defaultdict(list)
    for c in candidates:
        groups[c.normalized_value].append(c)

    best_group_key = None
    best_score = -1.0
    best_candidate = None

    for norm_val, group in groups.items():
        distinct_frames = {c.source_frame for c in group if c.source_frame}
        frame_count = max(1, len(distinct_frames))

        # Highest sharpness candidate in this group
        sharpest = max(group, key=lambda c: (c.sharpness, c.confidence))
        max_conf = max(c.confidence for c in group)

        # Consensus score: confidence (50%) + multi-frame confirmation bonus (30%) + sharpness (20%)
        frame_bonus = min(0.30, (frame_count - 1) * 0.10)
        sharpness_bonus = min(0.20, sharpest.sharpness / 500.0) if sharpest.sharpness > 0 else 0.0
        score = (max_conf * 0.50) + frame_bonus + sharpness_bonus

        if score > best_score:
            best_score = score
            best_group_key = norm_val
            best_candidate = sharpest

    if not best_candidate:
        return None

    winning_group = groups[best_group_key]
    distinct_frames = {c.source_frame for c in winning_group if c.source_frame}
    frame_count = max(1, len(distinct_frames))

    # Boost confidence based on multi-frame agreement
    final_confidence = min(1.0, round(best_candidate.confidence + min(0.15, (frame_count - 1) * 0.05), 3))

    all_evidence: List[str] = []
    for c in winning_group:
        all_evidence.extend(c.supporting_text)

    return ParsedField(
        value=best_candidate.value,
        normalized_value=best_candidate.normalized_value,
        confidence=final_confidence,
        supporting_text=list(dict.fromkeys(all_evidence)),  # deduplicate preserving order
        source_frame=best_candidate.source_frame,
        bounding_box=best_candidate.bounding_box,
        video_timestamp_seconds=best_candidate.video_timestamp_seconds,
        frame_count=frame_count,
    )


def parse_shipping_label(
    detections: List[Dict[str, Any]],
    frames_metadata: Optional[List[Dict[str, Any]]] = None,
) -> ParsedShippingLabel:
    """Parse raw OCR detections into structured shipping label information.

    Supports single-image detections and multi-frame video detections with consensus.
    """
    raw_evidence: List[str] = []
    all_candidates: Dict[str, List[_FieldCandidate]] = defaultdict(list)

    # If frames_metadata is provided, group detections by frame
    if frames_metadata:
        frames_dict = {f["frame_name"]: f for f in frames_metadata if "frame_name" in f}
    else:
        frames_dict = {}

    # Check if detections are tagged with source_frame
    detections_by_frame: Dict[Optional[str], List[Dict[str, Any]]] = defaultdict(list)
    for det in detections:
        src = det.get("source_frame") or det.get("first_seen_in")
        detections_by_frame[src].append(det)
        text = det.get("text", "").strip()
        if text:
            # Mask phone numbers in raw evidence for privacy protection
            masked_text = PHONE_RE.sub(lambda m: mask_phone_number(m.group(0)), text)
            raw_evidence.append(masked_text)

    for frame_name, frame_dets in detections_by_frame.items():
        frame_info = frames_dict.get(frame_name, {})
        timestamp = frame_info.get("timestamp_seconds")
        sharpness = float(frame_info.get("sharpness", 0.0))

        frame_candidates = _extract_frame_candidates(
            detections=frame_dets,
            frame_name=frame_name,
            timestamp=timestamp,
            sharpness=sharpness,
        )
        for field, cand_list in frame_candidates.items():
            all_candidates[field].extend(cand_list)

    # Build consensus per field
    parsed_fields: Dict[str, Any] = {}
    for field in [
        "tracking_id",
        "order_id",
        "platform",
        "courier_name",
        "customer_name",
        "pin_code",
        "phone_number",
        "seller_name",
        "product_description",
        "quantity",
    ]:
        parsed_fields[field] = _aggregate_field_candidates(all_candidates.get(field, []))

    return ParsedShippingLabel(
        tracking_id=parsed_fields["tracking_id"],
        order_id=parsed_fields["order_id"],
        platform=parsed_fields["platform"],
        courier_name=parsed_fields["courier_name"],
        customer_name=parsed_fields["customer_name"],
        pin_code=parsed_fields["pin_code"],
        phone_number=parsed_fields["phone_number"],
        seller_name=parsed_fields["seller_name"],
        product_description=parsed_fields["product_description"],
        quantity=parsed_fields["quantity"],
        raw_evidence=list(dict.fromkeys(raw_evidence)),
    )
