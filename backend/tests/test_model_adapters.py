"""Offline adapter contracts and explicit opt-in smoke tests using trusted local weights."""
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from app.config import settings
from app.services import model_adapters as adapters


@pytest.mark.parametrize("kind", ["quantity", "condition"])
def test_missing_weights_never_download(monkeypatch, tmp_path, kind):
    monkeypatch.setattr(settings, "model_cache_dir", tmp_path)
    monkeypatch.setattr(settings, "enable_quantity_model", True)
    monkeypatch.setattr(settings, "enable_condition_model", True)
    monkeypatch.setattr(adapters, "urlopen", lambda *args, **kwargs: pytest.fail("offline model download"))
    invoke = adapters.detect_objects if kind == "quantity" else adapters.classify_condition
    with pytest.raises(adapters.ModelUnavailable, match="not cached"):
        invoke(Image.new("RGB", (224, 224)), "cup" if kind == "quantity" else "product")


def test_detector_adapter_serializes_native_scalars(monkeypatch, tmp_path):
    import torch
    monkeypatch.setattr(settings, "enable_quantity_model", True)
    monkeypatch.setattr(settings, "model_cache_dir", tmp_path)
    (tmp_path / settings.quantity_checkpoint).write_bytes(b"mock; never loaded")
    box = SimpleNamespace(xyxy=torch.tensor([[10., 10., 100., 150.]]), cls=torch.tensor([0]), conf=torch.tensor([.9]))
    class Detector:
        names = {0: "cup"}
        def predict(self, image, **kwargs):
            assert kwargs["save"] is False
            assert kwargs["device"] == "cpu"
            return [SimpleNamespace(boxes=[box])]
    monkeypatch.setattr(adapters, "_load_detector", lambda *args: Detector())
    monkeypatch.setattr(adapters, "_configure_cache", lambda: None)
    result = adapters.detect_objects(Image.new("RGB", (224, 224)), "cup")
    assert type(result[0]["confidence"]) is float
    assert result[0]["box"] == [10., 10., 100., 150.]


def test_checkpoint_path_cannot_escape_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "model_cache_dir", tmp_path)
    with pytest.raises(adapters.ModelUnavailable):
        adapters._local_checkpoint("../outside.pt")


def test_cache_directories_exist_before_package_import(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "model_cache_dir", tmp_path)
    for name in ("YOLO_CONFIG_DIR", "YOLO_AUTOINSTALL", "YOLO_OFFLINE", "HF_HUB_OFFLINE",
                 "HF_HOME", "TORCH_HOME", "MPLCONFIGDIR"):
        monkeypatch.setenv(name, "test")
    adapters._configure_cache()
    for name in ("YOLO_CONFIG_DIR", "HF_HOME", "TORCH_HOME", "MPLCONFIGDIR"):
        path = Path(os.environ[name])
        assert path.is_dir() and path.is_relative_to(tmp_path)
    assert os.environ["YOLO_OFFLINE"] == "true"


@pytest.mark.skipif(os.environ.get("RUN_MODEL_INTEGRATION") != "1", reason="Set RUN_MODEL_INTEGRATION=1 and MODEL_SMOKE_CACHE for local-weight adapter smoke tests")
@pytest.mark.parametrize("kind", ["quantity", "condition"])
def test_real_local_model_adapter(monkeypatch, kind):
    cache = os.environ.get("MODEL_SMOKE_CACHE")
    assert cache, "MODEL_SMOKE_CACHE must point to trusted pre-downloaded weights"
    monkeypatch.setattr(settings, "model_cache_dir", Path(cache))
    monkeypatch.setattr(settings, "enable_quantity_model", True)
    monkeypatch.setattr(settings, "enable_condition_model", True)
    monkeypatch.setattr(settings, "model_device", "cpu")
    image = Image.new("RGB", (224, 224), "gray")
    if kind == "quantity":
        assert isinstance(adapters.detect_objects(image, "cup"), list)
    else:
        scores = adapters.classify_condition(image, "product")
        assert len(scores) == 2 and all(0 <= score <= 1 for score in scores)
