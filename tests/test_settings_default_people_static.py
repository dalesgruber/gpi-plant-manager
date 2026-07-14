from pathlib import Path


def test_partial_default_crew_can_close_without_a_minimum_staffing_gate():
    js = Path("src/zira_dashboard/static/settings.js").read_text()

    assert "Fewer than min" not in js
    assert "New days will start understaffed" not in js
