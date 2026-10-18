# Delivery Verification and Automated Complaint System — Backend

Quantity verification and visible-condition analysis are documented in
[the backend evidence guide](docs/quantity-condition.md), including model provenance,
offline defaults, APIs, report decisions, limitations and Swagger retest steps.
Customer product intake and automated complaint assistant flows are documented in
[the customer intake & complaint assistant guide](docs/customer-intake-complaints.md).
The current report rules extend the earlier Phase 3 summary below.

This FastAPI backend serves as the core intelligence and verification engine for the Delivery Verification and Automated Complaint System. It accepts package images and unboxing videos, extracts frames with timecodes and sharpness scoring, detects barcodes/QR codes, reads label text via OCR, extracts structured shipping-label metadata, executes hybrid visual product matching against stored reference images, and generates structured verification reports in JSON and responsive HTML to authenticate deliveries and prevent fraudulent claims.

> **Important System Note:**
> “This prototype uses explainable image-similarity methods and optional pretrained embeddings. It does not use a custom-trained product or damage-detection model.”

---

## 1. Architecture & Capabilities Summary

The backend is built in Python 3.11 with FastAPI and SQLAlchemy (SQLite):

* **API Layer (`app/api/`):**
  * `analysis.py`: Endpoints to upload unboxing media, extract frames, run computer vision, and trigger automated verification.
  * `orders.py`: CRUD endpoints for registered customer orders, tracking numbers, and product reference image management (`POST /api/orders/{order_id}/references`, `GET /api/orders/{order_id}/references`, `DELETE /api/orders/{order_id}/references/{reference_id}`).
  * `product_matching.py`: Visual matching endpoint (`POST /api/product-matching/analyze`) comparing delivered product images/videos against stored reference images.
  * `reports.py`: Report generation endpoints (`POST /api/reports/generate/{session_id}`, `GET /api/reports/{report_id}`, `GET /api/reports/{report_id}/html`).
  * `verification.py`: Dedicated verification endpoints evaluating barcodes, structured shipping labels, and managing complaint/claim lifecycles.
  * `customer_submissions.py`: Customer product intake endpoints (`POST /api/customer/submissions`, `GET /api/customer/submissions/{submission_id}`, `PATCH /api/customer/submissions/{submission_id}`, `POST /api/customer/submissions/{submission_id}/link-session`).
  * `complaints.py`: Automated complaint assistant endpoints (`GET /api/complaints/eligibility/{session_id}`, `POST /api/complaints/drafts`, `GET /api/complaints/drafts`, `GET /api/complaints/drafts/{draft_id}`, `PATCH /api/complaints/drafts/{draft_id}`, `GET /api/complaints/drafts/{draft_id}/preview`, `POST /api/complaints/drafts/{draft_id}/send`).
