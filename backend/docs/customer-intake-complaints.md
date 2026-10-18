# Customer Product Intake & Complaint Assistant Guide

This document describes the customer-facing product intake workflow, SSRF-safe URL handling, deterministic demo catalogue resolution, contradiction tracking, complaint eligibility gating, draft generation, email dispatch abstraction, and step-by-step Swagger UI verification.

---

## 1. Customer-Facing Architecture Principles

1. **Zero Retailer-Internal Identifiers Asked of Customers:**
   - The customer is **never** asked for Order ID, Tracking ID, AWB, delivery ID, or verification session ID.
   - Customers provide only:
     - `product_name` (required, string, 1–255 characters)
     - `platform` (required, normalized store name e.g., Amazon, Flipkart, Blinkit)
     - `product_url` (required, valid HTTPS URL)
     - `expected_quantity` (required, integer, 1 to 100)
     - `colour_or_variant` (optional)
     - `customer_name` (optional)
     - `customer_email` (optional)
     - `notes` (optional)
   - Retailer-internal IDs are derived later via package OCR/barcode scan or verification session linkage.

2. **Server-Generated Submission ID:**
   - Submissions generate a unique server-side identifier (`id` / `submission_id`) used for retrieval, updates, and session linking.

3. **URL Normalization & Security:**
   - Scheme and hostname are normalized to lower-case.
   - Standard affiliate/tracking parameters (`utm_*`, `ref`, `tag`, `qid`, `sr`, `keywords`, `pf_rd_*`, `pd_rd_*`, `linkcode`, `creative`, etc.) and fragments (`#...`) are stripped.
   - Original URL and normalized URL are both persisted.

4. **Deterministic Demo Catalogue Matching:**
   - Submissions are matched against the local demonstration catalogue using token overlap across product names, brands, models, and platforms.
   - Matched references are explicitly tagged with `reference_source="demo_catalogue"`.
   - The backend **never** claims that an image originated from the customer's remote URL.
   - If no confident local match exists, it gracefully marks `reference_source="reference_unavailable"`.

5. **Non-Destructive Contradiction Tracking:**
   - When a submission is linked to a `verification_session_id`, the backend cross-references customer claims against the verified order record (platform, expected quantity, customer email, customer name).
   - Any discrepancies are recorded in a structured `contradictions` list without overwriting or deleting either the customer's original input or the verified order data.

---

## 2. SSRF Protection & URL Metadata Extraction

- **Disabled by Default:** Remote URL metadata extraction is turned off by default (`enable_url_metadata_extraction=false`).
- **Defenses When Enabled:**
  - Strict HTTPS requirement (plain HTTP and FTP rejected).
  - Loopback and localhost destinations blocked (`localhost`, `127.0.0.1`, `::1`, `0.0.0.0`).
  - Domain allowlist enforced (`settings.url_metadata_allowed_domains`: `amazon.in`, `amazon.com`, `flipkart.com`, `blinkit.com`).
  - DNS resolution validation: destination hostnames are resolved via DNS before connection; private, loopback, link-local, multicast, or reserved IPs are blocked.
  - Strict connection timeout (`5.0s`) and response payload limit (`1 MB`).
  - Manual redirect inspection with full SSRF re-validation on each hop.
  - Graceful fallback: any network or parsing error falls back silently to the demo catalogue.

---

## 3. Complaint Eligibility Engine & Safeguards

The complaint assistant strictly gates automated complaint recommendations behind a high-confidence threshold (default **0.80**):

- **Eligible Issue Types:**
  - `wrong_product`: Product matching reported `decision="mismatch"` with confidence $\ge 0.80$.
  - `quantity_mismatch`: Quantity verification reported `status="mismatch"` with confirmed count discrepancy and confidence $\ge 0.80$.
  - `damaged_item`: Visible condition analysis reported `status="damaged"` with confidence $\ge 0.80$.
- **Inconclusive / Rejected Evidence (Not Eligible):**
  - Channels reporting `unsupported_product`, `insufficient_evidence`, `manual_review_required`, `model_unavailable`, or `no_visible_damage` do not trigger automated complaint recommendations.
  - Intermediate statuses like `likely_damaged` emit warnings recommending manual inspection before formal complaint filing.
- **Strict Default Rejection (HTTP 409 Conflict):**
  - By default, `POST /api/complaints/drafts` **rejects** draft creation with **HTTP 409 Conflict** if `eligibility.eligible` is `false` or `issue_types` is empty.
  - The 409 response payload returns the complete eligibility context:
    ```json
    {
      "detail": {
        "message": "Complaint draft generation rejected: Verification evidence does not qualify for an automated complaint recommendation.",
        "eligible": false,
        "issue_types": [],
        "warnings": [...],
        "reasons": [...],
        "confidence_values": {...},
        "supporting_analysis_ids": {...},
        "threshold": 0.80,
        "allow_manual_inquiry_hint": "Set allow_manual_inquiry=true to generate a manual review inquiry when automated evidence is inconclusive."
      }
    }
    ```
