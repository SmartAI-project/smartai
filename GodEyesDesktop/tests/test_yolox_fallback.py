from __future__ import annotations
import importlib.util
import sys
import types
from pathlib import Path
import numpy as np


def _load_engine_module():
    if "PySide6" not in sys.modules:
        pyside = types.ModuleType("PySide6")
        qtcore = types.ModuleType("PySide6.QtCore")
        class DummySignal:
            def __init__(self, *args, **kwargs): self._callbacks = []
            def connect(self, callback): self._callbacks.append(callback)
            def emit(self, *args, **kwargs):
                for callback in tuple(self._callbacks): callback(*args, **kwargs)
        class DummyQThread:
            def __init__(self, *args, **kwargs): pass
            def isRunning(self): return False
            def wait(self, *args, **kwargs): return True
        qtcore.QThread = DummyQThread
        qtcore.Signal = DummySignal
        pyside.QtCore = qtcore
        sys.modules["PySide6"] = pyside
        sys.modules["PySide6.QtCore"] = qtcore
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("godeyes_yolox_test_engine", root / "app" / "engine.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


engine = _load_engine_module()


class FakeNet:
    def __init__(self, predictions):
        self.predictions = predictions
        self.blob = None
    def setInput(self, blob):
        self.blob = blob
    def getUnconnectedOutLayersNames(self):
        return ["output"]
    def forward(self, names):
        assert names == ["output"]
        return self.predictions


def _fake_vision(predictions):
    vision = engine.SmartVision.__new__(engine.SmartVision)
    vision.person_net = FakeNet(predictions)
    vision.person_net_error = ""
    return vision


def test_yolox_decoder_finds_person_when_no_face_is_needed():
    # Synthetic YOLOX head output at stride 8 / grid (40, 40): box centered at
    # (320, 320), 80x160 pixels, with high person objectness/class confidence.
    pred = np.zeros((1, 8400, 85), dtype=np.float32)
    index = 40 * 80 + 40
    pred[0, index, 0:4] = [0.0, 0.0, np.log(10.0), np.log(20.0)]
    pred[0, index, 4] = 0.95
    pred[0, index, 5] = 0.95
    vision = _fake_vision(pred)
    persons, objects = vision._yolox_detections(np.zeros((480, 640, 3), dtype=np.uint8), 1.0, 640, 480)
    assert len(persons) == 1
    box, score = persons[0]
    assert abs(box[0] - 280) <= 2
    assert abs(box[1] - 240) <= 2
    assert abs(box[2] - 80) <= 2
    assert abs(box[3] - 160) <= 2
    assert score > 0.85
    assert objects == []
    assert vision.person_net.blob.shape == (1, 3, 640, 640)


def test_yolox_decoder_ignores_high_score_prediction_in_letterbox_padding():
    pred = np.zeros((1, 8400, 85), dtype=np.float32)
    # Frame is 640x320, so only the top 320 rows of 640 contain image pixels.
    # This prediction's decoded center is y=480, entirely in letterbox padding.
    index = 60 * 80 + 40
    pred[0, index, 0:4] = [0.0, 0.0, np.log(10.0), np.log(20.0)]
    pred[0, index, 4] = 0.99
    pred[0, index, 5] = 0.99
    vision = _fake_vision(pred)
    persons, _ = vision._yolox_detections(np.zeros((320, 640, 3), dtype=np.uint8), 1.0, 640, 320)
    assert persons == []


def test_yolox_decoder_fails_closed_for_wrong_output_shape():
    vision = _fake_vision(np.zeros((1, 100, 12), dtype=np.float32))
    persons, objects = vision._yolox_detections(np.zeros((480, 640, 3), dtype=np.uint8), 1.0, 640, 480)
    assert persons == [] and objects == []
    assert "Unexpected YOLOX output shape" in vision.person_net_error