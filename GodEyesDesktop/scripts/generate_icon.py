from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


DEFAULT_LOGO = Path(__file__).resolve().parents[1] / "app" / "assets" / "god_eyes_logo.png"


def build_icon(logo_path: str | Path | None = None, size: int = 256) -> Image.Image:
    """Use the supplied GodEyes logo image rather than drawing a substitute mark."""
    if size < 16:
        raise ValueError("Icon size must be at least 16 pixels.")
    path = Path(logo_path) if logo_path is not None else DEFAULT_LOGO
    if not path.is_file():
        raise FileNotFoundError(f"GodEyes logo image is missing: {path}")
    with Image.open(path) as source:
        logo = source.convert("RGBA")
    return logo.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Windows ICO from the supplied GodEyes logo.")
    parser.add_argument("--logo", default=str(DEFAULT_LOGO), help="Source GodEyes PNG image.")
    parser.add_argument("--output", required=True, help="Path to the Windows .ico output file.")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    image = build_icon(args.logo)
    image.save(output, format="ICO", sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])
    with Image.open(output) as check:
        if check.format != "ICO":
            raise RuntimeError(f"Expected ICO output, got {check.format!r}")
        if check.size != (256, 256):
            raise RuntimeError(f"Expected 256x256 icon frame, got {check.size!r}")
    if output.stat().st_size < 1024:
        raise RuntimeError("Generated icon is unexpectedly small.")
    print(f"GodEyes icon built from supplied logo: {output} ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
