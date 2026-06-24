from pathlib import Path


def test_people_matrix_odoo_link_is_visible_on_keyboard_focus():
    css = Path("src/zira_dashboard/static/skills.css").read_text()

    assert ".odoo-link:focus-visible" in css
    assert "opacity: 1 !important" in css


def test_people_matrix_sort_headers_handle_keyboard_activation():
    js = Path("src/zira_dashboard/static/skills-page.js").read_text()

    assert "th.addEventListener('keydown'" in js
    assert "e.key === 'Enter'" in js
    assert "e.key === ' '" in js


def test_people_matrix_view_popover_closes_on_escape():
    js = Path("src/zira_dashboard/static/skills-page.js").read_text()

    assert "document.addEventListener('keydown'" in js
    assert "e.key === 'Escape'" in js
    assert "btn.focus()" in js


def test_people_matrix_refresh_button_exposes_busy_state():
    js = Path("src/zira_dashboard/static/skills-page.js").read_text()

    assert "btn.setAttribute('aria-busy', 'true')" in js
    assert "btn.setAttribute('aria-busy', 'false')" in js
