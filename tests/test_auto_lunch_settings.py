"""auto_lunch_settings load/save/cache. Postgres-backed."""
import os
import pytest
from zira_dashboard import db, auto_lunch_settings as als

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres")


@pytest.fixture(autouse=True)
def _reset():
    db.bootstrap_schema()
    db.execute("UPDATE auto_lunch_settings SET enabled=FALSE, observe_only=TRUE, "
               "flex_after_hours=5.0, flex_minutes=30 WHERE id=1")
    als.reload()
    yield
    db.execute("UPDATE auto_lunch_settings SET enabled=FALSE, observe_only=TRUE, "
               "flex_after_hours=5.0, flex_minutes=30 WHERE id=1")
    als.reload()


def test_defaults_when_seeded():
    s = als.current()
    assert s.enabled is False and s.observe_only is True
    assert s.flex_after_hours == 5.0 and s.flex_minutes == 30


def test_save_round_trip_and_cache_invalidation():
    als.save(als.Settings(enabled=True, observe_only=False,
                          flex_after_hours=6.0, flex_minutes=45))
    s = als.current()
    assert s.enabled is True and s.observe_only is False
    assert s.flex_after_hours == 6.0 and s.flex_minutes == 45
    # A direct DB change is not seen until reload (proves caching).
    db.execute("UPDATE auto_lunch_settings SET flex_minutes=15 WHERE id=1")
    assert als.current().flex_minutes == 45
    assert als.reload().flex_minutes == 15