* **Service Layer (`app/services/`):**
  * `reference_service.py`: Secure storage and lifecycle management for order reference images under `storage/references/<order_id>/` (1–5 images per item, MIME validation, path traversal prevention, safe UUIDs).
  * `image_similarity.py`: Multi-signal explainable similarity algorithms (perceptual hashing via `imagehash`, normalized OpenCV HSV color histograms, OpenCV ORB visual feature matching with Lowe's ratio test, and optional lazy-loaded OpenCLIP adapter).
  * `frame_selection_service.py`: Intelligent video frame sampler picking sharp, non-duplicate candidate frames using Laplacian variance and pHash deduplication while preserving video timestamps.
  * `product_matching_service.py`: Hybrid product matching engine combining brand/model OCR token similarity and visual multi-signal matching.
  * `report_service.py`: Report synthesis engine formulating explainable verdicts (`verified`, `likely_verified`, `mismatch`, `manual_review_required`), masking customer PII, persisting JSON data, and rendering responsive HTML.
  * `file_handler.py`: Chunked streaming upload handler enforcing size limits and validating image/video magic bytes.
  * `frame_extractor.py`: OpenCV video frame sampler extracting 1 fps with timestamps and Laplacian variance sharpness metrics.
  * `code_detector.py`: Ultra-fast C++ barcode/QR scanner (`zxing-cpp`) capturing symbology and bounding coordinates.
  * `ocr_service.py`: Deep-learning text recognizer (`easyocr`) returning text, confidence, and bounding boxes.
  * `label_parser.py`: Regex and heuristic parser extracting structured shipping label fields with multi-frame consensus and contextual Flipkart Order ID normalization.
  * `label_verification_service.py`: Compares label fields against orders and executes two-source barcode-label cross-checks.
  * `order_service.py`: Database query abstractions, detail comparison logic, and claim state tracking.
  * `verification_engine.py`: Multi-factor authenticity decision engine producing standardized verification outcomes.
* **Data Layer (`app/database/`):**
  * `models.py`: SQLAlchemy models (`Order`, `OrderItem`, `ProductReference`, `VerificationSession`, `ProductMatchingRecord`, `VerificationReport`).
  * `connection.py`: Engine, connection pool, session generator, and safe SQLite column migration.
  * `seed.py`: Idempotent demonstration data seeder.
* **Storage (`storage/`):**
  * `uploads/`: Temporary uploaded media.
  * `references/`: Reference product photographs stored securely per order ID.
  * `frames/`: Extracted video frames per analysis session.
  * `results/`: Output JSON analysis reports.
  * `reports/`: Generated structured JSON reports and standalone responsive HTML reports.

---

## 2. Phase 3: Product Matching & Verification Reports

### A. Product Reference Management
* **Storage Path:** `backend/storage/references/<order_id>/`
* **Security:** Server-side UUID filenames, path traversal prevention, magic byte verification (JPEG, PNG, WEBP), configurable size limit (10 MB per image).
* **Limits:** 1–5 reference images per order item.
* **Privacy:** Relative paths stored in SQLite; absolute Windows filesystem paths are never leaked in API responses.

### B. Hybrid Product Matching Engine
The system uses an explainable multi-signal visual and textual matching approach:
1. **OCR Text Similarity (Weight: 0.35):** Tokenized brand, model, and product name matching against candidate OCR detections.
2. **ORB Visual Feature Matching (Weight: 0.30):** OpenCV ORB keypoint detection and BFMatcher with Lowe's ratio test (0.75).
3. **Perceptual Image Hash (Weight: 0.20):** Combined pHash and dHash Hamming distance calculation via `imagehash`.
4. **HSV Colour Histogram (Weight: 0.15):** Normalized 2D Hue-Saturation histogram correlation.
5. **Optional OpenCLIP Adapter:** Disabled by default (`enable_openclip: False`), lazy-loaded, with graceful fallback.

#### Score Normalization & Decisions:
* **All component scores and overall score** are normalized to the range `0.0 – 1.0`.
* **Decision Thresholds:**
  * `overall_score >= 0.75`: `match`
  * `0.55 <= overall_score < 0.75`: `likely_match`
  * `overall_score < 0.55`: `mismatch`
  * `insufficient evidence (no references/unusable frames)`: `insufficient_evidence`
* **Weight Renormalization:** When OCR text is absent from the candidate photograph, visual weights are automatically renormalized to sum to 1.0.

### C. Structured Verification Reports
Synthesizes barcode verification, 8-field shipping-label extraction, and product matching into an explainable verdict:
* `verified`: Known barcode + matching label + matching product.
* `likely_verified`: Known barcode + incomplete label + likely product match.
* `mismatch`: Barcode/order contradiction, label mismatch, or product mismatch.
* `manual_review_required`: Unreadable barcode, missing reference images, or insufficient visual evidence.
* **PII Masking:** Customer names (`D*** C***`), emails (`d***@example.com`), and street addresses are automatically masked.
* **HTML Reports:** Responsive standalone HTML document with modern dark-mode aesthetic, component breakdown scorecards, warning alerts, and mobile/laptop compatibility.

---

## 3. Demonstration Orders

The system includes pre-seeded demonstration orders:

| Platform | Order ID | Tracking ID / Barcode | Product | Expected Colour |
| :--- | :--- | :--- | :--- | :--- |
| **Flipkart** | `OD290920260550001234` | `VGR055-DEMO-2026-01` | VGR V-055 Professional Hair Trimmer | Black/Blue |
| **Flipkart** | `DEMO-OD-FLIPKART-88214`| `FMPC6414359435` | Rocsil Analog Watch For Women | Black |
| **Amazon** | `DEMO-AMZ-402-9981245-11002`| `AMZN-IN-981240182` | Sony WH-1000XM5 Wireless Headphones | Silver |
| **Blinkit** | `DEMO-BLK-99381023` | `BLK-PNQ-5521` | Nescafe Classic Coffee 200g Jar | Brown |

> **VGR Demo QR Payload:** The package QR code contains only `VGR055-DEMO-2026-01`. The backend uses this decoded value to retrieve the complete order from SQLite.

---

## 4. Open-Source Libraries Reused

| Package | Upstream URL | License | Purpose |
| :--- | :--- | :--- | :--- |
| **ImageHash** | [github.com/JohannesBuchner/imagehash](https://github.com/JohannesBuchner/imagehash) | BSD-2-Clause | Perceptual image hashing (pHash, dHash) |
| **OpenCV** | [github.com/opencv/opencv-python](https://github.com/opencv/opencv-python) | Apache 2.0 | Frame extraction, HSV color histograms, ORB feature matching |
| **EasyOCR** | [github.com/JaidedAI/EasyOCR](https://github.com/JaidedAI/EasyOCR) | Apache 2.0 | Deep-learning optical character recognition |
| **ZXing-CPP** | [github.com/zxing-cpp/zxing-cpp](https://github.com/zxing-cpp/zxing-cpp) | Apache 2.0 | High-performance 1D/2D barcode & QR scanning |
| **Jinja2** | [github.com/pallets/jinja](https://github.com/pallets/jinja) | BSD-3-Clause | Responsive HTML verification report rendering |
| **Pillow (PIL)**| [github.com/python-pillow/Pillow](https://github.com/python-pillow/Pillow) | HPND | Image format loading, conversion, and validation |

---

## 5. End-to-End Swagger Demo Sequence (VGR V-055 Trimmer)

1. **Start Backend Server:**
   * *Option A (from repository root):*
     ```powershell
     backend\venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --reload-dir backend/app
     ```
   * *Option B (from backend directory):*
     ```powershell
     cd backend
     .\venv\Scripts\python.exe -m uvicorn app.main:app --reload
     ```
2. **Open Interactive Docs:** Navigate to `http://127.0.0.1:8000/docs`.
3. **Verify Demo Order:**
   * Execute `GET /api/orders/OD290920260550001234` or `GET /api/orders/barcode/VGR055-DEMO-2026-01`.
   * Confirm the order and item details are returned.
4. **Upload Reference Product Photograph:**
   * Execute `POST /api/orders/OD290920260550001234/references`.
   * Attach your reference image file of the VGR trimmer.
   * Form fields: `product_name`: `VGR V-055 Professional Hair Trimmer`, `brand`: `VGR`, `model`: `V-055`, `colour`: `Black/Blue`.
5. **Run Package Analysis:**
   * Execute `POST /api/analyze` with the delivered package image or unboxing video.
   * Copy the returned `verification_session_id`.
6. **Run Product Matching Analysis:**
   * Execute `POST /api/product-matching/analyze`.
   * `order_id`: `OD290920260550001234`.
   * `verification_session_id`: Session ID from step 5.
   * `file`: Delivered product photograph or unboxing video frame.
   * Review component breakdown scores and decision (`match` / `likely_match`).
7. **Generate Verification Report:**
   * Execute `POST /api/reports/generate/{session_id}` with the session ID from step 5.
   * Review JSON verdict, masked customer information, and report URLs.
8. **View Responsive HTML Report:**
   * Open `http://127.0.0.1:8000/api/reports/{report_id}/html` in your browser.

---

## 6. Setup & PowerShell Commands

```powershell
# 1. Verify environment import
backend\venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); import app.main; print('Import successful')"

# 2. Run targeted Phase 3 tests
backend\venv\Scripts\python.exe -m pytest backend\tests\test_product_references.py -v
backend\venv\Scripts\python.exe -m pytest backend\tests\test_product_matching.py -v
backend\venv\Scripts\python.exe -m pytest backend\tests\test_reports.py -v

# 3. Run complete backend test suite (69 tests)
backend\venv\Scripts\python.exe -m pytest backend\tests -v

# 4. Start backend server (from repository root)
backend\venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --reload-dir backend/app

# Or start backend server (from backend directory)
cd backend
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload
```


---

## 7. Known Limitations

* **Severe Specular Reflections:** Highly reflective glossy packaging or transparent plastic wrap can degrade local visual feature (ORB) matching. Perceptual hashing and HSV color distribution compensate for this.
* **Extreme Camera Motion Blur:** Video frames captured during rapid camera rotation have reduced sharpness; the system automatically ranks frames by Laplacian variance to filter out blurry frames.
* **No Custom Fine-Tuned Model:** Visual verification relies on explainable computer vision algorithms (perceptual hashing, color histograms, ORB feature matching) and OCR tokens rather than deep end-to-end classification models.
