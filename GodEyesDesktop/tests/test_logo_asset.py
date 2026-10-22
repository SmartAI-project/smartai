from pathlib import Path
import importlib.util


ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "app" / "assets" / "god_eyes_logo.svg"
ENTRY_POINTS = (ROOT / "GodEyes_main.py", ROOT / "app" / "main.py")
ICON_BUILDER = ROOT / "scripts" / "generate_icon.py"


def test_vector_logo_is_present_and_has_expected_brand_elements():
    svg = LOGO.read_text(encoding="utf-8")
    assert '<svg' in svg and 'viewBox="0 0 256 256"' in svg
    assert "#0c1c3a" in svg and "#27dddb" in svg
    assert "<circle" in svg and "<path" in svg


def test_desktop_entry_points_render_the_same_vector_logo():
    sources = [path.read_text(encoding="utf-8") for path in ENTRY_POINTS]
    assert sources[0] == sources[1]
    assert "from PySide6.QtSvg import QSvgRenderer" in sources[0]
    assert "god_eyes_logo.svg" in sources[0]
    assert "_godeyes_logo_pixmap" in sources[0]


def test_windows_icon_generator_is_part_of_the_source_bundle():
    script = ICON_BUILDER.read_text(encoding="utf-8")
    assert 'format="ICO"' in script
    assert "GodEyes icon created" in script


def test_generated_windows_icon_has_expected_format_and_dimensions(tmp_path):
    spec = importlib.util.spec_from_file_location("godeyes_icon_builder_test", ICON_BUILDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    image = module.build_icon()
    assert image.size == (256, 256)
    assert image.mode == "RGBA"
    output = tmp_path / "GodEyes-test.ico"
    image.save(output, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    from PIL import Image
    with Image.open(output) as check:
        assert check.format == "ICO"
    assert output.stat().st_size >= 1024
