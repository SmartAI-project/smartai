$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
Set-Location $Root

# Respect the active PATH/virtual environment (especially setup-python in CI)
# instead of allowing the Windows `py` launcher to select a different Python.
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) { throw 'Python was not found on PATH. Activate the project virtual environment and retry.' }
$PythonVersion = (& $PythonExe -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
if ($LASTEXITCODE -ne 0) { throw 'Unable to start Python from PATH.' }
Write-Host "Using Python $PythonVersion at $PythonExe" -ForegroundColor Cyan

Write-Host ''
Write-Host '=== GodEyes Windows Build ===' -ForegroundColor Cyan
Write-Host 'This build bundles FFmpeg inside the GodEyes executable.' -ForegroundColor Green
Write-Host ''

$SourceMain = Join-Path $Root 'GodEyes_main.py'
$AppDir = Join-Path $Root 'app'
$TargetMain = Join-Path $AppDir 'main.py'

# Resolve the source file robustly. Windows may hide extensions, users may have
# extracted the kit into a subfolder, or may have renamed the source file.
if (-not (Test-Path $SourceMain)) {
    $CandidateNames = @(
        'GodEyes_main.py',
        'GodEyes_main.py.py',
        'GodEyes_main.py.txt',
        'GodEyes_main_CAMERA_AUDIO_dBFS_THREADSTOP.py',
        'GodEyes_main_PRESERVE_CORE_CAMERA_DBFS.py'
    )
    $Candidates = @()
    try {
        $Candidates = Get-ChildItem -LiteralPath $Root -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object {
                ($CandidateNames -contains $_.Name) -and
                ($_.FullName -notmatch '[\\/](app|build|dist|__pycache__|\.git)[\\/]')
            }
    } catch {}

    if ($Candidates.Count -gt 0) {
        $Picked = $Candidates | Sort-Object @{ Expression = { [array]::IndexOf($CandidateNames, $_.Name) }; Ascending = $true }, @{ Expression = 'LastWriteTime'; Descending = $true } | Select-Object -First 1
        Copy-Item -LiteralPath $Picked.FullName -Destination $SourceMain -Force
        Write-Host "Found source file in subfolder/alternate name and copied it to $SourceMain" -ForegroundColor Yellow
    }
}

if (-not (Test-Path $SourceMain)) {
    Write-Host ''
    Write-Host 'Updated source file is missing.' -ForegroundColor Red
    Write-Host "Expected: $SourceMain" -ForegroundColor Yellow
    Write-Host 'Extract the complete GodEyes Desktop source bundle into this project folder.' -ForegroundColor Yellow
    Write-Host 'After extraction, GodEyes_main.py should be beside run.py and the app folder.' -ForegroundColor Yellow
    throw 'Cannot locate GodEyes_main.py. Extract the complete GodEyes Desktop source bundle before building.'
}
if (-not (Test-Path (Join-Path $Root 'run.py'))) {
    throw "Cannot find run.py in $Root. Put the build kit in the existing GodEyes project root (for example D:\GodEyes)."
}
foreach ($Required in @('engine.py','db.py','server_client.py')) {
    if (-not (Test-Path (Join-Path $AppDir $Required))) {
        throw "Missing required project file: app\$Required"
    }
}

# The desktop entry points are intentionally kept byte-identical. Never
# overwrite either source at build time, which could silently discard changes.
$SourceHash = (Get-FileHash -LiteralPath $SourceMain -Algorithm SHA256).Hash
$TargetHash = (Get-FileHash -LiteralPath $TargetMain -Algorithm SHA256).Hash
if ($SourceHash -ne $TargetHash) {
    throw 'GodEyes_main.py and app\main.py differ. Synchronize the reviewed source files before building.'
}
Write-Host 'Desktop entry-point sources match (SHA-256 verified).' -ForegroundColor Green

# Install the packaging dependencies directly. Do not use `pip show` for
# presence checks: on some PowerShell versions, pip's expected "Package(s) not
# found" stderr is promoted to a terminating NativeCommandError.
Write-Host 'Installing/checking PyInstaller and imageio-ffmpeg...' -ForegroundColor Cyan
& $PythonExe -m pip install --upgrade pyinstaller imageio-ffmpeg
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to install PyInstaller or imageio-ffmpeg. Check your internet connection and Python/pip setup.'
}

# Confirm that imageio-ffmpeg supplies a Windows executable with RTSP demuxing support.
$FfmpegExe = (& $PythonExe -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or -not $FfmpegExe -or -not (Test-Path $FfmpegExe)) {
    throw 'imageio-ffmpeg is installed, but its FFmpeg executable could not be located.'
}
$Demuxers = (& $FfmpegExe -hide_banner -demuxers 2>&1 | Out-String)
if ($Demuxers -notmatch '(?m)^\s*D\s+rtsp\s+') {
    throw "The FFmpeg bundled with this Python environment does not show RTSP input support: $FfmpegExe"
}
Write-Host "FFmpeg RTSP demuxer verified: $FfmpegExe" -ForegroundColor Green

