# Changelog

## v0.15.0 — Body tracking fallback and safer identity continuity

- Add OpenCV Zoo YOLOX INT8 ONNX detection fallback on CPU when Ultralytics/Torch is not installed, detecting people even when their faces are not visible.
- Add fixed SHA-256 verification for the YOLOX model download and include its third-party license notice.
- Keep lost tracks for a bounded six-second grace period and reset the timer when the person returns.
- Separate current-frame facial evidence from retained identity embeddings so stale data cannot count as a fresh Face ID observation.
- Prevent a contradictory/unverified face or appearance-only change from rewriting the stored identity appearance anchor.
- Add synthetic tests for YOLOX output decoding, letterbox-padding rejection, wrong output shape, identity continuity and model-checksum configuration.
- Add a Windows x64 CI/build/release workflow.

## v0.14.0 — Temporal cue and false-alert guard update

- Require sustained, measured yaw and matching direction before head-turn alerts.
- Add temporal mouth-motion gate for possible talking while facing forward or while the head is turned.
- Add a generic-template pitch estimate with reprojection/landmark quality gates.
- Require sustained pitch-down, stable face visibility, sufficient samples, and low body/hand/task motion for drowsiness alerts.
- Add repeated-nod cue; all behavior alerts remain “possible” cues requiring teacher review.
- Keep unknown-identity cues local-only; add anti-duplicate cooldowns and reset handling.
- Add regression tests and checksum-verified model setup for Windows builds.