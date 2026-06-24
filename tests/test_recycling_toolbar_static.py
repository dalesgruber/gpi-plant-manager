from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src" / "zira_dashboard" / "templates" / "recycling.html"
CSS = ROOT / "src" / "zira_dashboard" / "static" / "recycling.css"


def test_recycling_range_toolbar_sits_below_subnav_not_header():
    html = TEMPLATE.read_text(encoding="utf-8")
    header_end = html.index("</header>")
    subnav = html.index('{% include "_dashboards_subnav.html" %}')
    toolbar = html.index('<form class="rc-toolbar"')

    assert toolbar > header_end
    assert toolbar > subnav


def test_recycling_range_toolbar_is_standalone_row():
    css = CSS.read_text(encoding="utf-8")

    start = css.index(".rc-toolbar")
    block = css[start:css.index("}", start)]
    assert "margin-left" not in block
    assert "margin:" in block
    assert "padding:" in block