# Fetch all official ONNX models with fixed SHA-256 checks before packaging.
# The model files are intentionally not stored as large binary assets in Git.
$ModelSetup = Join-Path $Root 'scripts\download_models.py'
if (-not (Test-Path $ModelSetup)) {
    throw "Model setup script is missing: $ModelSetup. Refusing to build without verified face models."
}
Write-Host 'Downloading/verifying official YuNet, SFace, and YOLOX INT8 models...' -ForegroundColor Cyan
& $PythonExe $ModelSetup --model-dir (Join-Path $AppDir 'assets\models')
if ($LASTEXITCODE -ne 0) { throw 'Model download or checksum verification failed; build cancelled.' }
$ModelSmoke = Join-Path $Root 'scripts\smoke_test_models.py'
Write-Host 'Running OpenCV model-load and YOLOX CPU-forward smoke tests...' -ForegroundColor Cyan
& $PythonExe $ModelSmoke --model-dir (Join-Path $AppDir 'assets\models')
if ($LASTEXITCODE -ne 0) { throw 'Model smoke test failed; build cancelled before PyInstaller.' }

# Confirm the project's Python syntax before packaging.
Write-Host 'Checking Python syntax...' -ForegroundColor Cyan
& $PythonExe -m py_compile GodEyes_main.py app\main.py app\engine.py app\db.py app\server_client.py app\event_gates.py scripts\download_models.py
if ($LASTEXITCODE -ne 0) { throw 'Python syntax check failed; build cancelled.' }
Write-Host 'Python syntax OK.' -ForegroundColor Green

# The project environment must already contain runtime dependencies (PySide6, OpenCV, NumPy, etc.).
& $PythonExe -c "import PySide6, cv2, numpy, sounddevice, imageio_ffmpeg; assert hasattr(cv2, 'dnn'); print('Runtime imports OK')"
if ($LASTEXITCODE -ne 0) {
    throw 'Missing runtime dependencies. Activate the same Python environment used to run GodEyes, then install the project dependencies before building.'
}

if (Test-Path 'build') { Remove-Item 'build' -Recurse -Force }
if (Test-Path 'dist') { Remove-Item 'dist' -Recurse -Force }

$PyArgs = @(
    '-m','PyInstaller',
    '--noconfirm',
    '--clean',
    '--windowed',
    '--onefile',
    '--name','GodEyes',
    '--add-data','app\assets;app\assets',
    '--collect-all','imageio_ffmpeg',
    '--hidden-import','imageio_ffmpeg',
    '--hidden-import','sounddevice',
    '--exclude-module','ultralytics',
    '--exclude-module','torch',
    '--exclude-module','torchvision',
    '--exclude-module','torchaudio',
    '--exclude-module','triton',
    '--exclude-module','nvidia',
    '--exclude-module','nvidia_ml_py',
    '--exclude-module','matplotlib',
    '--exclude-module','pandas',
    '--exclude-module','scipy',
    '--exclude-module','sympy',
    '--exclude-module','networkx',
    '--exclude-module','IPython',
    '--exclude-module','jupyter',
    '--exclude-module','notebook',
    '--exclude-module','tensorboard',
    '--exclude-module','wandb',
    '--exclude-module','polars',
    '--exclude-module','pytest',
    '--exclude-module','unittest'
)
$Icon = Join-Path $AppDir 'assets\god_eyes.ico'
if (Test-Path $Icon) {
    $PyArgs += @('--icon', 'app\assets\god_eyes.ico')
} else {
    Write-Host 'Icon file not found; building with the default Windows icon.' -ForegroundColor Yellow
}
$PyArgs += 'run.py'

Write-Host ''
Write-Host 'Building a single-file executable; this can take several minutes...' -ForegroundColor Cyan
& $PythonExe @PyArgs
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed. Check the error log above.' }

$Exe = Join-Path $Root 'dist\GodEyes.exe'
if (-not (Test-Path $Exe)) { throw 'Build finished without producing dist\GodEyes.exe.' }
$SizeMB = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
Write-Host ''
Write-Host 'BUILD COMPLETE' -ForegroundColor Green
Write-Host "EXE: $Exe" -ForegroundColor Green
Write-Host "Size: $SizeMB MB" -ForegroundColor Green
Write-Host 'FFmpeg is packaged through imageio-ffmpeg; users do not need to install FFmpeg separately.' -ForegroundColor Green
Write-Host 'Body/person detection fallback uses the checksum-verified OpenCV Zoo YOLOX INT8 ONNX model on CPU.' -ForegroundColor Green
Write-Host 'Important: the Wi-Fi camera must have audio enabled and its RTSP stream must contain an audio track.' -ForegroundColor Yellow
Write-Host ''