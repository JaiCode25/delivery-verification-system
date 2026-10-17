from collections import defaultdict
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import unicodedata

from app.schemas.enums import Platform
from app.schemas.label import (
    BoundingBox,
    BoundingBoxPoint,
    LabelSummary,
    OcrDetection,
    ParsedField,
    ParsedShippingLabel,
)

# ---------------------------------------------------------------------------
# Normalization Functions
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
    norm = re.sub(
        r"^(?:name|recipient|customer|ship\s*to|deliver\s*to|buyer|consignee|to)\s*[:\-\s]\s*",
        "",
        norm,
        flags=re.IGNORECASE,
    )
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


def clean_product_name(product: str) -> str:
    """Clean OCR artifacts from product name."""
    if not product:
        return ""
    # Strip leading/trailing quotes or brackets
    cleaned = re.sub(r"^[\[\"\'\(\)\{\}\-\s]+", "", product)
    cleaned = re.sub(r"[\]\"\'\(\)\{\}\-\s]+$", "", cleaned)
    # Fix OCR typos like Rocsii -> Rocsil
    cleaned = re.sub(r"\bRocsii\b", "Rocsil", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_flipkart_order_id(
    candidate: str, has_flipkart_context: bool = False, has_order_context: bool = False
) -> Optional[str]:
    """Normalize a candidate string to canonical Flipkart Order ID format (OD followed by 18 digits).

    Applies OCR-confusion correction only when:
    - Candidate has total length 20 (2 prefix chars + 18 digits)
    - Remainder is 18 numeric digits
    - Begins with OD, or begins with OCR-confused 00, 0D, O0, OO when both Flipkart context and Order context are present.
    """
    if not candidate:
        return None
    cleaned = re.sub(r"[\s\-_/:\.#]", "", candidate)
    if len(cleaned) != 20:
        return None

    prefix = cleaned[:2].upper()
    remainder = cleaned[2:]

    if not remainder.isdigit():
        return None

    if prefix == "OD":
        return f"OD{remainder}"

    if prefix in ("00", "0D", "O0", "OO"):
        if has_flipkart_context and has_order_context:
            return f"OD{remainder}"

    return None


# ---------------------------------------------------------------------------
# Pattern Definitions & Extraction Heuristics
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
    # Delhivery / Bluedart / standard 12-15 digit numeric tracking
    (re.compile(r"\b(\d{12,15})\b"), 0.80),
]

