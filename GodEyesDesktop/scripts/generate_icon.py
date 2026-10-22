from __future__ import annotations

import argparse
from pathlib import Path
from PIL import Image, ImageDraw


def build_icon(size: int = 512) -> Image.Image:
    """Draw the compact GodEyes eye mark at high resolution before downsampling."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = size / 256.0

    def box(x: float, y: float, w: float, h: float):
        return tuple(round(n * scale) for n in (x, y, w, h))

    draw.rounded_rectangle(box(9, 9, 238, 238), radius=round(52 * scale),
                           fill="#0c1c3a", outline="#3675e8", width=max(2, round(5 * scale)))
    points = [(36,128),(59,101),(88,83),(128,80),(168,83),(197,101),
              (220,128),(197,155),(168,173),(128,176),(88,173),(59,155)]
    draw.polygon([(round(x*scale), round(y*scale)) for x,y in points], fill="#f2f8ff")
    draw.ellipse(box(85,85,86,86), fill="#2178f6", outline="#4fe0e2", width=max(2, round(6*scale)))
    draw.ellipse(box(101,101,54,54), fill="#091e45")
    draw.ellipse(box(106,104,14,14), fill="#ffffff")
    draw.arc(box(48,109,160,67), start=18, end=162, fill="#27dddb", width=max(2,round(4*scale)))
    return image.resize((256,256), Image.Resampling.LANCZOS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the GodEyes Windows application icon.")
    parser.add_argument("--output", required=True, help="Path to the Windows .ico output file.")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    image = build_icon()
    image.save(output, format="ICO", sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])
    with Image.open(output) as check:
        if check.format != "ICO":
            raise RuntimeError(f"Expected ICO output, got {check.format!r}")
    if output.stat().st_size < 1024:
        raise RuntimeError("Generated icon is unexpectedly small.")
    print(f"GodEyes icon created: {output} ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
