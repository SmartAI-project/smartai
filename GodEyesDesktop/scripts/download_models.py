#!/usr/bin/env python3
"""Download official OpenCV Zoo detection/face models and verify SHA-256 before use."""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

MODELS = (
    {
        "filename": "face_detection_yunet_2023mar.onnx",
        "url": "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        "minimum_bytes": 100_000,
    },
    {
        "filename": "face_recognition_sface_2021dec.onnx",
        "url": "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        "minimum_bytes": 10_000_000,
    },
    {
        "filename": "object_detection_yolox_2022nov_int8.onnx",
        "url": "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/object_detection_yolox/object_detection_yolox_2022nov_int8.onnx",
        "sha256": "01a3b0f400b30bc1e45230e991b2e499ab42622485a330021947333fbaf03935",
        "minimum_bytes": 8_000_000,
    },
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_one(model: dict, destination: Path, timeout: float) -> None:
    target = destination / model["filename"]
    if target.is_file():
        size_ok = target.stat().st_size >= model["minimum_bytes"]
        if size_ok and sha256_file(target) == model["sha256"]:
            print(f"OK: {target.name} (checksum verified)")
            return
        print(f"Existing model failed verification; replacing: {target.name}")

    request = urllib.request.Request(
        model["url"],
        headers={"User-Agent": "GodEyes-Desktop-ModelSetup/0.15.0"},
    )
    temporary = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".part", dir=destination)
            temporary = Path(tmp_name)
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(fd, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 150_000_000:
                        raise RuntimeError(f"Refusing unexpectedly large model download: {target.name}")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        if size < model["minimum_bytes"]:
            raise RuntimeError(f"Downloaded model is too small: {target.name} ({size} bytes)")
        actual = digest.hexdigest()
        if actual != model["sha256"]:
            raise RuntimeError(f"SHA-256 mismatch for {target.name}: expected {model['sha256']}, got {actual}")
        temporary.replace(target)
        temporary = None
        print(f"DOWNLOADED: {target.name} ({size:,} bytes; SHA-256 verified)")
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
        raise RuntimeError(f"Could not securely fetch {target.name}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=Path("app/assets/models"))
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    directory = args.model_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        for model in MODELS:
            download_one(model, directory, args.timeout)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"All model files verified in: {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())