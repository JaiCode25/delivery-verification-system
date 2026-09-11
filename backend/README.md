# Delivery Verification and Automated Complaint System — Backend

This FastAPI backend serves as the core intelligence and verification engine for the Delivery Verification and Automated Complaint System. It accepts package images and unboxing videos, extracts frames with timecodes and sharpness scoring, detects barcodes/QR codes, reads label text via OCR, and cross-references identifiers against an order database to authenticate deliveries and prevent fraudulent claims.

---

## 1. Architecture Summary

The backend is built in Python 3.11 with FastAPI and SQLAlchemy (SQLite):

* **API Layer (`app/api/`):**
  * `analysis.py`: Endpoints to upload unboxing media, extract frames, run computer vision, and trigger automated verification.
  * `orders.py`: CRUD and query endpoints for registered customer orders and tracking numbers.
  * `verification.py`: Dedicated verification endpoints evaluating barcodes and managing complaint/claim lifecycles.
* **Service Layer (`app/services/`):**
  * `file_handler.py`: Chunked streaming upload handler enforcing size limits and validating image/video magic bytes.
  * `frame_extractor.py`: OpenCV video frame sampler extracting 1 fps with timestamps and Laplacian variance sharpness metrics.
  * `code_detector.py`: Ultra-fast C++ barcode/QR scanner (`zxing-cpp`) capturing symbology and bounding coordinates.
  * `ocr_service.py`: Deep-learning text recognizer (`easyocr`) cached as a singleton for efficiency.
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

For this reason, the system verifies barcode values against an authoritative order database, verifies customer and product consistency, tracks prior claim finalizations, and evaluates the physical evidence frame.

> **Note on College Prototype:** Because commercial production APIs from Amazon, Flipkart, and Blinkit are closed and proprietary, this college prototype uses a persistent SQLite database pre-seeded with representative demonstration orders to simulate real-world e-commerce verification.

---

## 3. Setup Commands (Windows Powershell)

Ensure you have Python 3.11 installed. From the `backend` directory:

```powershell
# Create and activate virtual environment
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1

# Upgrade pip and install dependencies
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## 4. Database Initialization & Automatic Seeding

The application automatically creates database tables and idempotently inserts demonstration orders upon startup via FastAPI's lifespan event.

To manually run the seed script:

```powershell
.\venv\Scripts\python.exe -m app.database.seed
```

---

## 5. Demonstration Barcode Values

The following fictional orders are seeded for testing:

| Platform | Tracking / Barcode Identifier | Expected Order ID | Expected Customer | Expected Product |
| :--- | :--- | :--- | :--- | :--- |
| **Flipkart** | `FMPC6414359435` | `DEMO-OD-FLIPKART-88214` | Demo Student User (Testing) | Rocsil Analog Watch (Black) |
| **Amazon** | `AMZN-IN-981240182` | `DEMO-AMZ-402-9981245-11002` | Demo Faculty Member | Sony WH-1000XM5 Headphones |
| **Blinkit** | `BLK-PNQ-5521` | `DEMO-BLK-99381023` | Demo Lab Assistant | Nescafe Classic Coffee 200g |

---

## 6. Running the Development Server

Start the Uvicorn ASGI server with reload restricted to the `app` package:

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload --reload-dir app
```

* **Interactive API Documentation (Swagger UI):** `http://127.0.0.1:8000/docs`
* **Alternative API Documentation (ReDoc):** `http://127.0.0.1:8000/redoc`

---

## 7. Running the Automated Test Suite

Run pytest with verbose output:

```powershell
.\venv\Scripts\python.exe -m pytest -v
```

All tests execute against an isolated in-memory SQLite database and never modify your development database.
