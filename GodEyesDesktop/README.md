# GodEyes Desktop — Classroom Vision and Identity Tracking v0.15.0

GodEyes is a Windows desktop classroom-monitoring application. This source snapshot improves the temporal gating around head turns, mouth movement, head-down posture, and repeated nods while retaining the existing camera, Face ID, audio, and session flows.

## What's new in v0.15.0

- Detects sustained measured left/right head turns rather than alerting on a single frame.
- Looks for repeated mouth-motion cues during a head turn and labels them as **possible talking while turned**.
- Adds a relative head-pitch estimate from YuNet facial landmarks. Sleep/drowsiness cues need sustained measured pitch-down, stable visible tracking, and low hand/task motion; repeated measured nods can also produce a cue.
- Uses per-track temporal confirmation and cooldowns to reduce duplicate/short-lived alerts.
- Unknown-identity events remain local-only; they are not written to a named student's record or synced to the Server.
- Adds the official OpenCV Zoo YOLOX INT8 body/object detector as a CPU-only fallback when the optional Ultralytics model is unavailable, enabling tracking when the face is turned away or temporarily occluded.
- Keeps the previously locked identity through a short track loss (bounded to 6 seconds); a fresh face embedding is required for a new Face ID match, and appearance re-identification is conservative rather than being treated as Face ID.
- Prevents a conflicting or unverified frame from updating a student's stored appearance anchor.
- Adds checksum-verified model downloads, regression tests, and a Windows x64 GitHub Actions build/release workflow.

## Important limitations

No vision system can guarantee 100% detection, zero false positives, or zero operational risk. Camera angle, lighting, glasses, occlusion, low resolution, face size, seating layout, device performance, and a student's normal writing/reading posture affect results. The yaw and pitch calculations use generic face geometry and should be treated as estimates. “Talking”, “distracted”, and “sleep” are **possible visual cues**, not proof or a medical conclusion; a teacher must review context before acting. A head turn alone does not prove talking.

Do not use these alerts as the sole basis for discipline or consequential decisions. Validate the app in the actual classroom and camera position before routine use. Unknown identities are never attributed to a named student by this cue layer.

## Requirements

- Windows 10/11 x64.
- Python 3.11 x64 recommended for a reproducible local build.
- A compatible webcam or supported RTSP camera. RTSP audio monitoring requires the camera stream to include an audio track.
- Network access on first build for Python packages and the official OpenCV Zoo model files. The model downloader verifies fixed SHA-256 hashes before packaging.

## Build the Windows executable

Open PowerShell in this folder and run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m pytest -q
powershell -ExecutionPolicy Bypass -File .\build_godeyes.ps1
```

The script downloads and verifies YuNet, SFace, and the OpenCV Zoo YOLOX INT8 ONNX detector, runs a model-load/CPU-forward smoke test, checks Python syntax, verifies the FFmpeg RTSP demuxer supplied by `imageio-ffmpeg`, then creates `dist\GodEyes.exe`. The ONNX models are fetched from the official OpenCV Zoo project at build time instead of being duplicated as large binary files in this repository.

## GitHub CI and public Windows release

- A push or pull request runs syntax checks and the deterministic regression suite on Linux.
- To build and publish a Windows x64 release, open **Actions → GodEyes Desktop CI and Windows Release → Run workflow**, choose the reviewed source branch, and provide a new version such as `v0.15.0`.
- The workflow builds `GodEyes.exe` on a Windows runner only after the tests pass, calculates SHA-256, stores the build artifact, and publishes the executable plus checksum as a GitHub Release.
- Do not reuse an existing release tag for a different binary. Keep published assets and checksums matched to the same build.

## Run from source

```powershell
py -m pip install -r requirements.txt
py scripts\download_models.py --model-dir app\assets\models
py run.py
```

## Tests

```powershell
py -m pytest -q
py -m py_compile .\GodEyes_main.py .\app\main.py .\app\engine.py .\app\db.py .\app\server_client.py .\app\event_gates.py .\scripts\download_models.py .\scripts\smoke_test_models.py
```

The tests are deterministic regression tests for temporal rules and a synthetic pose projection. They do **not** replace field validation against representative classroom video or Windows camera/hardware smoke testing.

## Privacy / safety

- Alerts are phrased as suspicion/cues and should be reviewed by a teacher.
- Unidentified-person behavior cues are displayed locally and are not persisted or uploaded as student events. Named-student events still use the existing selected-roster, recent-face, and evidence-sync gates.
- Do not publicly upload student photographs, session evidence, device credentials, server tokens, or `app/server_config.json`. The repository ignore rules exclude configuration secrets and local evidence/build outputs.

## Third-party model licenses

- YuNet: MIT license, see `THIRD_PARTY_LICENSE_YUNET.txt`.
- SFace: Apache License 2.0, see `THIRD_PARTY_LICENSE_SFACE.txt`.
- YOLOX INT8: Apache License 2.0, see `THIRD_PARTY_LICENSE_YOLOX.txt`.
- OpenCV, PySide6, NumPy, sounddevice, imageio-ffmpeg, and PyInstaller remain subject to their respective upstream licenses.