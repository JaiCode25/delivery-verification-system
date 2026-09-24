# Delivery Verification and Automated Complaint System — Backend

This FastAPI backend serves as the core intelligence and verification engine for the Delivery Verification and Automated Complaint System. It accepts package images and unboxing videos, extracts frames with timecodes and sharpness scoring, detects barcodes/QR codes, reads label text via OCR, extracts structured shipping-label metadata, and cross-references identifiers against an authoritative order database to authenticate deliveries and prevent fraudulent claims.

---

## 1. Architecture Summary

The backend is built in Python 3.11 with FastAPI and SQLAlchemy (SQLite):

* **API Layer (`app/api/`):**
  * `analysis.py`: Endpoints to upload unboxing media, extract frames, run computer vision, and trigger two-factor automated verification.
  * `orders.py`: CRUD and query endpoints for registered customer orders and tracking numbers.
  * `verification.py`: Dedicated verification endpoints evaluating barcodes, structured shipping labels, and managing complaint/claim lifecycles.
* **Service Layer (`app/services/`):**
  * `file_handler.py`: Chunked streaming upload handler enforcing size limits and validating image/video magic bytes.
  * `frame_extractor.py`: OpenCV video frame sampler extracting 1 fps with timestamps and Laplacian variance sharpness metrics.
  * `code_detector.py`: Ultra-fast C++ barcode/QR scanner (`zxing-cpp`) capturing symbology and bounding coordinates.
  * `ocr_service.py`: Deep-learning text recognizer (`easyocr`) returning text, confidence, and bounding boxes.
  * `label_parser.py`: Regex and heuristic parser extracting structured shipping label fields with multi-frame consensus.
  * `label_verification_service.py`: Compares label fields against orders and executes two-source barcode-label cross-checks.
  * `order_service.py`: Database query abstractions, detail comparison logic, and claim state tracking.
  * `verification_engine.py`: Multi-factor authenticity decision engine producing 6 standardized verification outcomes.
* **Data Layer (`app/database/`):**
  * `models.py`: SQLAlchemy models (`Order`, `OrderItem`, `VerificationSession`).
  * `connection.py`: Engine, connection pool, and session generator.
  * `seed.py`: Idempotent demonstration data seeder.
* **Storage (`storage/`):**
  * `uploads/`: Temporary uploaded media.
  * `frames/`: Extracted video frames per analysis session.
  * `results/`: Output JSON analysis reports.
  * `reports/`: Generated PDF verification reports (future phase).

---

## 2. Why Barcode Scanning Alone Does Not Prove Authenticity

Simply decoding a barcode or QR code does not guarantee that a delivery is legitimate or that a customer's claim is valid:
* **Counterfeit Labels:** Anyone can print a generic barcode or copy an old tracking number onto an empty box.
* **Mismatched Shipments:** A delivery may belong to a completely different customer, address, or product than what is being claimed.
* **Repeated Claim Fraud:** The same valid shipping label could be photographed multiple times or used across separate complaint claims.
* **Cancelled / Returned Orders:** The label might match an order that was previously cancelled or marked undeliverable.

For this reason, the system treats **barcodes** and **shipping-label text** as two independent evidence sources, verifying values against an authoritative order database, checking customer and product consistency, and tracking prior claim finalizations.

> **Note on College Prototype:** Because commercial production APIs from Amazon, Flipkart, and Blinkit are closed and proprietary, this college prototype uses a persistent SQLite database pre-seeded with representative demonstration orders to simulate real-world e-commerce verification.

---

## 3. Shipping-Label Analysis Flow & Extracted Fields

The shipping-label parser extracts and normalizes structured fields from raw OCR bounding boxes:

1. **Extracted Fields:**
   * `tracking_id`: Canonical courier tracking number (e.g. `FMPC6414359435`, `TBA123456789012`, `BLK-PNQ-5521`).
   * `order_id`: E-commerce platform order ID (e.g. `DEMO-OD-FLIPKART-88214`, `402-9981245-11002`).
   * `platform`: Shopping platform (`amazon`, `flipkart`, `blinkit`, `other`).
   * `courier_name`: Courier service provider (Ekart Logistics, Amazon Shipping / ATS, Delhivery, Blue Dart, etc.).
   * `customer_name`: Recipient name parsed from delivery address lines.
   * `pin_code`: 6-digit destination postal PIN code.
   * `phone_number`: Recipient contact number (strictly masked for privacy, e.g. `XXXXXX3210`).
   * `seller_name`: Merchant or seller listed on invoice/label.
   * `product_description`: Printed product title, SKU, or model description.
   * `quantity`: Printed item quantity.

2. **Field Confidence Scoring:**
   Every field includes a confidence score (0.0 to 1.0) combining underlying OCR quality, pattern strength (explicit prefix vs regex token), and cross-frame observation consistency.

3. **Multi-Frame Consensus:**
   In video unboxing streams, the same label may appear across multiple frames. The parser groups identical normalized candidates, counts distinct supporting frames, and selects the sharpest evidence frame while prioritizing multi-frame agreement.

