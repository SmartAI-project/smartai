from pathlib import Path
import importlib.util

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "app" / "assets" / "god_eyes_logo.png"
ENTRY_POINTS = (ROOT / "GodEyes_main.py", ROOT / "app" / "main.py")
ICON_BUILDER = ROOT / "scripts" / "generate_icon.py"


def test_supplied_logo_image_is_present_and_readable():
    assert LOGO.is_file()
    with Image.open(LOGO) as image:
        assert image.format == "PNG"
        assert image.width >= 64 and image.height >= 64
        assert image.mode in {"RGBA", "RGB"}


def test_desktop_entry_points_use_the_same_supplied_logo_and_compact_sidebar():
    sources = [path.read_text(encoding="utf-8") for path in ENTRY_POINTS]
    assert sources[0] == sources[1]
    assert "god_eyes_logo.png" in sources[0]
    assert "self.logo_view.setMinimumHeight(78)" in sources[0]
    assert "logo.scaled(76,76" in sources[0]
    assert "QSvgRenderer" not in sources[0]


def test_windows_icon_is_generated_from_the_supplied_logo(tmp_path):
    spec = importlib.util.spec_from_file_location("godeyes_icon_builder_test", ICON_BUILDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    image = module.build_icon(LOGO)
    assert image.size == (256, 256)
    assert image.mode == "RGBA"
    output = tmp_path / "GodEyes-test.ico"
    image.save(output, format="ICO", sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])
    with Image.open(output) as check:
        assert check.format == "ICO"
    assert output.stat().st_size >= 1024
