"""Resolution of (shift_start, shift_end) per Odoo work schedule. Postgres-backed."""

import os
from datetime import date, time

import pytest

from zira_dashboard import db, work_schedule_store, shift_config
from zira_dashboard.routes.timeclock import _hours_for_punch

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)

CAL_ID = 990003
MONDAY = date(2026, 6, 1)  # weekday 0


@pytest.fixture(autouse=True)
def _clean():
    db.execute("DELETE FROM work_schedules WHERE resource_calendar_id = %s", (CAL_ID,))
    work_schedule_store.reload()
    yield
    db.execute("DELETE FROM work_schedules WHERE resource_calendar_id = %s", (CAL_ID,))
    work_schedule_store.reload()


def test_override_hours_for_weekday():
    work_schedule_store.create(CAL_ID, "Drivers")
    work_schedule_store.refresh_synced(CAL_ID, "Drivers", {"0": ["05:45", "14:30"]})
    start, end = _hours_for_punch(CAL_ID, MONDAY)
    assert start == time(5, 45)
    assert end == time(14, 30)


def test_weekday_without_hours_falls_back_to_plant_default():
    work_schedule_store.create(CAL_ID, "Drivers")
    work_schedule_store.refresh_synced(CAL_ID, "Drivers", {"0": ["05:45", "14:30"]})
    saturday = date(2026, 6, 6)  # weekday 5, not configured
    start, end = _hours_for_punch(CAL_ID, saturday)
    assert start == shift_config.shift_start_for(saturday)
    assert end == shift_config.shift_end_for(saturday)


def test_no_calendar_uses_plant_default():
    start, end = _hours_for_punch(None, MONDAY)
    assert start == shift_config.shift_start_for(MONDAY)
    assert end == shift_config.shift_end_for(MONDAY)
