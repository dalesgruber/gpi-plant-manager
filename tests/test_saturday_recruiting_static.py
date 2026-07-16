from pathlib import Path


def test_saturday_recruiting_panel_assets_are_loaded_by_staffing_page():
    html = Path("src/zira_dashboard/templates/staffing.html").read_text()
    assert 'href="/static/saturday-recruiting.css?v={{ static_v(\'saturday-recruiting.css\') }}"' in html
    assert 'src="/static/saturday-recruiting.js?v={{ static_v(\'saturday-recruiting.js\') }}"' in html
    assert '{% include "_saturday_recruiting_panel.html" %}' in html


def test_saturday_recruiting_panel_keeps_confirmation_and_accessible_errors():
    html = Path("src/zira_dashboard/templates/_saturday_recruiting_panel.html").read_text()
    js = Path("src/zira_dashboard/static/saturday-recruiting.js").read_text()
    assert 'id="saturday-recruiting-error" role="alert" hidden' in html
    assert 'data-saturday-action="activate"' in html
    assert 'data-saturday-action="cancel"' in html
    assert 'data-commitment-cancel="{{ commitment.person_id }}"' in html
    assert "management must directly contact" in js
    assert "window.confirm" in js
    assert "window.prompt" in js


def test_saturday_recruiting_style_has_danger_and_small_screen_rules():
    css = Path("src/zira_dashboard/static/saturday-recruiting.css").read_text()
    assert ".saturday-actions .danger" in css
    assert "@media (max-width: 640px)" in css
