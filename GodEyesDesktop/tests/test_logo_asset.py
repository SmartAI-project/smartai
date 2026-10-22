from pathlib import Path


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