- **Manual Inquiry Override (`allow_manual_inquiry=true`):**
  - If a user wishes to escalate an inconclusive verification for manual customer service review, they must explicitly pass `"allow_manual_inquiry": true` (default is `false`).
  - Manual inquiries follow strict non-accusatory formatting rules:
    1. Subject: `Manual Delivery Verification Inquiry – [Product Name]`.
    2. Tone: Inconclusive; states clearly that automated package checks did not confirm a defect.
    3. Action: Formally requests human review by the support team.
    4. Remedy: Phrased conditionally: *"If an issue is confirmed after review, I request..."*.
    5. Never uses accusatory phrases like *"damaged or incorrect merchandise"* when no defect was confirmed.

---

## 4. Complaint Draft Generation, Platform Contradictions & Evidence Integrity

- **Platform Contradiction Handling:**
  - When the customer's specified platform (e.g. Amazon) contradicts the verified order record (e.g. Flipkart):
    - The backend **never** guesses or arbitrarily chooses either retailer.
    - Recipient email is left unresolved (`""`), and the addressee is set to generic `Customer Support Team`.
    - Any recipient supplied during draft generation or regeneration (`regenerate=true`) is ignored while the contradiction is unresolved.
    - Existing recipient emails are cleared upon draft regeneration or transition between formal complaint and manual inquiry whenever a platform contradiction is present.
    - A visible warning is appended to `warnings`:
      > *"Platform contradiction detected: Customer specified 'Amazon', but verified order record is 'Flipkart'. Recipient and platform addressee remain unresolved until confirmed."*
    - Attempting to send an unresolved draft (`POST /api/complaints/drafts/{id}/send`) is rejected with **HTTP 400 Bad Request**.
    - Resolving the contradiction requires an explicit destination confirmation via `PATCH /api/complaints/drafts/{id}`:
      - `intended_platform`: Confirmed retailer platform (e.g. `"Amazon"` or `"Flipkart"`).
      - `recipient_email`: Valid production support email (placeholder domains like `@example.com`, `@example.org`, `@example.net` are rejected).
      - `confirmed_destination`: `true` to record explicit user confirmation.
- **Evidence & Attachment Grounding:**
  - The draft lists only files that actually exist in storage on disk (`actual_attached_filenames`).
  - If `report_id` is null, the draft explicitly states `Verification Report: None generated` and does not claim an attached report.
  - Vague or unestablished claims (such as *"evidence has been audited for data integrity"*) are omitted.
- **Professional & Objective Phrasing:**
  - Factual and respectful tone suitable for customer support teams.
  - No defamatory, abusive, or unsubstantiated legal claims.
  - Clearly includes:
    - Order & verification metadata
    - Technical verification findings with confidence scores
    - Customer remarks (sanitized against XSS injection)
    - Standard disclaimers acknowledging external visible condition assessment limitations.
- **Resolution Options:**
  - `replacement`: Full replacement of damaged or incorrect merchandise (or conditional request if manual inquiry).
  - `refund`: Full refund to original payment method.
  - `missing_item_refund`: Partial refund corresponding to missing units.
  - `other`: Policy-based customer remedy.
- **Draft Formats:**
  - Generates both clean plain text (`body_text`) and sanitized HTML (`body_html`).
  - Draft response models include `eligible`, `manual_inquiry`, `warnings`, and `issue_types`.
  - Supports draft preview (`GET /api/complaints/drafts/{id}/preview`) and in-place editing (`PATCH /api/complaints/drafts/{id}`).
  - Supports explicit draft regeneration (`regenerate=true`).

---

## 5. Mail Delivery System & Security

- **Delivery Providers:**
  - `DryRunMailProvider` (default): Simulates email transmission without external networking. Safe for test suites, CI, and local demos.
  - `FastMailSmtpProvider`: Production SMTP transport using `fastapi-mail` with STARTTLS/SSL, credential masking, and TLS certificate validation.
