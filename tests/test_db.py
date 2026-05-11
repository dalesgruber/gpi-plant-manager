import os
import pytest

from zira_dashboard import db


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="No DATABASE_URL — Postgres tests need a live database",
)


@pytest.fixture(autouse=True)
def reset_pool():
    db.shutdown_pool()
    yield
    db.shutdown_pool()


def test_init_pool_idempotent():
    db.init_pool()
    db.init_pool()


def test_query_and_execute_round_trip():
    db.init_pool()
    db.execute("CREATE TEMP TABLE _t (id INT, name TEXT)")
    db.execute("INSERT INTO _t VALUES (1, 'alpha'), (2, 'beta')")
    rows = db.query("SELECT id, name FROM _t ORDER BY id")
    assert rows == [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]


def test_cursor_rolls_back_on_exception():
    db.init_pool()
    db.execute("CREATE TEMP TABLE _t (id INT)")
    with pytest.raises(RuntimeError, match="boom"):
        with db.cursor() as cur:
            cur.execute("INSERT INTO _t VALUES (1)")
            raise RuntimeError("boom")
    rows = db.query("SELECT id FROM _t")
    assert rows == []


def test_bootstrap_schema_idempotent():
    db.init_pool()
    db.bootstrap_schema()
    db.bootstrap_schema()
    rows = db.query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name IN "
        "('people', 'skills', 'person_skills', 'work_centers', "
        "'schedules', 'app_settings', 'sync_outbox')"
    )
    names = {r["table_name"] for r in rows}
    for expected in ("people", "skills", "person_skills", "work_centers",
                     "schedules", "app_settings", "sync_outbox"):
        assert expected in names, f"missing {expected}"


def test_bootstrap_creates_precompute_tables():
    db.init_pool()
    db.bootstrap_schema()
    rows = db.query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name IN "
        "('production_daily','today_attendance_cache',"
        "'today_timeoff_cache','today_production_cache')"
    )
    names = {r["table_name"] for r in rows}
    assert names == {
        "production_daily",
        "today_attendance_cache",
        "today_timeoff_cache",
        "today_production_cache",
    }


def test_production_daily_pk_and_indexes():
    db.init_pool()
    db.bootstrap_schema()
    # PK columns
    pk_rows = db.query(
        "SELECT a.attname FROM pg_index i "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = 'production_daily'::regclass AND i.indisprimary "
        "ORDER BY a.attname"
    )
    assert {r["attname"] for r in pk_rows} == {"day", "emp_id", "wc_name"}
    # Both secondary indexes exist
    idx_rows = db.query(
        "SELECT indexname FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = 'production_daily'"
    )
    idx_names = {r["indexname"] for r in idx_rows}
    assert any("name" in n and "day" in n for n in idx_names)
    assert any("wc_name" in n and "day" in n for n in idx_names)
