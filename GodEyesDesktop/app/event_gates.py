"""Small, testable helpers for behavior-event anti-spam keys."""


def event_dedupe_key(event_type: str, assessment: str = "") -> str:
    """Return an event-specific debounce key without changing Server schema.

    The Server event type stays OB_TALKING for compatibility, but a sustained
    talking cue during a measured head turn gets an independent local cooldown.
    """
    event = str(event_type or "OBSERVATION").strip().upper()
    details = str(assessment or "").casefold()
    if event == "OB_TALKING" and (
        "talking while turned" in details
        or "while measured head yaw stayed" in details
    ):
        return "OB_TALKING_WHILE_TURNED"
    return event