# Changelog

## v0.15.1 TEST — Compact overview and more responsive head-turn detection

- Tighten Overview card spacing, input/button heights and student tiles; prevent the Start Scan bar from stretching vertically.
- Increase default AI inference cadence from 6 FPS to 8 FPS.
- Lower the YuNet face confidence threshold from 0.65 to 0.55 and improve side-profile Haar detection for partially turned faces.
- Detect sustained left/right turns from about 28° after 1.6 seconds, using four consistent samples. Brief landmark jitter is tolerated, while temporal confirmation and per-event cooldown remain in place.
- Add regression tests for moderate-angle turns, short-turn rejection and one-frame pose jitter. Real-camera validation is still required for performance and false-positive assessment.

## v0.15.1 TEST — Supplied logo and compact sidebar

- Replace the temporary generated brand mark with the provided GodEyes logo image for the Desktop sidebar, attendance dialog, and window icon.
- Reduce the sidebar logo area to a more compact size.
- Generate a multi-resolution Windows `.ico` from that same supplied logo image and fail the build if the image/icon is missing instead of shipping a default icon.
- Add a static asset regression test and compile-check the icon generator in CI.
- Keep the two canonical Desktop entry points identical; preserve Unicode-safe font selection, elided labels, sustained turn confirmation, and evidence-frame quality gates from the current v0.15.1 source.

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