from datetime import date
from zira_dashboard.production_history import attribute_for_day


def test_attribute_for_day_empty_schedule_returns_empty():
    out = attribute_for_day(
        assignments={},
        wc_totals={},
        elapsed_minutes=480,
    )
    assert out == {}
