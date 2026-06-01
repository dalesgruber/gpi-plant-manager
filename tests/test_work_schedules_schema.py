"""Schema presence checks for per-schedule rounding. Postgres-backed."""

import os

import pytest

from zira_dashboard import db

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)


def test_work_schedules_table_queryable():
    # Selecting every column should not raise; an empty table is fine.
    db.query(
        "SELECT resource_calendar_id, name, work_hours, in_before_min, "
        "in_after_min, out_before_min, out_after_min, last_synced_at, "
        "updated_at FROM work_schedules LIMIT 1"
    )


def test_people_has_resource_calendar_id():
    rows = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'people' AND column_name = 'resource_calendar_id'"
    )
    assert rows, "people.resource_calendar_id column is missing"
