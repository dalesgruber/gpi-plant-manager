from pathlib import Path


def test_planned_saturday_header_opens_an_accessible_schedule_modal():
    html = Path("src/zira_dashboard/templates/timeclock_home.html").read_text()

    assert 'id="saturday-schedule-trigger"' in html
    assert 'aria-haspopup="dialog"' in html
    assert 'id="saturday-schedule-modal"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert "Saturday schedule has not been published yet." in html
    assert "event.key === 'Escape'" in html
    assert "scheduleTrigger.focus()" in html
