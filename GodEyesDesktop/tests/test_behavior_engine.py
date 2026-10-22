"""Synthetic temporal-regression tests for GodEyes' behavior cues.

These tests exercise the decision logic with deterministic track signals. They do
not claim to measure real-world model accuracy; representative camera footage and
hardware smoke testing are still required before a public release.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path


def _load_engine_module():
    # The behavior engine has no GUI dependency, but its module defines Qt workers.
    # Provide minimal import-time QtCore stubs to keep the decision tests headless.
    if "PySide6" not in sys.modules:
        pyside = types.ModuleType("PySide6")
        qtcore = types.ModuleType("PySide6.QtCore")

        class DummySignal:
            def __init__(self, *args, **kwargs):
                self._callbacks = []
            def connect(self, callback):
                self._callbacks.append(callback)
            def emit(self, *args, **kwargs):
                for callback in tuple(self._callbacks):
                    callback(*args, **kwargs)

        class DummyQThread:
            def __init__(self, *args, **kwargs):
                pass
            def isRunning(self):
                return False
            def wait(self, *args, **kwargs):
                return True

        qtcore.QThread = DummyQThread
        qtcore.Signal = DummySignal
        pyside.QtCore = qtcore
        sys.modules["PySide6"] = pyside
        sys.modules["PySide6.QtCore"] = qtcore

    root = Path(__file__).resolve().parents[1]
    module_path = root / "app" / "engine.py"
    spec = importlib.util.spec_from_file_location("godeyes_test_engine", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


engine = _load_engine_module()
BehaviorEngine = engine.BehaviorEngine


@dataclass
class Track:
    face: object = (10, 10, 50, 60)
    missed: int = 0
    head_yaw_deg: float = 0.0
    head_yaw_measured: bool = True
    head_pitch_deg: float = 0.0
    head_pitch_measured: bool = True
    head_quality: float = 0.90
    person_score: float = 0.95
    mouth_score: float = 0.05
    mouth_motion: float = 0.001
    mouth_activity: float = 0.05
    task_activity: float = 0.02
    lower_motion: float = 0.02
    hand_motion: float = 0.02
    face_y_ratio: float = 0.30


def _run(engine_obj, track, times):
    result = []
    for t in times:
        result.extend(engine_obj.evaluate("TRACK:7", track, now=t))
    return result


def test_short_head_turn_does_not_trigger():
    be = BehaviorEngine()
    tr = Track(head_yaw_deg=-48.0)
    events = _run(be, tr, [100.0, 100.25, 100.50, 100.75, 101.0, 101.25])
    assert not any(e[0] == "HEAD_TURN_LEFT" for e in events)


def test_sustained_measured_head_turn_triggers_once_and_cools_down():
    be = BehaviorEngine()
    tr = Track(head_yaw_deg=-48.0)
    events = _run(be, tr, [100.0 + i * 0.35 for i in range(13)])
    turns = [e for e in events if e[0] == "HEAD_TURN_LEFT"]
    assert len(turns) == 1
    assert turns[0][1] >= 0.72
    # Continued same-direction pose must not create a stream of duplicate alerts.
    later = _run(be, tr, [105.0 + i * 0.3 for i in range(15)])
    assert not any(e[0] == "HEAD_TURN_LEFT" for e in later)


def test_unmeasured_yaw_never_triggers_turn():
    be = BehaviorEngine()
    tr = Track(head_yaw_deg=55.0, head_yaw_measured=False)
    events = _run(be, tr, [200.0 + i * 0.4 for i in range(20)])
    assert not any(e[0] in {"HEAD_TURN_LEFT", "HEAD_TURN_RIGHT"} for e in events)


def test_profile_direction_is_preserved_when_yunet_yaw_collapses_to_zero():
    yaw, measured = engine._resolve_head_yaw("RIGHT", 45.0, 0.0)
    assert yaw >= 42.0
    assert measured is True

    yaw_left, measured_left = engine._resolve_head_yaw("LEFT", -45.0, 9.0)
    assert yaw_left <= -42.0
    assert measured_left is True


def test_profile_direction_without_yunet_landmarks_is_a_measured_cue():
    yaw, measured = engine._resolve_head_yaw("RIGHT", 45.0, None)
    assert yaw == 45.0
    assert measured is True

    # A sustained profile pose should pass the existing temporal filter once.
    be = BehaviorEngine()
    tr = Track(head_yaw_deg=yaw, head_yaw_measured=measured)
    events = _run(be, tr, [250.0 + i * 0.35 for i in range(13)])
    turns = [event for event in events if event[0] == "HEAD_TURN_RIGHT"]
    assert len(turns) == 1


def test_frontal_landmark_yaw_is_still_used_and_unknown_yaw_stays_unmeasured():
    yaw, measured = engine._resolve_head_yaw("FRONT", 0.0, 18.0)
    assert yaw == 18.0
    assert measured is True
    yaw_unknown, unknown_measured = engine._resolve_head_yaw("UNKNOWN", 0.0, None)
    assert yaw_unknown == 0.0
    assert unknown_measured is False




def test_repeated_mouth_motion_can_trigger_talking():
    be = BehaviorEngine()
    tr = Track()
    events = []
    for i in range(12):
        tr.mouth_score = 0.35 + (0.08 if i % 2 else 0.0)
        tr.mouth_motion = 0.020 + (0.026 if i % 2 else 0.0)
        tr.mouth_activity = 0.48 + (0.18 if i % 2 else 0.0)
        events.extend(be.evaluate("TRACK:7", tr, now=300.0 + i * 0.3))
    assert any(e[0] == "OB_TALKING" for e in events)


def test_talking_while_head_is_turned_can_trigger():
    be = BehaviorEngine()
    tr = Track(head_yaw_deg=46.0)
    events = []
    for i in range(12):
        tr.mouth_score = 0.34 + (0.10 if i % 2 else 0.0)
        tr.mouth_motion = 0.021 + (0.025 if i % 2 else 0.0)
        tr.mouth_activity = 0.49 + (0.17 if i % 2 else 0.0)
        events.extend(be.evaluate("TRACK:7", tr, now=400.0 + i * 0.35))
    turned_talk = [e for e in events if e[0] == "OB_TALKING" and "turned" in e[2].lower()]
    assert len(turned_talk) == 1


def test_head_down_with_active_hands_or_task_does_not_trigger_sleep():
    be = BehaviorEngine()
    # Establish an upright baseline with four visible samples.
    tr = Track(head_pitch_deg=0.0, face_y_ratio=0.30)
    _run(be, tr, [500.0 + i * 0.35 for i in range(5)])
    tr.head_pitch_deg = 22.0
    tr.face_y_ratio = 0.46
    tr.task_activity = 0.45
    tr.lower_motion = 0.30
    tr.hand_motion = 0.42
    events = _run(be, tr, [502.0 + i * 0.35 for i in range(40)])
    assert not any(e[0] == "OB_SLEEP" for e in events)


def test_sustained_measured_nod_with_low_motion_triggers_sleep_suspicion():
    be = BehaviorEngine()
    tr = Track(head_pitch_deg=0.0, face_y_ratio=0.30)
    _run(be, tr, [600.0 + i * 0.35 for i in range(5)])
    tr.head_pitch_deg = 23.0
    tr.face_y_ratio = 0.46
    events = _run(be, tr, [602.0 + i * 0.35 for i in range(36)])
    sleep_events = [e for e in events if e[0] == "OB_SLEEP"]
    assert len(sleep_events) == 1
    assert "teacher confirmation" in sleep_events[0][2].lower()


def test_face_loss_alone_never_triggers_sleep():
    be = BehaviorEngine()
    tr = Track(head_pitch_deg=0.0)
    _run(be, tr, [700.0 + i * 0.4 for i in range(5)])
    tr.face = None
    tr.missed = 1
    events = _run(be, tr, [702.0 + i * 0.35 for i in range(40)])
    assert not any(e[0] == "OB_SLEEP" for e in events)



def test_evidence_selection_prefers_sharpest_recent_frame_and_expires_old_frames():
    worker_cls = engine.AIWorker
    candidates = [
        {"stamp": 8.8, "quality": 400.0, "frame": "expired-sharp", "track": "old"},
        {"stamp": 9.7, "quality": 80.0, "frame": "recent-soft", "track": "soft"},
        {"stamp": 9.9, "quality": 140.0, "frame": "recent-sharp", "track": "sharp"},
    ]
    best = worker_cls._best_evidence_candidate(candidates, now=10.0, window_seconds=1.0)
    assert best is not None
    assert best["frame"] == "recent-sharp"


def test_evidence_quality_prefers_in_focus_face_region():
    import cv2
    import numpy as np

    class FaceTrack:
        face = (10, 10, 60, 60)
        visible = True
        missed = 0
        face_feature_current = True

    sharp = np.zeros((80, 80, 3), dtype=np.uint8)
    for x in range(10, 70, 6):
        cv2.line(sharp, (x, 10), (x, 69), (255, 255, 255), 2)
    blurred = cv2.GaussianBlur(sharp, (17, 17), 0)
    sharp_score = engine.AIWorker._evidence_frame_quality(sharp, FaceTrack())
    blur_score = engine.AIWorker._evidence_frame_quality(blurred, FaceTrack())
    assert sharp_score > blur_score


def test_ai_worker_rejects_missing_or_stale_camera_frames():
    is_fresh = engine.AIWorker._is_frame_fresh
    assert not is_fresh(0.0, now=10.0)
    assert is_fresh(9.0, now=10.0, max_age=2.5)
    assert not is_fresh(7.4, now=10.0, max_age=2.5)
    assert not is_fresh(11.0, now=10.0, max_age=2.5)

def test_reset_clears_track_temporal_state():
    be = BehaviorEngine()
    tr = Track(head_yaw_deg=-50.0)
    _run(be, tr, [800.0 + i * 0.35 for i in range(12)])
    be.reset()
    assert be.state == {}


def test_yunet_pitch_estimator_tracks_downward_pitch_with_positive_sign():
    import numpy as np
    import cv2

    width, height = 640, 480
    focal = float(max(width, height)) * 1.15
    camera = np.asarray([[focal, 0.0, width / 2], [0.0, focal, height / 2], [0.0, 0.0, 1.0]], dtype=np.float64)
    distortion = np.zeros((4, 1), dtype=np.float64)
    model = np.asarray([
        [0.0, 0.0, 0.0], [0.0, 330.0, -65.0],
        [-225.0, -170.0, -135.0], [225.0, -170.0, -135.0],
        [-150.0, 150.0, -125.0], [150.0, 150.0, -125.0],
    ], dtype=np.float64)

    def to_yunet_face(pitch_deg):
        rvec = np.deg2rad(np.asarray([pitch_deg, 0.0, 0.0], dtype=np.float64)).reshape(3, 1)
        tvec = np.asarray([[0.0], [0.0], [2500.0]], dtype=np.float64)
        projected, _ = cv2.projectPoints(model, rvec, tvec, camera, distortion)
        nose, chin, eye1, eye2, mouth1, mouth2 = projected.reshape(-1, 2)
        left = min(eye1[0], eye2[0], nose[0], mouth1[0], mouth2[0])
        right = max(eye1[0], eye2[0], nose[0], mouth1[0], mouth2[0])
        top = min(eye1[1], eye2[1]) - 5.0
        cx = (left + right) / 2.0
        fw = max((eye2[0] - eye1[0]) / 0.60, right - left + 10.0)
        fh = (chin[1] - top) / 0.96
        x = cx - fw / 2.0
        return [x, top, fw, fh, *eye1, *eye2, *nose, *mouth1, *mouth2, 0.99]

    upright = engine.SmartVision._estimate_pitch_from_yunet(to_yunet_face(0.0), (height, width))
    nodded = engine.SmartVision._estimate_pitch_from_yunet(to_yunet_face(20.0), (height, width))
    assert upright is not None and nodded is not None
    assert abs(upright) < 1.5
    assert nodded > upright + 10.0


def test_repeated_nods_require_measured_pitch_and_low_hand_motion():
    be = BehaviorEngine()
    tr = Track(head_pitch_deg=0.0, face_y_ratio=0.30)
    _run(be, tr, [900.0 + i * 0.35 for i in range(8)])
    all_events = []
    now = 903.0
    for _ in range(3):
        tr.head_pitch_deg = 24.0
        tr.face_y_ratio = 0.48
        all_events.extend(_run(be, tr, [now + i * 0.35 for i in range(6)]))
        now += 2.2
        tr.head_pitch_deg = 0.0
        tr.face_y_ratio = 0.30
        all_events.extend(_run(be, tr, [now + i * 0.35 for i in range(6)]))
        now += 2.2
    assert any(e[0] == "OB_SLEEP" and "repeated" in e[2].lower() for e in all_events)


def test_head_down_without_measured_pitch_is_not_sleep():
    be = BehaviorEngine()
    tr = Track(head_pitch_deg=0.0, head_pitch_measured=False, face_y_ratio=0.30)
    _run(be, tr, [1000.0 + i * 0.35 for i in range(6)])
    tr.face_y_ratio = 0.52
    events = _run(be, tr, [1002.2 + i * 0.35 for i in range(45)])
    assert not any(e[0] == "OB_SLEEP" for e in events)


def test_head_turn_direction_change_resets_confirmation_window():
    be = BehaviorEngine()
    tr = Track(head_yaw_deg=-48.0)
    first = _run(be, tr, [1100.0 + i * 0.35 for i in range(5)])
    tr.head_yaw_deg = 48.0
    second = _run(be, tr, [1101.75 + i * 0.35 for i in range(5)])
    all_events = first + second
    assert not any(e[0] in {"HEAD_TURN_LEFT", "HEAD_TURN_RIGHT"} for e in all_events)


def test_low_quality_face_does_not_trigger_sustained_turn():
    be = BehaviorEngine()
    tr = Track(head_yaw_deg=52.0, head_quality=0.10)
    events = _run(be, tr, [1200.0 + i * 0.35 for i in range(18)])
    assert not any(e[0] in {"HEAD_TURN_LEFT", "HEAD_TURN_RIGHT"} for e in events)


def test_static_mouth_signal_does_not_count_as_talking():
    be = BehaviorEngine()
    tr = Track(mouth_score=0.55, mouth_motion=0.018, mouth_activity=0.55)
    events = _run(be, tr, [1300.0 + i * 0.3 for i in range(20)])
    assert not any(e[0] == "OB_TALKING" for e in events)


def test_single_short_nod_is_not_sleep():
    be = BehaviorEngine()
    tr = Track(head_pitch_deg=0.0, face_y_ratio=0.30)
    _run(be, tr, [1400.0 + i * 0.3 for i in range(6)])
    tr.head_pitch_deg = 23.0
    tr.face_y_ratio = 0.46
    down = _run(be, tr, [1402.0 + i * 0.25 for i in range(8)])
    tr.head_pitch_deg = 0.0
    tr.face_y_ratio = 0.30
    up = _run(be, tr, [1404.0 + i * 0.3 for i in range(8)])
    assert not any(e[0] == "OB_SLEEP" for e in down + up)


def test_sleep_latched_alert_does_not_repeat_while_same_posture_continues():
    be = BehaviorEngine()
    tr = Track(head_pitch_deg=0.0, face_y_ratio=0.30)
    _run(be, tr, [1500.0 + i * 0.3 for i in range(6)])
    tr.head_pitch_deg = 23.0
    tr.face_y_ratio = 0.46
    events = _run(be, tr, [1502.0 + i * 0.35 for i in range(70)])
    assert len([e for e in events if e[0] == "OB_SLEEP"]) == 1


def test_measured_sleep_candidate_does_not_emit_duplicate_head_down_distraction():
    be = BehaviorEngine()
    tr = Track(head_pitch_deg=0.0, face_y_ratio=0.30)
    _run(be, tr, [1600.0 + i * 0.3 for i in range(6)])
    tr.head_pitch_deg = 23.0
    tr.face_y_ratio = 0.46
    events = _run(be, tr, [1602.0 + i * 0.35 for i in range(36)])
    assert any(e[0] == "OB_SLEEP" for e in events)
    assert not any(e[0] == "OB_DISTRACT" for e in events)