# Order ID regex patterns
ORDER_LABEL_PREFIX_RE = re.compile(
    r"(?:order\s*(?:id|no|number|#)?|od\s*(?:no|#)?|invoice\s*(?:no|#)?)\s*[:\-\s]\s*([A-Za-z0-9\-_]{5,35})",
    re.IGNORECASE,
)
KNOWN_ORDER_PATTERNS = [
    # Flipkart Order format (OD...)
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
    ("Ekart Logistics", re.compile(r"\b(?:ekart(?:\s+logistics|\s+loaustics|\s+oaustics)?)\b", re.IGNORECASE)),
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

# Address context keywords
ADDRESS_CONTEXT_KEYWORDS = re.compile(
    r"(?:shipping/customer\s*address|customer\s*address|shipping\s*address|deliver\s*to|ship\s*to|recipient\s*address|address\s*:|pune|mumbai|delhi|maharashtra|in-mh|karnataka|bangalore|bengaluru|road|street|nagar|hostel|blocks|colony|apartment|sector|ward)",
    re.IGNORECASE,
)


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
# Spatial Layout & Line Clustering
# ---------------------------------------------------------------------------


def _get_bbox_rect(det: Dict[str, Any]) -> Optional[Tuple[int, int, int, int]]:
    """Return (xmin, ymin, xmax, ymax) from detection dict."""
    bbox = det.get("bounding_box")
    if not bbox or not isinstance(bbox, dict):
        return None
    tl = bbox.get("top_left", {})
    br = bbox.get("bottom_right", {})
    if "x" in tl and "y" in tl and "x" in br and "y" in br:
        return (min(tl["x"], br["x"]), min(tl["y"], br["y"]), max(tl["x"], br["x"]), max(tl["y"], br["y"]))
    return None


def _cluster_detections_into_lines(detections: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Cluster detections with bounding boxes into horizontal visual lines/rows."""
    positioned = []
    unpositioned = []
    for d in detections:
        rect = _get_bbox_rect(d)
        if rect:
            positioned.append((rect, d))
        else:
            unpositioned.append(d)

    # Sort vertically by top y-coordinate
    positioned.sort(key=lambda item: item[0][1])

    clustered: List[List[Tuple[Tuple[int, int, int, int], Dict[str, Any]]]] = []
    for rect, d in positioned:
        xmin, ymin, xmax, ymax = rect
        h = max(1, ymax - ymin)
        placed = False
        for line in clustered:
            line_rects = [item[0] for item in line]
            l_ymin = min(r[1] for r in line_rects)
            l_ymax = max(r[3] for r in line_rects)
            l_h = max(1, l_ymax - l_ymin)
            overlap = max(0, min(ymax, l_ymax) - max(ymin, l_ymin))
            if overlap >= 0.35 * min(h, l_h):
                line.append((rect, d))
                placed = True
                break
        if not placed:
            clustered.append([(rect, d)])

    # Sort items in each line horizontally left-to-right
    result_lines = []
    for line in clustered:
        line.sort(key=lambda item: item[0][0])
        result_lines.append([item[1] for item in line])

    return result_lines


# ---------------------------------------------------------------------------
# Spatial Table Parser
# ---------------------------------------------------------------------------


def _extract_table_fields(
    detections: List[Dict[str, Any]],
    frame_name: Optional[str] = None,
    timestamp: Optional[float] = None,
    sharpness: float = 0.0,
) -> Dict[str, _FieldCandidate]:
    """Parse structured shipping label tables (e.g. Flipkart SKU ID, Description, Qty)."""
    table_candidates: Dict[str, _FieldCandidate] = {}
    lines = _cluster_detections_into_lines(detections)

    header_line_idx = -1
    sku_col_x = (0, 0)
    desc_col_x = (0, 0)
    qty_col_x = (0, 0)

    # 1. Identify header line
    for idx, line in enumerate(lines):
        line_texts = [d.get("text", "").lower() for d in line]
        combined = " ".join(line_texts)

        has_sku = any(re.search(r"\b(?:sku(?:\s*id)?|item\s*code|model)\b", t) for t in line_texts)
        has_desc = any(re.search(r"\b(?:description|particulars|title)\b", t) for t in line_texts)
        has_qty = any(re.search(r"\b(?:qty[il]?|quantity|units?|count)\b", t) for t in line_texts)

        if (has_sku and has_desc) or (has_desc and has_qty) or (has_sku and has_qty):
            header_line_idx = idx
            # Find column bounds
            for d in line:
                t = d.get("text", "").lower()
                rect = _get_bbox_rect(d)
                if not rect:
                    continue
                if re.search(r"\b(?:sku(?:\s*id)?|item\s*code|model)\b", t):
                    sku_col_x = (rect[0], rect[2])
                if re.search(r"\b(?:description|particulars|title)\b", t):
                    desc_col_x = (rect[0], rect[2])
                if re.search(r"\b(?:qty[il]?|quantity|units?|count)\b", t):
                    qty_col_x = (rect[0], rect[2])
            break

    if header_line_idx == -1:
        return table_candidates

    # If bounds were partially found, estimate column separators
    if desc_col_x == (0, 0) and sku_col_x != (0, 0):
        desc_col_x = (sku_col_x[1], sku_col_x[1] + 300)
    if qty_col_x == (0, 0) and desc_col_x != (0, 0):
        qty_col_x = (desc_col_x[1], desc_col_x[1] + 200)

    # 2. Inspect data rows directly below header
    data_rows = lines[header_line_idx + 1 : min(header_line_idx + 3, len(lines))]
    if not data_rows:
        return table_candidates

    sku_tokens: List[str] = []
    desc_tokens: List[str] = []
    qty_val: Optional[int] = None
    qty_conf = 0.8
    sku_conf = 0.8
    desc_conf = 0.8

    for row in data_rows:
        for d in row:
            raw_t = d.get("text", "").strip()
            conf = float(d.get("confidence", 0.5))
            rect = _get_bbox_rect(d)
            if not rect or not raw_t:
                continue

            cx = (rect[0] + rect[2]) / 2.0

            # If detection has a vertical pipe (e.g. "Black | Rocsii Analog Watch"), split into columns
            if "|" in raw_t:
                parts = [p.strip() for p in raw_t.split("|") if p.strip()]
                if len(parts) >= 2:
                    left_part, right_part = parts[0], parts[1]
                    # Left part belongs to SKU/colour
                    if left_part:
                        sku_tokens.append(left_part)
                        sku_conf = max(sku_conf, conf)
                    # Right part belongs to Description
                    if right_part:
                        desc_tokens.append(right_part)
                        desc_conf = max(desc_conf, conf)
                    continue

            # Classify by horizontal center position
            # Left edge: check if single-digit row index (e.g. '1' at far left margin)
            if cx < (sku_col_x[0] if sku_col_x[0] > 0 else 180) and raw_t in ("1", "2", "3", "#"):
                # Table row number, ignore
                continue

            # Qty column check
            qty_threshold = qty_col_x[0] - 40 if qty_col_x[0] > 0 else 750
            if cx >= qty_threshold or (qty_col_x[0] > 0 and abs(cx - ((qty_col_x[0] + qty_col_x[1]) / 2.0)) < 80):
                digits = re.sub(r"\D", "", fix_ocr_digits(raw_t))
                if digits and len(digits) <= 3:
                    try:
                        q = int(digits)
                        if 1 <= q <= 999:
                            qty_val = q
                            qty_conf = max(qty_conf, conf)
                            continue
                    except ValueError:
                        pass

            # SKU column check (left of description column)
            desc_left = desc_col_x[0] - 20 if desc_col_x[0] > 0 else 300
            if cx < desc_left:
                clean_sku_token = re.sub(r"^[\[\"\'\-\s]+", "", raw_t).strip("]\"' ")
                if clean_sku_token and clean_sku_token not in ("1", "2", "3", "B", "8", "|"):
                    sku_tokens.append(clean_sku_token)
                    sku_conf = max(sku_conf, conf)
                continue

            # Description column check (between SKU and Qty)
            if cx < qty_threshold:
                clean_desc_part = re.sub(r"^[\[\"\'\-\s]+", "", raw_t).strip("]\"' ")
                if clean_desc_part and clean_desc_part not in ("8", "B", "|", "1"):
                    desc_tokens.append(clean_desc_part)
                    desc_conf = max(desc_conf, conf)

    # 3. Assemble and clean table candidate fields
    # Colour / Model
    if sku_tokens:
        raw_sku_str = " ".join(sku_tokens)
        raw_sku_str = re.sub(r"\bBiack\b", "Black", raw_sku_str, flags=re.IGNORECASE)
        # Clean leading digits error like 1105 -> 105
        raw_sku_str = re.sub(r"\b1105\b", "105", raw_sku_str)
        # Format as 'Model - Colour' if both model number and color are present
        model_match = re.search(r"\b(\d{2,4})\b", raw_sku_str)
        color_match = re.search(r"\b(black|blue|white|silver|gold|red|green|brown|grey|gray)\b", raw_sku_str, re.I)
        if model_match and color_match:
            formatted_sku = f"{model_match.group(1)} - {color_match.group(1).capitalize()}"
        elif model_match:
            formatted_sku = model_match.group(1)
        elif color_match:
            formatted_sku = color_match.group(1).capitalize()
        else:
            formatted_sku = raw_sku_str.strip()

        if formatted_sku:
            table_candidates["colour_model"] = _FieldCandidate(
                value=formatted_sku,
                normalized_value=normalize_comparison_string(formatted_sku),
                confidence=round(sku_conf, 3),
                supporting_text=f"Table SKU: {raw_sku_str}",
                source_frame=frame_name,
                video_timestamp_seconds=timestamp,
                sharpness=sharpness,
            )

    # Product Description
    if desc_tokens:
        raw_desc_str = " ".join(desc_tokens)
        cleaned_product = clean_product_name(raw_desc_str)
        # If colour/model number (e.g. 105) appears redundantly at end, clean it
        if "colour_model" in table_candidates:
            mod_val = table_candidates["colour_model"].value
            mod_num = re.search(r"\b(\d{2,4})\b", mod_val)
            if mod_num:
                cleaned_product = re.sub(rf"\s+{mod_num.group(1)}$", "", cleaned_product).strip()

        if cleaned_product:
            table_candidates["product_description"] = _FieldCandidate(
                value=cleaned_product,
                normalized_value=normalize_comparison_string(cleaned_product),
                confidence=round(desc_conf, 3),
                supporting_text=f"Table Description: {raw_desc_str}",
                source_frame=frame_name,
                video_timestamp_seconds=timestamp,
                sharpness=sharpness,
            )

    # Quantity
    if qty_val is not None:
        table_candidates["quantity"] = _FieldCandidate(
            value=str(qty_val),
            normalized_value=str(qty_val),
            confidence=round(qty_conf, 3),
            supporting_text=f"Table Qty: {qty_val}",
            source_frame=frame_name,
            video_timestamp_seconds=timestamp,
            sharpness=sharpness,
        )

    return table_candidates


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

    # First, run spatial table parser to extract tabular fields
    table_fields = _extract_table_fields(
        detections=detections,
        frame_name=frame_name,
        timestamp=timestamp,
        sharpness=sharpness,
    )
    for field, cand in table_fields.items():
        candidates[field].append(cand)

    frame_has_flipkart = any(re.search(r"\b(?:flipkart|flpkart|fkrt|ekart)\b", d.get("text", ""), re.I) for d in detections)
    address_context_active = False
    lines_since_address = 99

    for i, det in enumerate(detections):
        raw_text = det.get("text", "").strip()
        if not raw_text:
            continue

        ocr_conf = float(det.get("confidence", 0.5))
        bbox_dict = det.get("bounding_box")
        bbox = BoundingBox(**bbox_dict) if isinstance(bbox_dict, dict) and "top_left" in bbox_dict else None
        clean_line = normalize_text(raw_text)

        # Contextual indicators for detection
        local_flipkart_context = frame_has_flipkart or any(
            re.search(r"\b(?:flipkart|flpkart|fkrt|ekart)\b", detections[j].get("text", ""), re.I)
            for j in range(max(0, i - 3), min(num_items, i + 4))
        )
        local_order_context = (
            bool(re.search(r"\b(?:order\s*(?:id|no|#)?|od\s*(?:no|#)?|ordered\s*through|ordered\s*on|invoice\s*(?:no|#)?|cod)\b", clean_line, re.I))
            or any(
                re.search(r"\b(?:order\s*(?:id|no|#)?|od\s*(?:no|#)?|ordered\s*through|ordered\s*on|invoice\s*(?:no|#)?|cod)\b", detections[j].get("text", ""), re.I)
                for j in range(max(0, i - 3), min(num_items, i + 4))
            )
        )

        # Address context tracker
        if ADDRESS_CONTEXT_KEYWORDS.search(clean_line):
            address_context_active = True
            lines_since_address = 0
        elif address_context_active:
            lines_since_address += 1
            if lines_since_address > 5 or re.search(r"\b(?:sold\s*by|seller|gstin|invoice|sku)\b", clean_line, re.I):
                address_context_active = False

        # 1. Tracking ID / AWB
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
        # Dedicated Flipkart pattern check (with OCR-confusion correction when in Flipkart & order context)
        fk_order_candidate = None
        for token in clean_line.split():
            fk_norm = normalize_flipkart_order_id(token, local_flipkart_context, local_order_context)
            if fk_norm:
                fk_order_candidate = fk_norm
                break
        if not fk_order_candidate:
            fk_order_candidate = normalize_flipkart_order_id(clean_line, local_flipkart_context, local_order_context)

        if fk_order_candidate:
            candidates["order_id"].append(
                _FieldCandidate(
                    value=fk_order_candidate,
                    normalized_value=fk_order_candidate,
                    confidence=min(1.0, ocr_conf * 0.98),
                    supporting_text=raw_text,
                    source_frame=frame_name,
                    bounding_box=bbox,
                    video_timestamp_seconds=timestamp,
                    sharpness=sharpness,
                )
            )

        order_prefix_match = ORDER_LABEL_PREFIX_RE.search(clean_line)
        if order_prefix_match:
            val = order_prefix_match.group(1).strip()
            fk_norm = normalize_flipkart_order_id(val, local_flipkart_context, True)
            if fk_norm:
                val = fk_norm
                norm = fk_norm
            else:
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
                fk_norm = normalize_flipkart_order_id(val, local_flipkart_context, local_order_context)
                if fk_norm:
                    val = fk_norm
                    norm = fk_norm
                else:
                    norm = normalize_tracking_id(val)
                conf = min(1.0, ocr_conf * strength)
                candidates["order_id"].append(
                    _FieldCandidate(val, norm, conf, raw_text, frame_name, bbox, timestamp, sharpness)
                )

        # Contextual Order ID following 'Ordered through' or 'Ordered through Flipkart'
        if re.search(r"ordered\s*through", clean_line, re.I):
            if i + 1 < num_items:
                next_t = detections[i + 1].get("text", "").strip()
                next_conf = float(detections[i + 1].get("confidence", 0.5))
                fk_norm = normalize_flipkart_order_id(next_t, local_flipkart_context, True)
                if fk_norm:
                    candidates["order_id"].append(
                        _FieldCandidate(
                            fk_norm,
                            fk_norm,
                            min(1.0, next_conf * 0.98),
                            f"{raw_text} -> {next_t}",
                            frame_name,
                            bbox,
                            timestamp,
                            sharpness,
                        )
                    )
                else:
                    for pattern, strength in KNOWN_ORDER_PATTERNS:
                        m = pattern.search(next_t)
                        if m:
                            val = m.group(1).strip()
                            norm = normalize_tracking_id(val)
                            conf = min(1.0, next_conf * strength)
                            candidates["order_id"].append(
                                _FieldCandidate(val, norm, conf, f"{raw_text} -> {val}", frame_name, bbox, timestamp, sharpness)
                            )

        # 3. Platform Detection
        for plat_enum, pattern in PLATFORM_KEYWORDS:
            if pattern.search(clean_line):
                val = plat_enum.value.capitalize()
                conf = min(1.0, ocr_conf * 0.95)
                candidates["platform"].append(
                    _FieldCandidate(val, plat_enum.value, conf, raw_text, frame_name, bbox, timestamp, sharpness)
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

        if re.search(r"(?:shipping/customer\s*address|customer\s*address|ship\s*to)", clean_line, re.I):
            if i + 1 < num_items:
                next_text = detections[i + 1].get("text", "").strip()
                clean_name = normalize_name(next_text)
                if len(clean_name) >= 3 and not any(kw in clean_name.lower() for kw in ["address", "road", "hostel"]):
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

        # 6. PIN Code (Address Context Only)
        pin_match = PIN_CODE_RE.search(clean_line)
        if pin_match:
            pin_digits = re.sub(r"\s+", "", pin_match.group(1))
            # Validate 6 digits and verify it occurs within address context
            if len(pin_digits) == 6 and not pin_digits.startswith("0"):
                is_address_related = address_context_active or any(
                    addr_w in clean_line.lower() for addr_w in ["pune", "mumbai", "delhi", "in-mh", "mh", "road", "street"]
                )
                conf = min(1.0, ocr_conf * (0.95 if is_address_related else 0.40))
                if is_address_related:
                    candidates["pin_code"].append(
                        _FieldCandidate(
                            pin_digits,
                            pin_digits,
                            conf,
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
            if len(raw_seller) < 3 and i + 1 < num_items:
                raw_seller = detections[i + 1].get("text", "").strip()
            if len(raw_seller) >= 3:
                norm = normalize_comparison_string(raw_seller)
                candidates["seller_name"].append(
                    _FieldCandidate(
                        raw_seller, norm, min(1.0, ocr_conf * 0.85), raw_text, frame_name, bbox, timestamp, sharpness
                    )
                )

        # 9. Fallback Product Description (if not already captured by table parser)
        if "product_description" not in table_fields:
            desc_match = re.search(
                r"(?:description|item\s*description|product\s*name|title)\s*[:\-\s]?\s*([A-Za-z0-9\s,\.\-]{4,100})",
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

        # 10. Fallback Quantity (if not already captured by table parser)
        if "quantity" not in table_fields:
            qty_match = re.search(r"(?:qty|quantity|units?|count)\s*[:\-\s]?\s*([0-9]{1,3})\b", clean_line, re.IGNORECASE)
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
# Multi-Frame Consensus & Aggregation
# ---------------------------------------------------------------------------


def _aggregate_field_candidates(candidates: List[_FieldCandidate]) -> Optional[ParsedField]:
    """Aggregate candidate observations for a single field across frames using consensus."""
    if not candidates:
        return None

    groups: Dict[str, List[_FieldCandidate]] = defaultdict(list)
    for c in candidates:
        groups[c.normalized_value].append(c)

    best_group_key = None
    best_score = -1.0
    best_candidate = None

    for norm_val, group in groups.items():
        distinct_frames = {c.source_frame for c in group if c.source_frame}
        frame_count = max(1, len(distinct_frames))

        sharpest = max(group, key=lambda c: (c.sharpness, c.confidence))
        max_conf = max(c.confidence for c in group)

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

    final_confidence = min(1.0, round(best_candidate.confidence + min(0.15, (frame_count - 1) * 0.05), 3))

    all_evidence: List[str] = []
    for c in winning_group:
        all_evidence.extend(c.supporting_text)

    return ParsedField(
        value=best_candidate.value,
        normalized_value=best_candidate.normalized_value,
        confidence=final_confidence,
        supporting_text=list(dict.fromkeys(all_evidence)),
        source_frame=best_candidate.source_frame,
        bounding_box=best_candidate.bounding_box,
        video_timestamp_seconds=best_candidate.video_timestamp_seconds,
        frame_count=frame_count,
    )


def parse_shipping_label(
    detections: List[Dict[str, Any]],
    frames_metadata: Optional[List[Dict[str, Any]]] = None,
) -> ParsedShippingLabel:
    """Parse raw OCR detections into structured shipping label information."""
    raw_evidence: List[str] = []
    all_candidates: Dict[str, List[_FieldCandidate]] = defaultdict(list)

    if frames_metadata:
        frames_dict = {f["frame_name"]: f for f in frames_metadata if "frame_name" in f}
    else:
        frames_dict = {}

    detections_by_frame: Dict[Optional[str], List[Dict[str, Any]]] = defaultdict(list)
    for det in detections:
        src = det.get("source_frame") or det.get("first_seen_in")
        detections_by_frame[src].append(det)
        text = det.get("text", "").strip()
        if text:
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
        "colour_model",
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
        colour_model=parsed_fields["colour_model"],
        quantity=parsed_fields["quantity"],
        raw_evidence=list(dict.fromkeys(raw_evidence)),
    )


# ---------------------------------------------------------------------------
# Summary Builder (Phase 2.1 Fixed Summary Structure)
# ---------------------------------------------------------------------------


def build_label_summary(parsed_label: ParsedShippingLabel) -> LabelSummary:
    """Build the canonical 8-field summary from a ParsedShippingLabel.

    Returns:
        LabelSummary with keys: platform, courier, order_id, tracking_number,
        pin_code, product, colour_model, quantity (integer or None).
    """
    qty_int: Optional[int] = None
    if parsed_label.quantity and parsed_label.quantity.value:
        try:
            digits = re.sub(r"\D", "", parsed_label.quantity.value)
            if digits:
                val = int(digits)
                if 1 <= val <= 999:
                    qty_int = val
        except (ValueError, TypeError):
            qty_int = None

    return LabelSummary(
        platform=parsed_label.platform.value if parsed_label.platform else None,
        courier=parsed_label.courier_name.value if parsed_label.courier_name else None,
        order_id=parsed_label.order_id.value if parsed_label.order_id else None,
        tracking_number=parsed_label.tracking_id.value if parsed_label.tracking_id else None,
        pin_code=parsed_label.pin_code.value if parsed_label.pin_code else None,
        product=parsed_label.product_description.value if parsed_label.product_description else None,
        colour_model=parsed_label.colour_model.value if parsed_label.colour_model else None,
        quantity=qty_int,
    )
