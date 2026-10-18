#!/usr/bin/env python3
"""Pre-build smoke test for the checksum-pinned OpenCV Zoo ONNX models."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

from download_models import MODELS, sha256_file


def verify_files(model_dir: Path) -> None:
    for model in MODELS:
        path = model_dir / model["filename"]
        if not path.is_file():
            raise RuntimeError(f"Required model is missing: {path}")
        if path.stat().st_size < model["minimum_bytes"]:
            raise RuntimeError(f"Model is unexpectedly small: {path.name}")
        actual = sha256_file(path)
        if actual.lower() != model["sha256"].lower():
            raise RuntimeError(f"Model checksum mismatch for {path.name}: {actual}")
        print(f"SHA-256 OK: {path.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=Path("app/assets/models"))
    args = parser.parse_args()
    model_dir = args.model_dir.resolve()
    try:
        verify_files(model_dir)

        yunet_path = model_dir / "face_detection_yunet_2023mar.onnx"
        sface_path = model_dir / "face_recognition_sface_2021dec.onnx"
        yolox_path = model_dir / "object_detection_yolox_2022nov_int8.onnx"
        if not hasattr(cv2, "FaceDetectorYN") or not hasattr(cv2, "FaceRecognizerSF"):
            raise RuntimeError("Installed OpenCV build lacks FaceDetectorYN/FaceRecognizerSF APIs")

        face_detector = cv2.FaceDetectorYN.create(str(yunet_path), "", (320, 320), 0.65, 0.30, 5000)
        face_recognizer = cv2.FaceRecognizerSF.create(str(sface_path), "")
        if face_detector is None or face_recognizer is None:
            raise RuntimeError("OpenCV could not initialize the YuNet/SFace models")
        face_detector.setInputSize((320, 240))
        _count, faces = face_detector.detect(np.zeros((240, 320, 3), dtype=np.uint8))
        if faces is not None and not np.isfinite(faces).all():
            raise RuntimeError("YuNet returned non-finite outputs on the synthetic smoke-test frame")
        print("YuNet + SFace load and empty-frame smoke test: OK")

        net = cv2.dnn.readNet(str(yolox_path))
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        # Match OpenCV Zoo's YOLOX preprocessing: RGB values, top-left letterbox,
        # 114 padding, float32 NCHW without ImageNet normalization.
        padded = np.full((640, 640, 3), 114, dtype=np.uint8)
        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).astype(np.float32)
        blob = np.transpose(rgb, (2, 0, 1))[None, ...]
        net.setInput(blob)
        outputs = net.forward(net.getUnconnectedOutLayersNames())
        if isinstance(outputs, (list, tuple)):
            if not outputs:
                raise RuntimeError("YOLOX returned no output tensors")
            outputs = outputs[0]
        raw = np.asarray(outputs)
        squeezed = np.squeeze(raw)
        valid_shapes = {(8400, 85), (85, 8400)}
        if squeezed.shape not in valid_shapes:
            raise RuntimeError(f"YOLOX output shape is incompatible: {raw.shape}")
        if not np.isfinite(squeezed).all():
            raise RuntimeError("YOLOX returned NaN/Inf outputs on the synthetic smoke-test frame")
        print(f"YOLOX OpenCV-DNN CPU forward smoke test: OK ({raw.shape})")
        print(f"Model preflight passed with OpenCV {cv2.__version__} / NumPy {np.__version__}")
        return 0
    except Exception as exc:
        print(f"MODEL SMOKE TEST FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())