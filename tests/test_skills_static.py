from pathlib import Path


def test_people_matrix_odoo_link_is_visible_on_keyboard_focus():
    css = Path("src/zira_dashboard/static/skills.css").read_text()

    assert ".odoo-link:focus-visible" in css
    assert "opacity: 1 !important" in css
