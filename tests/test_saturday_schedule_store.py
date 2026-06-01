"""saturday_schedule_store load/save/cache. Postgres-backed."""
import os
from datetime import time
import pytest
from zira_dashboard import db, saturday_schedule_store
from zira_dashboard.saturday_schedule_store import SaturdaySchedule, DEFAULT
from zira_dashboard.schedule_store import Break

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres"
)


@pytest.fixture(autouse=True)
def _reset():
    db.execute("DELETE FROM saturday_schedule WHERE id = 1")
    saturday_schedule_store.reload()
    yield
    db.execute("DELETE FROM saturday_schedule WHERE id = 1")
    saturday_schedule_store.reload()


def test_default_when_no_row():
    assert saturday_schedule_store.current() == DEFAULT
    assert DEFAULT.shift_start == time(6, 0)
    assert DEFAULT.shift_end == time(12, 0)
    assert DEFAULT.breaks == (
        Break(time(8, 0), time(8, 15), "Morning break"),
        Break(time(10, 0), time(10, 30), "Lunch"),
    )


def test_save_persists_and_invalidates_cache():
    s = SaturdaySchedule(time(6, 0), time(11, 0),
                         (Break(time(9, 0), time(9, 15), "Break"),))
    saturday_schedule_store.save(s)
    saturday_schedule_store.reload()
    assert saturday_schedule_store.current() == s


def test_save_reflected_in_current_without_reload():
    s = SaturdaySchedule(time(7, 0), time(13, 0), ())
    saturday_schedule_store.save(s)
    assert saturday_schedule_store.current() == s


def test_current_is_cached():
    saturday_schedule_store.current()  # prime cache as DEFAULT (no row)
    db.execute(
        "INSERT INTO saturday_schedule (id, shift_start, shift_end, breaks) "
        "VALUES (1, '05:00', '09:00', '[]'::jsonb) "
        "ON CONFLICT (id) DO UPDATE SET shift_start = EXCLUDED.shift_start"
    )
    assert saturday_schedule_store.current() == DEFAULT  # stale cache wins
    saturday_schedule_store.reload()
    assert saturday_schedule_store.current().shift_start == time(5, 0)
