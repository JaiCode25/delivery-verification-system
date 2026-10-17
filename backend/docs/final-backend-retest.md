# Final Backend Swagger Retest Guide

This document provides exact, reproducible steps for manually testing all backend capabilities (Order lookup, Barcode/QR verification, Shipping-label OCR, Product matching, Quantity verification, Visible-condition analysis, and JSON/HTML report generation) using the FastAPI Swagger UI.

---

## 1. Environment & Offline Model Setup

Execute the following PowerShell commands from the repository root (`C:\Users\Lenovo\OneDrive\Desktop\EDI\delivery-verification-system`):

```powershell
# 1. Ensure models are pre-cached in local git-ignored storage
backend\venv\Scripts\python.exe backend\scripts\cache_evidence_models.py quantity
backend\venv\Scripts\python.exe backend\scripts\cache_evidence_models.py condition

# 2. Configure environment variables for offline real-model testing
$env:ENABLE_QUANTITY_MODEL = 'true'
$env:ENABLE_CONDITION_MODEL = 'true'
$env:MODEL_OFFLINE = 'true'
$env:MODEL_DEVICE = 'cpu'
$env:MODEL_CACHE_DIR = "$PWD\backend\storage\models"
```

---

## 2. Server Launch

Run Uvicorn from the repository root:

```powershell
backend\venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --reload-dir backend/app
```

Open Swagger UI in your browser:
**`http://127.0.0.1:8000/docs`**

---

## 3. Recommended Endpoint Execution Order

Follow this exact sequence to complete a full delivery verification workflow:

1. **Scan Shipping Label / Barcode & Obtain Session ID**:
   - `POST /api/analyze`
   - **Form Fields**: `expected_order_id` (e.g. `OD290920260550001234` or `VGR055-DEMO-2026-01`), `file` (Upload image/video of package label/QR).
   - **Output**: Copy `order_verification.verification_session_id` (e.g. `f7b26ba5ea894d8daded04f930e1dc18`).

2. **Upload Reference Product Images (If Not Pre-existing)**:
   - `POST /api/orders/{order_id}/references`
   - **Form Fields**: `files` (Upload clear reference photographs of the expected item).

3. **Run Product Visual Matching**:
   - `POST /api/product-matching/analyze`
   - **Form Fields**: `order_id`, `verification_session_id`, `file` (Delivered product photo/video).
   - **Output**: Multi-modal similarity score (OCR, ORB, pHash, HSV color) and decision (`match`, `mismatch`, etc.).

4. **Run Quantity Verification**:
   - `POST /api/quantity-verification/analyze`
   - **Form Fields**: `order_id`, `verification_session_id`, `file` (Delivered items photo/video), `regenerate` (`false` or `true`).
   - **Output**: `detected_quantity`, `target_class`, `status` (`match`, `mismatch`, `unsupported_product`, etc.).

5. **Run Visible Condition Analysis**:
   - `POST /api/condition-analysis/analyze`
   - **Form Fields**: `order_id`, `verification_session_id`, `file`, `evidence_type` (`sealed_package`, `opened_package`, `product`, `unknown`), `regenerate` (`false` or `true`).
   - **Output**: Zero-shot prompt scores, ORB edge cues, and `status` (`no_visible_damage`, `damaged`, `likely_damaged`, `manual_review_required`, etc.).

6. **Generate Comprehensive Verification Report**:
   - `POST /api/reports/generate/{verification_session_id}`
   - **Output**: Complete structured report containing all 5 verification channels, overall decision, explanations, warnings, and masked PII.

7. **Retrieve JSON / HTML Reports**:
   - **JSON API**: `GET /api/reports/{report_id}`
   - **HTML Browser View**: `GET /api/reports/{report_id}/html`

---

## 4. Test Cases & Expected Behaviors

### Case A: Unsupported Product (VGR V-055 Trimmer Demo)
- **Order ID**: `VGR055-DEMO-2026-01`
- **Behavior**: Quantity analysis returns `status: unsupported_product` and `detected_quantity: null`. Expected quantity is never assumed or fabricated.

### Case B: Supported COCO Class — Correct Quantity
- **Order**: Product named `cup`, expected quantity `1`.
- **Evidence**: Image with 1 visible cup.
- **Behavior**: Quantity status returns `match`. If product condition is clean, condition returns `no_visible_damage`. Report verdict returns `verified` or `likely_verified`.

### Case C: Supported COCO Class — Wrong Quantity
- **Order**: Product named `cup`, expected quantity `2`.
- **Evidence**: Image with 1 visible cup.
- **Behavior**: Quantity status returns `mismatch`. Overall report decision yields `mismatch`.

### Case D: Damaged Product Evidence
- **Evidence**: Product image with visible cracks/tears and matching reference.
- **Behavior**: Condition status returns `damaged` or `likely_damaged`. Report decision yields `issue_detected` or `manual_review_required`.

### Case E: Blurred / Poor-Quality Evidence
- **Evidence**: Blurry or dark photograph (sharpness < 30).
- **Behavior**: Returns `insufficient_evidence` with explicit warnings. Automated decision does not guess.

### Case F: Model Unavailable / Disabled Fallback
- **Environment**: `$env:ENABLE_QUANTITY_MODEL = 'false'` or missing model weights.
- **Behavior**: API returns HTTP 200/201 with `status: model_unavailable` and a clear warning. HTTP 500 is never raised.

---

## 5. Security & Privacy Guarantees

- **PII Masking**: All customer names (`D*** C***`), emails (`u***@example.com`), and addresses (`*** Hidden for Privacy *** [PIN: 110001]`) are automatically masked in public JSON/HTML reports.
- **Path Isolation**: Internal filesystem paths are excluded from API outputs.
- **Idempotency**: Regenerating reports with `regenerate=true` reuses session IDs and report IDs without creating orphan database records.