---

## 4. Barcode vs. Label Cross-Check Logic

| Scenario | Barcode Result | Label OCR Result | Final Combined Decision | Explanation |
| :--- | :--- | :--- | :--- | :--- |
| **1. Full Agreement** | Matches Order A | Matches Order A | `verified` | Strong independent verification from both physical sources. |
| **2. Label Unreadable** | Matches Order A | Insufficient text | `verified` (with notice) | Barcode verified; physical label is blurry or partially obscured. |
| **3. Direct Contradiction**| Matches Order A | Matches Order B / conflicts | `details_mismatch` / `manual_review_required` | Physical package label text directly contradicts the scanned barcode. |
| **4. Barcode Missing** | Not found / unreadable | Matches Order A | `manual_review_required` | Label matches registered order, but barcode was unreadable or missing. |
| **5. Neither Found** | Not found | Not found | `order_not_found` / `insufficient_evidence` | Package identifier is completely unrecognized in the system. |

---

## 5. Privacy Safeguards & Compliance

* **Phone Number Masking:** Customer phone numbers detected on shipping labels are masked (only the last 4 digits are retained).
* **PII Redaction:** Full street addresses and customer email addresses are never returned in public label comparisons.
* **Local Processing:** EasyOCR and ZXing run entirely offline on the local CPU/GPU; no image data is sent to external cloud APIs.

---

## 6. Example API Output (`POST /api/analyze`)

```json
{
  "analysis_id": "cf7c7795cc004acf8f8f345b08c1f9a3",
  "status": "completed",
  "created_at": "2026-09-19T18:00:00Z",
  "original_filename": "shipping_label.png",
  "input_type": "image",
  "frames_processed": 1,
  "codes": [
    {
      "type": "Code 128",
      "text": "FMPC6414359435",
      "first_seen_in": "shipping_label.png",
      "sharpness": 284.15,
      "video_timestamp_seconds": 0.0,
      "position": {
        "top_left": {"x": 42, "y": 180},
        "top_right": {"x": 310, "y": 180},
        "bottom_right": {"x": 310, "y": 240},
        "bottom_left": {"x": 42, "y": 240}
      }
    }
  ],
  "order_verification": {
    "verification_status": "verified",
    "matched_order": {
      "order_id": "DEMO-OD-FLIPKART-88214",
      "tracking_id": "FMPC6414359435",
      "barcode_value": "FMPC6414359435",
      "platform": "flipkart",
      "customer_name": "Demo Student User (Testing)",
      "order_status": "delivered"
    },
    "mismatch_reasons": []
  },
  "label_analysis": {
    "status": "verified",
    "parsed_fields": {
      "tracking_id": {
        "value": "FMPC6414359435",
        "normalized_value": "FMPC6414359435",
        "confidence": 0.95
      },
      "order_id": {
        "value": "DEMO-OD-FLIPKART-88214",
        "normalized_value": "DEMOODFLIPKART88214",
        "confidence": 0.95
      },
      "platform": {
        "value": "flipkart",
        "normalized_value": "flipkart",
        "confidence": 0.95
      },
      "customer_name": {
        "value": "Demo Student User",
        "normalized_value": "demo student user",
        "confidence": 0.90
      },
      "pin_code": {
        "value": "411037",
        "normalized_value": "411037",
        "confidence": 0.90
      }
    },
    "field_comparisons": [
      {
        "field_name": "tracking_id",
        "status": "match",
        "label_value": "FMPC6414359435",
        "order_value": "FMPC6414359435",
        "confidence": 0.95
      },
      {
        "field_name": "platform",
        "status": "match",
        "label_value": "flipkart",
        "order_value": "flipkart",
        "confidence": 0.95
      }
    ],
    "supporting_frames": ["shipping_label.png"],
    "warnings": []
  }
}
```

---

## 7. Known OCR Limitations

* **Severe Motion Blur:** Fast camera motion during unboxing videos reduces OCR character recognition accuracy. The system uses Laplacian variance sharpness scoring to mitigate this by picking the sharpest frames.
* **Creased & Damaged Labels:** Thermal paper creases or barcode tearing can obscure characters; multi-frame consensus aggregates observations across multiple viewing angles.
* **Low Thermal Contrast:** Faded thermal shipping labels with low print contrast may require manual review.

---

## 8. Setup & Test Commands (Windows Powershell)

Ensure you have Python 3.11 installed. From the `backend` directory:

```powershell
# 1. Activate virtual environment
.\venv\Scripts\Activate.ps1

# 2. Install dependencies
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. Verify backend import
.\venv\Scripts\python.exe -c "import app.main; print('Import successful')"

# 4. Run full automated test suite (33 tests)
.\venv\Scripts\python.exe -m pytest -v

# 5. Start development server
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload --reload-dir app
```

* **Interactive API Documentation (Swagger UI):** `http://127.0.0.1:8000/docs`
* **Alternative API Documentation (ReDoc):** `http://127.0.0.1:8000/redoc`
