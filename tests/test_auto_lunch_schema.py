"""Auto-lunch schema migrations are present after bootstrap. Postgres-backed."""
import os
import pytest
from zira_dashboard import db

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres")


def _columns(table):
    return {r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s", (table,))}


def test_punch_log_has_source_column():
    db.bootstrap_schema()
    assert "source" in _columns("timeclock_punches_log")


def test_people_has_is_flexible_column():
    db.bootstrap_schema()
    assert "is_flexible" in _columns("people")


def test_auto_lunch_runs_and_settings_exist():
    db.bootstrap_schema()
    assert _columns("auto_lunch_runs") >= {
        "person_odoo_id", "day", "kind", "state", "target_out_at",
        "target_in_at", "wc_name", "out_punch_id", "in_punch_id"}
    assert _columns("auto_lunch_settings") >= {
        "enabled", "observe_only", "flex_after_hours", "flex_minutes"}


def test_settings_singleton_seeded():
    db.bootstrap_schema()
    rows = db.query("SELECT id FROM auto_lunch_settings WHERE id = 1")
    assert len(rows) == 1