- **Safety Safeguards:**
  - **Explicit Confirmation Required:** `POST /api/complaints/drafts/{id}/send` rejects requests without `confirmed=true` (HTTP 400).
  - **Idempotency Protection:** Requests with an `idempotency_key` prevent duplicate transmissions from rapid button clicks.
  - **Rate Limiting:** Dispatches for the same draft within 5 seconds return HTTP 429.
  - **Attachment Security (`validate_and_prepare_attachments`):**
    - Path traversal sequences (`../`, `..\`) are stripped and rejected.
    - Prohibited sensitive extensions are blocked (`.pt`, `.pth`, `.bin`, `.db`, `.sqlite`, `.log`).
    - Files outside permitted storage directories are blocked.
    - Attachment size limit enforced (max 10 MB per email).
  - **Audit Logging:** Every attempt (success, failure, dry run, warnings) is persisted in `delivery_attempts`.

---

## 6. Swagger UI Manual Retest Walkthrough

Follow these steps in the interactive Swagger UI (`http://127.0.0.1:8000/docs`):

### Step 1: Submit Customer Product Intake
- **Endpoint:** `POST /api/customer/submissions`
- **Request Body:**
  ```json
  {
    "product_name": "VGR V-055 Professional Hair Trimmer",
    "platform": "Flipkart",
    "product_url": "https://www.flipkart.com/vgr-v-055-trimmer/p/itm12345?ref=tracker",
    "expected_quantity": 1,
    "colour_or_variant": "Black",
    "customer_name": "Demo Customer",
    "customer_email": "customer@example.com",
    "notes": "Delivered to front porch."
  }
  ```
- **Verify:**
  - Status 201 Created.
  - `submission_id` is returned.
  - `normalized_product_url` has tracking parameter `ref=tracker` removed.
  - `resolved_reference.reference_source` is `"demo_catalogue"`.

### Step 2: Retrieve & Update Customer Submission
- **Endpoint:** `GET /api/customer/submissions/{submission_id}`
  - Verify details match submission.
- **Endpoint:** `PATCH /api/customer/submissions/{submission_id}`
  - Update `notes` or `colour_or_variant`.

### Step 3: Link Submission to a Verification Session
- **Endpoint:** `POST /api/customer/submissions/{submission_id}/link-session`
- **Request Body:**
  ```json
  {
    "verification_session_id": "<SESSION_ID_FROM_ANALYZE>"
  }
  ```
- **Verify:**
  - `verification_session_id` and `order_id` are linked.
  - If customer specified a platform or quantity differing from order records, the `contradictions` list records each field without destroying data.

### Step 4: Check Complaint Eligibility
- **Endpoint:** `GET /api/complaints/eligibility/{verification_session_id}`
- **Verify:**
  - Returns `eligible: true/false`.
  - Shows `issue_types` (`wrong_product`, `quantity_mismatch`, `damaged_item`), confidence values, and reasons.

### Step 5: Generate Complaint Draft (with Eligibility Safeguard)
- **Endpoint:** `POST /api/complaints/drafts`
- **Case A — Ineligible Session (Default Safeguard):**
  - If the session does not have confirmed defect evidence, calling `POST /api/complaints/drafts` will return **409 Conflict** with reasons and warnings explaining why automated complaints are disabled.
- **Case B — Inconclusive Inquiry Override:**
  - If manual support review is desired, submit with:
    ```json
    {
      "verification_session_id": "<SESSION_ID>",
      "allow_manual_inquiry": true,
      "requested_resolution": "replacement"
    }
    ```
  - Returns 201 Created with subject: `Manual Delivery Verification Inquiry – [Product]` and non-accusatory conditional wording.
- **Case C — Eligible Session (Defect Detected):**
  - Returns 201 Created with `draft_id`, specific issue details, and objective defect evidence.

### Step 6: Preview and Edit Draft
- **Endpoint:** `GET /api/complaints/drafts/{draft_id}/preview`
  - Review HTML and plain-text output, `eligible`, `manual_inquiry`, `warnings`, and `issue_types`.
- **Endpoint:** `PATCH /api/complaints/drafts/{draft_id}`
  - Customize subject or requested resolution.
  - **Required on Platform Contradiction:** If customer platform differs from order records, `recipient_email` is unresolved (`""`). Resolve and confirm the destination before sending:
    ```json
    {
      "intended_platform": "Amazon",
      "recipient_email": "cs-support@amazon.in",
      "confirmed_destination": true
    }
    ```
    *(Note: Placeholder domains like `@example.com` are rejected).*

### Step 7: Send Complaint
- **Endpoint:** `POST /api/complaints/drafts/{draft_id}/send`
- **Request Body (Safe Confirmation):**
  ```json
  {
    "confirmed": true,
    "idempotency_key": "swagger-test-001"
  }
  ```
- **Verify:**
  - If `recipient_email` was left unresolved, returns **HTTP 400 Bad Request** instructing user to update recipient email.
  - When resolved, `delivery_status` is `"dry_run"`.
  - `delivery_attempt` record returned with timestamp.
  - Repeating with same `idempotency_key` returns the cached status without redispatching.

### Step 8: View Final Report
- **Endpoint:** `GET /api/reports/{report_id}` and `GET /api/reports/{report_id}/html`
- **Verify:**
  - Customer product details, reference source, and complaint assistant card are rendered.
