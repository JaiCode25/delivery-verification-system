from enum import Enum


class Platform(str, Enum):
    AMAZON = "amazon"
    FLIPKART = "flipkart"
    BLINKIT = "blinkit"
    OTHER = "other"


class OrderStatus(str, Enum):
    PLACED = "placed"
    SHIPPED = "shipped"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    ORDER_NOT_FOUND = "order_not_found"
    DETAILS_MISMATCH = "details_mismatch"
    ALREADY_USED = "already_used"
    UNREADABLE = "unreadable"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class FieldComparisonStatus(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    NOT_DETECTED = "not_detected"
    UNCERTAIN = "uncertain"


class LabelVerificationStatus(str, Enum):
    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    MISMATCH = "mismatch"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class ProductMatchDecision(str, Enum):
    MATCH = "match"
    LIKELY_MATCH = "likely_match"
    MISMATCH = "mismatch"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class OverallVerificationDecision(str, Enum):
    ISSUE_DETECTED = "issue_detected"
    VERIFIED = "verified"
    LIKELY_VERIFIED = "likely_verified"
    MISMATCH = "mismatch"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
