from datetime import date
from zira_dashboard.staffing import Schedule


def test_schedule_custom_hours_defaults_to_none():
    s = Schedule(day=date(2026, 4, 28))
    assert s.custom_hours is None
