# Quantity and visible-condition evidence

These modules assess uploaded images and prerecorded videos. They do not use a project-trained detector, infer unseen items, or diagnose internal, electrical or functional damage. The frontend is unchanged.

## Models and provenance

| Use | Package pin | Model / checkpoint | Source | License |
| --- | --- | --- | --- | --- |
| Visible object counts | `ultralytics==8.3.200` | YOLOv8 nano, `yolov8n.pt`, COCO classes | [Ultralytics repository](https://github.com/ultralytics/ultralytics), [official checkpoint release](https://github.com/ultralytics/assets/releases/tag/v8.3.0) | AGPL-3.0; see [vendor licensing](https://www.ultralytics.com/license) |
| Supporting zero-shot condition scores | `open_clip_torch==3.2.0` | `ViT-B-32`, `laion2b_s34b_b79k`, `open_clip_pytorch_model.bin` | [OpenCLIP repository](https://github.com/mlfoundations/open_clip), [LAION checkpoint card](https://huggingface.co/laion/CLIP-ViT-B-32-laion2B-s34B-b79K) | MIT for package and selected checkpoint |

YOLOv8n is approximately 6 MB; the selected OpenCLIP weight file is approximately 605 MB ([file listing](https://huggingface.co/laion/CLIP-ViT-B-32-laion2B-s34B-b79K/tree/main)). OpenCLIP is relatively small within the CLIP family but still considerably heavier than YOLOv8n on a laptop. No weights were downloaded during implementation or normal tests.

Compatibility was checked against local Python 3.11.9, Torch 2.14.0+cpu and Torchvision 0.29.0+cpu. These satisfy the selected package versions' declared requirements: [Ultralytics 8.3.200](https://github.com/ultralytics/ultralytics/blob/v8.3.200/pyproject.toml), [OpenCLIP 3.2.0](https://github.com/mlfoundations/open_clip/blob/v3.2.0/pyproject.toml). A successful package import is not an accuracy evaluation. Real-weight smoke tests require explicit opt-in.

Install from the repository root, reusing the existing Torch installation:

```powershell
backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

Both models are disabled and offline by default. Application import and startup do not load these models or download their weights. Adapters load lazily, cache models in memory and serialize access to the shared instances. Disabled models, missing packages/weights and inference failures produce a persisted `model_unavailable` result. Re-enable and explicitly regenerate after resolving the cause.

To pre-cache trusted official weights explicitly:

```powershell
backend\venv\Scripts\python.exe backend\scripts\cache_evidence_models.py quantity
backend\venv\Scripts\python.exe backend\scripts\cache_evidence_models.py condition
$env:ENABLE_QUANTITY_MODEL = 'true'
$env:ENABLE_CONDITION_MODEL = 'true'
$env:MODEL_OFFLINE = 'true'
```

Alternatively, `MODEL_OFFLINE=false` permits download of the documented checkpoints on the first real inference that needs them. Downloads use unique partial files and an atomic rename; a download failure returns a controlled unavailable result. Custom checkpoints must be placed explicitly inside `MODEL_CACHE_DIR`; they are never downloaded automatically. Weight files are trusted model inputs, not upload inputs. The default cache is `backend/storage/models/`, ignored by Git. Package caches, model settings and Matplotlib/Torch caches are directed below the model cache on real adapter use.

## Configuration

Settings use environment variables (or `.env` in the current working directory). Defaults:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ENABLE_QUANTITY_MODEL`, `ENABLE_CONDITION_MODEL` | `false` | Enable real adapters independently |
| `MODEL_DEVICE` | `cpu` | `cpu`, `auto`, or `cuda`; unavailable explicit CUDA degrades safely |
| `MODEL_OFFLINE` | `true` | Require local weights |
| `MODEL_CACHE_DIR` | `backend/storage/models` absolute path | Trusted weights and third-party caches |
| `QUANTITY_CHECKPOINT` | `yolov8n.pt` | Local checkpoint name, constrained to model cache |
| `CONDITION_MODEL_NAME` | `ViT-B-32` | OpenCLIP architecture |
| `CONDITION_PRETRAINED` | `laion2b_s34b_b79k` | Provenance tag |
| `CONDITION_CHECKPOINT` | `open_clip_pytorch_model.bin` | Local weights matching the configured architecture/tag |
| `QUANTITY_CONFIDENCE_THRESHOLD` | `0.65` | Minimum accepted box score |
| `QUANTITY_TRACK_IOU` | `0.30` | Approximate adjacent selected-frame association |
| `EVIDENCE_MAX_FRAMES` | `8` | Maximum selected observations (2–32) |
| `EVIDENCE_MIN_SHARPNESS` | `30` | Laplacian variance after bounded image loading |
| `EVIDENCE_MIN_DIMENSION` | `128` | Minimum image dimension in pixels |
| `CONDITION_DAMAGE_THRESHOLD` | `0.90` | Strong positive prompt score |
| `CONDITION_LIKELY_THRESHOLD` | `0.75` | Moderate positive prompt score |
| `CONDITION_INTACT_THRESHOLD` | `0.80` | Negative/normal prompt score |
| `CONDITION_MARGIN_THRESHOLD` | `0.25` | Minimum score difference between ensembles |
| `CONDITION_EDGE_CHANGE_THRESHOLD` | `0.18` | Changed fraction of comparable aligned edges |
| `RETAIN_UNSELECTED_EVIDENCE_FRAMES` | `true` | If false, remove only unselected frames from the current request's new directory |
| `MAX_FILE_SIZE` | `209715200` | Existing streaming upload limit, bytes |
| `ALLOWED_IMAGE_EXTENSIONS`, `ALLOWED_VIDEO_EXTENSIONS` | Existing image/video sets | JSON arrays configuring supported formats |

`QUANTITY_CLASS_MAPPING` is a JSON object mapping **exact normalized stored product names** to checkpoint class names. Defaults cover `bottle`, `cup`, `bowl`, `book`, `chair`, `laptop`, and `cell phone`. For a known supported product, an administrator may configure:

```powershell
$env:QUANTITY_CLASS_MAPPING = '{"ceramic mug 300ml":"cup","cup":"cup"}'
```

There is no client-controlled class or expected-quantity override. A trimmer, watch, headphones or coffee jar is not silently mapped to an unrelated COCO class. Multiple order line items are unsupported by this first implementation. Positive expected quantities come exclusively from the stored order; missing or nonpositive quantities are rejected.

## Algorithms and limits

Quantity reuses secure streaming upload and the existing frame extraction/selection pipeline. EXIF, palette and one-bit inputs are normalized in memory without changing evidence. Poor focus, exposure, size or contrast prevents counting. YOLO boxes below 0.65, truncated boxes and overlapping objects prevent a reliable count. Boxes include confidence, class, source filename, timestamp, approximate track ID and the resized coordinate-space width/height.

Each frame uses one-to-one IoU track association with the previous selected observation. The final count is a stable **simultaneous frame count**, never a sum of detections or all track IDs. Video requires at least two useful agreeing observations. Empty detections mean insufficient evidence, not proof of zero items. All items must be presented together, unobstructed. Sampling, camera movement, hidden items and similar background objects remain limitations; no general detector proves shipment completeness. A product match alone never sets quantity.

Condition uses evidence-type-specific ensembles of damaged and normal prompts. Normal opening is not treated as a broken seal. Images are assessed only after quality checks. Product references may provide ORB/RANSAC alignment and tolerant edge comparisons, gated by correspondence count, inlier ratio, near-matching perspective, coverage and photometric correlation. The least-changed comparable reference is used. Edge differences alone never classify damage and do not identify a specific crack, dent or missing part. Shadows, viewpoint and texture changes can still confuse generic models; uncertain cases need a reviewer.

`damaged` requires at least two useful agreeing observations, scores >=0.90 with sufficient margin, and independent aligned structural support in every observation. A single supported positive image or moderate supported positive evidence yields `likely_damaged`. Contradictory views, weak margins, or positive prompts without structural support yield `manual_review_required`. Good-quality consistent negative evidence without a structural anomaly may yield **“no visible damage detected”** in the supplied views. It never certifies an undamaged or functional product.

Stored reference images describe products, not packaging. Packaging is therefore **not** compared against product photographs. Strong packaging prompts currently require manual review because there is no dedicated packaging-reference or trained tear/seal detector. This is an explicit extension point, not a completed arbitrary-damage classifier. For custom damage cues, extend `compare_surface` and test the evidence contract; do not convert generic CLIP scores into definitive diagnoses.

## Persistence, API and reports

Two new tables, `quantity_verification_records` and `condition_analysis_records`, contain `id`, unique `verification_session_id`, `order_id`, `status`, JSON `details`, and `created_at`. Existing `init_db()` creates them additively with SQLAlchemy `create_all`; no development database was migrated, deleted or recreated during implementation. Existing rows, sessions and serialized reports require no rewrite.

| Endpoint | Fields / response |
| --- | --- |
| `POST /api/quantity-verification/analyze` | Multipart `order_id`, `verification_session_id`, `file`, optional `regenerate=false` |
| `GET /api/quantity-verification/{analysis_id}` | Stored quantity result |
| `POST /api/condition-analysis/analyze` | Same fields plus `evidence_type`: `sealed_package`, `opened_package`, `product`, `unknown` |
| `GET /api/condition-analysis/{analysis_id}` | Stored condition result |

New analysis returns 201. Duplicate requests return 409 unless `regenerate=true`; regeneration reuses the result ID, replaces its JSON snapshot and returns 200. Previous uploaded evidence remains retained. GET returns 200 or 404. Missing order/session returns 404; unlinked/wrong session ownership returns 409 without adopting the session. Invalid stored quantity is 400; invalid upload is 400/415, oversized upload 413, malformed form 422. Unexpected errors have a generic 500 response without paths or traces.

Reports add nullable `quantity_verification`, nullable `condition_analysis` and `channel_statuses`. No row means `not_run`; an existing unusable saved payload means `missing_analysis`. Unsupported, unavailable, insufficient and reviewed statuses remain distinct. Missing results never become successful results. Legacy report retrieval preserves its saved decision and fills new optional fields with defaults. Regeneration reads current results for the exact session and order and reuses the report ID. HTML renders the persisted snapshot with automatic escaping. Report generation and retrieval remove raw OCR fragments and redact nested recipient/phone comparisons as well as top-level customer details; source sessions remain unchanged.

Decision precedence: barcode/label/product/quantity contradiction -> `mismatch`; strong visible damage -> new additive `issue_detected`; likely damage or unreliable executed evidence -> `manual_review_required`; verified barcode, verified label, matching product, matching quantity and no visible damage -> `verified`. Missing optional analyses or unsupported counting can at most yield `likely_verified` with explicit incomplete-channel warnings. Older stored `verified` reports remain readable; regenerating them uses these conservative current rules. Explanations list only recorded channel results; missing channels are identified in warnings and the status table.

## Swagger manual retest

Run from the repository root after configuring/cache-enabling models as desired:

```powershell
backend\venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --reload-dir backend/app
```

Open `http://127.0.0.1:8000/docs`.

1. Use `GET /api/orders/OD290920260550001234` for the existing trimmer demo. For supported counting, use `POST /api/orders` to create a **new** test order with product name `cup` and expected quantity `2`; do not overwrite an existing customer order.
2. Use `POST /api/orders/{order_id}/references` to add a clear intact reference for that order. Keep reference metadata consistent with the stored order.
3. Use `POST /api/analyze` with a QR/label photo or prerecorded video and matching `expected_order_id`. Copy `order_verification.verification_session_id`; the session must already belong to the order. Existing OCR may need its own local EasyOCR weights.
4. Use `POST /api/product-matching/analyze` with the same order/session and a clear delivered-product image/video.
5. Use `POST /api/quantity-verification/analyze` with the same order/session and all items visible together. Do not supply an expected count: the stored count is authoritative.
6. Use `POST /api/condition-analysis/analyze` with the same order/session and the correct `evidence_type`. Use several clear views for video; avoid glare and obstruction.
7. Use `POST /api/reports/generate/{session_id}`. Read both new nested results, warnings, channel statuses and the decision.
8. Open `GET /api/reports/{report_id}` and `GET /api/reports/{report_id}/html`.
9. To replace an analysis, repeat its POST with `regenerate=true`, then regenerate the report. GET analysis returns the current result; existing report snapshots change only when explicitly regenerated.

Expected examples (real predictions are not guaranteed):

| Evidence | Expected conservative behavior |
| --- | --- |
| Two clearly separated supported cups, expected 2, intact views | Reliable count `match`; adequate normal condition evidence may yield `no_visible_damage`. Full report verification also requires barcode/label/product success. |
| One or three reliable visible cups, expected 2 | Quantity `mismatch`; report `mismatch`. Occluded/uncertain counts require review instead. |
| Damaged product, comparable reference and agreeing clear video views | Supporting evidence may yield `damaged` / report `issue_detected`; unsupported cues require review. |
| Torn package or suspected disturbed seal | Positive prompt evidence without packaging-specific structural support requires manual review. |
| Existing VGR trimmer | Quantity `unsupported_product`, detected quantity null, never assumed to be 1. |
| Blurry/dark/incomplete image | `insufficient_evidence`, count/damage flag unknown; no successful channel inferred. |
| Models disabled or weights missing | `model_unavailable` after usable evidence reaches inference; report flags incomplete evidence. |

## Automated verification

Normal tests mock OCR, detector and CLIP inference; no network or large weight downloads are needed. Before app import, test configuration routes settings to repository-local temporary directories and an in-memory database. The seeder and service session factories are patched too. The old product-matching test now uses the seeded fixture and `tmp_path`, not development storage. The CLI label test uses temporary synthetic evidence and deterministic OCR detections while retaining its eight output assertions. External sockets are blocked during normal tests; Windows asyncio loopback sockets remain allowed.

```powershell
backend\venv\Scripts\python.exe -m pytest backend\tests\test_quantity_verification.py -v
backend\venv\Scripts\python.exe -m pytest backend\tests\test_condition_analysis.py -v
backend\venv\Scripts\python.exe -m pytest backend\tests\test_reports.py -v
backend\venv\Scripts\python.exe -m pytest backend\tests\test_product_matching.py -v
backend\venv\Scripts\python.exe -m pytest backend\tests\test_analysis_api.py -v
backend\venv\Scripts\python.exe -m pytest backend\tests -v
backend\venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); import app.main; print('Import successful')"
git diff --check
```

Two real-adapter smoke cases are skipped unless explicitly enabled with already cached weights:

```powershell
$env:RUN_MODEL_INTEGRATION = '1'
$env:MODEL_SMOKE_CACHE = "$PWD\backend\storage\models"
backend\venv\Scripts\python.exe -m pytest backend\tests\test_model_adapters.py -v
```

These checks validate adapter execution, not model accuracy on delivery evidence. Human review and a representative labeled evaluation set remain necessary before relying on automated condition or quantity outcomes.
