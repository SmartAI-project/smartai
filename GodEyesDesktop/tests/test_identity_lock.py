"""Regression tests for identity persistence across face loss and track changes."""
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
    spec = importlib.util.spec_from_file_location("godeyes_identity_test_engine", root / "app" / "engine.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


engine = _load_engine_module()


def _detection(*, box=(200, 100, 90, 160), face=(218, 110, 52, 52), face_feature=(1.0, 0.0, 0.0), appearance_feature=(0.2, 0.8, 0.4)):
    return engine.Detection(
        box=box, person_score=0.96, face=face,
        face_feature=None if face_feature is None else np.asarray(face_feature, dtype=np.float32),
        appearance_feature=None if appearance_feature is None else np.asarray(appearance_feature, dtype=np.float32),
        head_quality=0.9,
    )


def _locked_identity(with_second_student=False):
    roster = [{"id": 21, "ready": True, "student_code": "A001", "full_name": "Student A", "face_embedding": [1.0, 0.0, 0.0]}]
    if with_second_student:
        roster.append({"id": 22, "ready": True, "student_code": "B002", "full_name": "Student B", "face_embedding": [0.0, 1.0, 0.0]})
    track = engine.PersonTrack(1, _detection(), now=10.0)
    track.update(_detection(), now=10.3)
    identity = engine.IdentityLock()
    identity.set_server_roster(roster)
    mapping = identity.lock([track], frame_size=(640, 480))
    label = "A001 • Student A"
    assert mapping.get(1) == label
    return identity, track, label


def test_identity_lock_survives_temporary_face_loss_without_faking_a_fresh_match():
    identity, track, label = _locked_identity()
    last_verified = identity.student_profiles[label]["last_face_seen"]
    no_face = _detection(face=None, face_feature=None)
    track.update(no_face, now=11.0)

    # The last good embedding is retained as an identity anchor, but marked stale.
    assert track.face_feature is not None
    assert track.face_feature_current is False
    assert identity._v22_best_server_match(track)[0] is None
    identity.active_track_status = {1: {"visible": True, "confirmed": True, "missed": 0}}
    result, score = identity.resolve(track, frame_size=(640, 480))
    assert result == label
    assert score > 0.0
    assert identity.student_profiles[label]["last_face_seen"] == last_verified


def test_new_no_face_track_can_recover_same_person_when_appearance_and_geometry_match():
    identity, _old_track, label = _locked_identity()
    new_track = engine.PersonTrack(2, _detection(face=None, face_feature=None), now=12.0)
    identity.active_track_status = {
        1: {"visible": False, "confirmed": True, "missed": 31},
        2: {"visible": True, "confirmed": False, "missed": 0},
    }
    result, score = identity.resolve(new_track, frame_size=(640, 480))
    assert result == label
    assert score >= 0.8
    assert identity.track_to_student.get(2) == label
    assert 1 not in identity.track_to_student


def test_reidentification_does_not_steal_from_a_still_visible_track():
    identity, _old_track, label = _locked_identity()
    duplicate = engine.PersonTrack(2, _detection(face=None, face_feature=None), now=12.0)
    identity.active_track_status = {
        1: {"visible": True, "confirmed": True, "missed": 0},
        2: {"visible": True, "confirmed": False, "missed": 0},
    }
    result, _score = identity.resolve(duplicate, frame_size=(640, 480))
    assert result == "IDENTITY UNCERTAIN"
    assert identity.student_to_track.get(label) == 1
    assert identity.track_to_student.get(1) == label


def test_confidently_different_face_never_keeps_the_previous_student_name():
    identity, track, label = _locked_identity(with_second_student=True)
    conflicting = _detection(face=(218, 110, 52, 52), face_feature=(0.0, 1.0, 0.0))
    track.update(conflicting, now=11.0)
    identity.active_track_status = {1: {"visible": True, "confirmed": True, "missed": 0}}
    result, score = identity.resolve(track, frame_size=(640, 480))
    assert result == "IDENTITY UNCERTAIN"
    assert result != label
    assert score > 0.9


def test_distant_no_face_track_is_not_given_a_student_identity_by_clothing_alone():
    identity, _old_track, _label = _locked_identity()
    distant = engine.PersonTrack(
        2,
        _detection(box=(520, 300, 80, 150), face=None, face_feature=None, appearance_feature=(0.2, 0.8, 0.4)),
        now=12.0,
    )
    identity.active_track_status = {
        1: {"visible": False, "confirmed": True, "missed": 31},
        2: {"visible": True, "confirmed": False, "missed": 0},
    }
    result, _score = identity.resolve(distant, frame_size=(640, 480))
    assert result == "IDENTITY UNCERTAIN"


def test_disappeared_track_cannot_reuse_previous_frame_face_as_fresh_evidence():
    identity, track, label = _locked_identity()
    previous_face_feature = track.face_feature.copy()
    identity.active_track_status = {1: {"visible": True, "confirmed": True, "missed": 0}}
    track.miss(now=11.0)
    assert track.face is None
    assert track.face_feature_current is False
    assert track.face_feature is not None
    assert np.allclose(track.face_feature, previous_face_feature)
    assert identity._v22_best_server_match(track)[0] is None
    identity.active_track_status = {1: {"visible": False, "confirmed": True, "missed": 1}}
    result, _score = identity.resolve(track, frame_size=(640, 480))
    assert result == label
    assert identity.student_profiles[label]["identity_source"] == "TRACK_HOLD_NO_FACE_V22"


def test_initial_lock_never_uses_a_retained_embedding_without_a_current_face():
    roster = [{"id": 21, "ready": True, "student_code": "A001", "full_name": "Student A", "face_embedding": [1.0, 0.0, 0.0]}]
    # This is a defensive edge case: a track object might retain an old feature
    # after a face disappears. It must not seed a new identity lock.
    stale = engine.PersonTrack(5, _detection(face=None, face_feature=(1.0, 0.0, 0.0)), now=20.0)
    identity = engine.IdentityLock()
    identity.set_server_roster(roster)
    assert stale.face_feature is not None
    assert stale.face_feature_current is False
    assert identity.lock([stale], frame_size=(640, 480)) == {}




def test_new_face_id_waits_for_three_consistent_fresh_frames():
    roster = [{
        "id": 31, "ready": True, "student_code": "C031",
        "full_name": "Nguyễn Hữu Tĩnh", "face_embedding": [1.0, 0.0, 0.0],
    }]
    track = engine.PersonTrack(31, _detection(), now=100.0)
    track.update(_detection(), now=100.2)
    identity = engine.IdentityLock()
    identity.set_server_roster(roster)
    label = "C031 • Nguyễn Hữu Tĩnh"

    first, _ = identity.resolve(track, frame_size=(640, 480))
    assert first == "IDENTITY UNCERTAIN"

    track.update(_detection(), now=100.4)
    second, _ = identity.resolve(track, frame_size=(640, 480))
    assert second == "IDENTITY UNCERTAIN"

    track.update(_detection(), now=100.6)
    third, _ = identity.resolve(track, frame_size=(640, 480))
    assert third == label
    assert identity.track_to_student.get(track.track_id) == label


def test_inconsistent_face_match_resets_pending_identity_confirmation():
    roster = [{
        "id": 31, "ready": True, "student_code": "C031",
        "full_name": "Nguyễn Hữu Tĩnh", "face_embedding": [1.0, 0.0, 0.0],
    }]
    track = engine.PersonTrack(32, _detection(), now=200.0)
    track.update(_detection(), now=200.2)
    identity = engine.IdentityLock()
    identity.set_server_roster(roster)

    assert identity.resolve(track, frame_size=(640, 480))[0] == "IDENTITY UNCERTAIN"
    wrong = _detection(face_feature=(-1.0, 0.0, 0.0))
    track.update(wrong, now=200.4)
    assert identity.resolve(track, frame_size=(640, 480))[0] == "IDENTITY UNCERTAIN"

    track.update(_detection(), now=200.6)
    assert identity.resolve(track, frame_size=(640, 480))[0] == "IDENTITY UNCERTAIN"
    track.update(_detection(), now=200.8)
    assert identity.resolve(track, frame_size=(640, 480))[0] == "IDENTITY UNCERTAIN"
    track.update(_detection(), now=201.0)
    assert identity.resolve(track, frame_size=(640, 480))[0] == "C031 • Nguyễn Hữu Tĩnh"

def test_tracker_keeps_short_disappearances_but_expires_after_six_seconds():
    tracker = engine.PersonTracker()
    det = _detection()
    tracker.update([det], now=10.0)
    tracker.update([det], now=10.1)
    tracks = tracker.update([], now=10.2)
    assert len(tracks) == 1
    assert tracks[0].missed == 1
    tracks = tracker.update([], now=16.19)
    assert len(tracks) == 1
    tracks = tracker.update([], now=16.21)
    assert tracks == []


def test_tracker_resets_disappearance_timer_when_person_returns():
    tracker = engine.PersonTracker()
    det = _detection()
    tracker.update([det], now=30.0)
    tracker.update([det], now=30.1)
    tracker.update([], now=30.2)
    tracks = tracker.update([det], now=35.9)
    assert len(tracks) == 1
    assert tracks[0].track_id == 1
    assert tracks[0].missed == 0
    assert tracks[0].lost_since is None


def test_conflicting_face_cannot_rewrite_locked_students_appearance_anchor():
    identity, track, _label = _locked_identity(with_second_student=True)
    profile = identity.student_profiles["A001 • Student A"]
    original_appearance = profile["appearance_feature"].copy()
    conflicting = _detection(
        face=(218, 110, 52, 52),
        face_feature=(0.0, 1.0, 0.0),
        appearance_feature=(0.9, 0.1, 0.1),
    )
    track.update(conflicting, now=13.0)
    identity._refresh_profile("A001 • Student A", track, frame_size=(640, 480), identity_source="TEST")
    assert np.allclose(profile["appearance_feature"], original_appearance)