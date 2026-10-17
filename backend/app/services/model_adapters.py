"""Lazy local-weight adapters. Importing this module never imports Torch or downloads weights."""
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
from threading import RLock
from urllib.request import urlopen
from uuid import uuid4

from app.config import settings
from app.schemas.evidence import ModelMetadata


class ModelUnavailable(RuntimeError):
    """Optional package, trusted local weights or device could not be loaded."""


_MODEL_LOCK = RLock()


def model_metadata(kind: str) -> ModelMetadata:
    quantity = kind == "quantity"
    package = "ultralytics" if quantity else "open_clip_torch"
    try:
        package_version = version(package)
    except PackageNotFoundError:
        package_version = "not installed"
    return ModelMetadata(
        repository="https://github.com/ultralytics/ultralytics" if quantity else "https://github.com/mlfoundations/open_clip",
        package_version=package_version,
        model=Path(settings.quantity_checkpoint).stem if quantity else settings.condition_model_name,
        checkpoint=Path(settings.quantity_checkpoint).name if quantity else settings.condition_pretrained,
        license="AGPL-3.0" if quantity else "MIT",
        device=settings.model_device,
    )


def resolve_device() -> str:
    import torch
    if settings.model_device == "cuda" and not torch.cuda.is_available():
        raise ModelUnavailable("Configured CUDA device is unavailable.")
    return "cuda" if settings.model_device != "cpu" and torch.cuda.is_available() else "cpu"


def _local_checkpoint(name: str) -> Path:
    cache = settings.model_cache_dir.resolve()
    path = (cache / name).resolve()
    if not path.is_relative_to(cache) or path == cache:
        raise ModelUnavailable("Checkpoint must be inside the configured model cache.")
    return path


def _configure_cache() -> None:
    cache = settings.model_cache_dir.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    for directory in ("ultralytics", "huggingface", "torch", "matplotlib"):
        (cache / directory).mkdir(parents=True, exist_ok=True)
    # Prevent third-party settings and cache writes to a user's home directory.
    os.environ["YOLO_CONFIG_DIR"] = str(cache / "ultralytics")
    os.environ["YOLO_AUTOINSTALL"] = "false"
    # All downloads go through the explicit controlled downloader above.
    # Disable third-party telemetry/connectivity probes and implicit Hub fetches.
    os.environ["YOLO_OFFLINE"] = "true"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HOME"] = str(cache / "huggingface")
    os.environ["TORCH_HOME"] = str(cache / "torch")
    os.environ["MPLCONFIGDIR"] = str(cache / "matplotlib")


def _ensure_weights(path: Path, kind: str) -> None:
    if path.is_file():
        return
    if settings.model_offline:
        raise ModelUnavailable(f"{kind.capitalize()} weights are not cached locally.")
    # Only the documented, official checkpoints may be downloaded automatically.
    sources = {
        "quantity": ("yolov8n.pt", "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"),
        "condition": ("open_clip_pytorch_model.bin", "https://huggingface.co/laion/CLIP-ViT-B-32-laion2B-s34B-b79K/resolve/main/open_clip_pytorch_model.bin"),
    }
    name, url = sources[kind]
    if path.name != name or (kind == "condition" and (settings.condition_model_name != "ViT-B-32" or settings.condition_pretrained != "laion2b_s34b_b79k")):
        raise ModelUnavailable("Custom checkpoints must be explicitly pre-cached.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".part")
    try:
        with urlopen(url, timeout=30) as source, temporary.open("xb") as destination:
            total = 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > 800 * 1024 * 1024:
                    raise ModelUnavailable("Checkpoint download exceeded the expected size limit.")
                destination.write(chunk)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@lru_cache(maxsize=2)
def _load_detector(path: str, device: str):
    from ultralytics import YOLO
    return YOLO(path, task="detect")


def detect_objects(image, target_class: str) -> list[dict]:
    if not settings.enable_quantity_model:
        raise ModelUnavailable("Quantity model is disabled.")
    try:
        with _MODEL_LOCK:
            path = _local_checkpoint(settings.quantity_checkpoint)
            _ensure_weights(path, "quantity")
            _configure_cache()
            device = resolve_device()
            model = _load_detector(str(path), device)
            class_ids = [key for key, value in model.names.items() if value == target_class]
            if not class_ids:
                raise ModelUnavailable("Configured target class is absent from the checkpoint.")
            prediction = model.predict(
                image, device=device, classes=class_ids, conf=0.1,
                imgsz=640, max_det=100, verbose=False, save=False,
            )[0]
            return [
                {"box": [round(float(v), 2) for v in box.xyxy[0].tolist()],
                 "class_name": model.names[int(box.cls.item())],
                 "confidence": round(float(box.conf.item()), 4)}
                for box in prediction.boxes
            ]
    except ModelUnavailable:
        raise
    except Exception as exc:
        raise ModelUnavailable("Quantity model could not run; check its package and local weights.") from exc


def condition_prompts(evidence_type: str) -> tuple[list[str], list[str]]:
    if evidence_type in ("sealed_package", "opened_package"):
        positives = ["a torn delivery package", "a punctured shipping carton", "a severely crushed delivery box"]
        negatives = ["normal packaging without visible tears", "an intact delivery box", "a carton with normal printed markings and shadows"]
        if evidence_type == "sealed_package":
            positives.append("a visibly broken package seal")
            negatives.append("an undamaged sealed package")
        else:
            negatives.append("a normally opened intact package")
    else:
        positives = ["a clearly damaged product", "a broken product with missing parts", "a cracked or severely deformed product"]
        negatives = ["an intact undamaged product", "a normal new product", "a product photographed from a different angle", "an intact product with reflections and shadows"]
    return positives, negatives


@lru_cache(maxsize=2)
def _load_clip(model_name: str, checkpoint: str, device: str):
    import open_clip
    model, _, transform = open_clip.create_model_and_transforms(model_name, pretrained=checkpoint, device=device)
    model.eval()
    return model, transform, open_clip.get_tokenizer(model_name)


def classify_condition(image, evidence_type: str) -> tuple[float, float]:
    if not settings.enable_condition_model:
        raise ModelUnavailable("Condition model is disabled.")
    try:
        with _MODEL_LOCK:
            path = _local_checkpoint(settings.condition_checkpoint)
            _ensure_weights(path, "condition")
            _configure_cache()
            import torch
            device = resolve_device()
            model, transform, tokenizer = _load_clip(settings.condition_model_name, str(path), device)
            positive, negative = condition_prompts(evidence_type)
            with torch.inference_mode():
                text = model.encode_text(tokenizer(positive + negative).to(device))
                text = text / text.norm(dim=-1, keepdim=True)
                # Average normalized prompt embeddings per ensemble, not a
                # softmax over unequal numbers of individual prompt labels.
                groups = torch.stack((text[:len(positive)].mean(0), text[len(positive):].mean(0)))
                groups = groups / groups.norm(dim=-1, keepdim=True)
                visual = model.encode_image(transform(image).unsqueeze(0).to(device))
                visual = visual / visual.norm(dim=-1, keepdim=True)
                scores = (100.0 * visual @ groups.T).softmax(dim=-1)[0]
            return round(float(scores[0].item()), 4), round(float(scores[1].item()), 4)
    except ModelUnavailable:
        raise
    except Exception as exc:
        raise ModelUnavailable("Condition model could not run; check its package and local weights.") from exc
