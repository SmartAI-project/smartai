from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("godeyes_event_gates_test", ROOT / "app" / "event_gates.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
event_dedupe_key = MODULE.event_dedupe_key


def test_ordinary_talking_has_normal_dedupe_key():
    assert event_dedupe_key("OB_TALKING", "Mouth motion repeated.") == "OB_TALKING"


def test_talking_while_turned_uses_independent_dedupe_key():
    assert event_dedupe_key("OB_TALKING", "Possible talking while turned; teacher confirmation needed.") == "OB_TALKING_WHILE_TURNED"


def test_other_behavior_types_keep_original_dedupe_key():
    assert event_dedupe_key("HEAD_TURN_LEFT", "Possible distraction.") == "HEAD_TURN_LEFT"