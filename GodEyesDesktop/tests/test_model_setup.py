from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "download_models.py"
spec = importlib.util.spec_from_file_location("godeyes_model_setup_test", SCRIPT)
assert spec and spec.loader
setup = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = setup
spec.loader.exec_module(setup)


class _FakeResponse:
    def __init__(self, body: bytes):
        self.body = body
        self.offset = 0
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, size=-1):
        if self.offset >= len(self.body):
            return b""
        if size < 0:
            size = len(self.body) - self.offset
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_model_sha_helper_matches_known_bytes(tmp_path):
    path = tmp_path / "model.bin"
    path.write_bytes(b"test model data")
    assert setup.sha256_file(path) == hashlib.sha256(b"test model data").hexdigest()


def test_download_model_checks_hash_and_atomically_writes(tmp_path, monkeypatch):
    body = b"synthetic-test-model" * 10
    model = {
        "filename": "test.onnx",
        "url": "https://example.invalid/test.onnx",
        "sha256": hashlib.sha256(body).hexdigest(),
        "minimum_bytes": 10,
    }
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *args, **kwargs: _FakeResponse(body))
    setup.download_one(model, tmp_path, timeout=1.0)
    assert (tmp_path / "test.onnx").read_bytes() == body
    assert not list(tmp_path.glob("*.part"))


def test_download_model_rejects_checksum_mismatch_and_cleans_temp(tmp_path, monkeypatch):
    body = b"synthetic-test-model" * 10
    model = {
        "filename": "test.onnx",
        "url": "https://example.invalid/test.onnx",
        "sha256": "0" * 64,
        "minimum_bytes": 10,
    }
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *args, **kwargs: _FakeResponse(body))
    try:
        setup.download_one(model, tmp_path, timeout=1.0)
    except RuntimeError as exc:
        assert "SHA-256 mismatch" in str(exc)
    else:
        raise AssertionError("checksum mismatch should have been rejected")
    assert not (tmp_path / "test.onnx").exists()
    assert not list(tmp_path.glob("*.part"))


def test_official_yolox_fallback_is_pinned_and_checksum_verified():
    model = next(x for x in setup.MODELS if x["filename"] == "object_detection_yolox_2022nov_int8.onnx")
    assert model["url"].startswith("https://huggingface.co/opencv/opencv_zoo/")
    assert len(model["sha256"]) == 64
    assert model["minimum_bytes"] >= 8_000_000