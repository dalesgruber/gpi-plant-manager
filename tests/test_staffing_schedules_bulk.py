"""load_schedules_bulk: the set-based bulk hydration must match the per-day
load_schedule path, including the SQL-pushed date/published filters. Skips
when DATABASE_URL is unset (same convention as the other Postgres tests)."""

import os
from datetime import date

import pytest

from zira_dashboard.staffing import Schedule, load_schedules_bulk, save_schedule

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)

D1 = date(2098, 6, 1)   # published, with assignment + notes
D2 = date(2098, 6, 2)   # draft, empty
WC = "Repair 1"
PERSON = "Bulk Test Person"


@pytest.fixture(autouse=True)
def _seed():
    from zira_dashboard import db, staffing
    db.bootstrap_schema()
    for d in (D1, D2):
        db.execute("DELETE FROM schedules WHERE day = %s", (d,))
        staffing._invalidate_schedule_cache(d)
    db.execute(
        "INSERT INTO work_centers (name, category, cell, meter_id, min_ops, max_ops) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (name) DO NOTHING",
        (WC, "Repair", "Bay 1", None, 1, 1),
    )
    db.execute(
        "INSERT INTO people (name, active, reserve, local_dirty) "
        "VALUES (%s, TRUE, FALSE, FALSE) ON CONFLICT (name) DO NOTHING",
        (PERSON,),
    )
    yield
    for d in (D1, D2):
        db.execute("DELETE FROM schedules WHERE day = %s", (d,))
        staffing._invalidate_schedule_cache(d)
    db.execute("DELETE FROM people WHERE name = %s", (PERSON,))


def test_bulk_matches_per_day_loader():
    from zira_dashboard import staffing
    save_schedule(Schedule(
        day=D1, published=True, assignments={WC: [PERSON]},
        notes="day note", wc_notes={WC: "wc note"}, testing_day=True,
    ))
    save_schedule(Schedule(day=D2, published=False, assignments={}))

    pairs = load_schedules_bulk(start=D1, end=D2)
    assert [d for d, _ in pairs] == [D2, D1]  # newest first
    out = dict(pairs)

    per_day = staffing._load_schedule_from_db(D1)
    b = out[D1]
    assert b.assignments == per_day.assignments == {WC: [PERSON]}
    assert b.notes == per_day.notes == "day note"
    assert b.wc_notes == per_day.wc_notes == {WC: "wc note"}
    assert b.published is True and b.testing_day is True
    assert out[D2].assignments == {} and out[D2].published is False


def test_bulk_pushes_filters_into_sql():
    save_schedule(Schedule(day=D1, published=True, assignments={}))
    save_schedule(Schedule(day=D2, published=False, assignments={}))

    pub_days = [d for d, _ in load_schedules_bulk(start=D1, end=D2, published_only=True)]
    assert D1 in pub_days and D2 not in pub_days

    assert [d for d, _ in load_schedules_bulk(start=D2, end=D2)] == [D2]
