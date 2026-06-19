"""missing_wc: pure shaping (no DB) + cache/resolve round-trips (Postgres)."""

import os

import pytest

from zira_dashboard import missing_wc


# ---- pure shaping (no DB) ----

def _people():
    return {
        7: {"name": "Maria", "wage_type": "hourly", "active": True, "excluded": False},
        8: {"name": "Boss", "wage_type": "monthly", "active": True, "excluded": False},
        9: {"name": "Gone", "wage_type": "hourly", "active": False, "excluded": False},
    }


def test_shape_keeps_only_active_hourly_unresolved():
    cached = [
        {"att_id": 1, "employee_odoo_id": 7, "employee_name": "Maria", "check_in": "2026-06-02T11:58:00+00:00"},
        {"att_id": 2, "employee_odoo_id": 8, "employee_name": "Boss", "check_in": "2026-06-02T08:00:00+00:00"},
        {"att_id": 3, "employee_odoo_id": 9, "employee_name": "Gone", "check_in": "2026-06-02T07:00:00+00:00"},
        {"att_id": 4, "employee_odoo_id": 7, "employee_name": "Maria", "check_in": "2026-06-01T06:00:00+00:00"},
    ]
    rows = missing_wc.shape_rows(cached, _people(), resolved={4})
    ids = [r["attendance_id"] for r in rows]
    assert ids == [1]  # salaried(2) + inactive(3) dropped; 4 resolved; only hourly-active-unresolved 1
    assert rows[0]["name"] == "Maria"
    assert rows[0]["check_in_label"]  # formatted, non-empty


def test_shape_sorts_newest_first():
    cached = [
        {"att_id": 1, "employee_odoo_id": 7, "employee_name": "M", "check_in": "2026-06-01T06:00:00+00:00"},
        {"att_id": 2, "employee_odoo_id": 7, "employee_name": "M", "check_in": "2026-06-03T06:00:00+00:00"},
    ]
    rows = missing_wc.shape_rows(cached, _people(), resolved=set())
    assert [r["attendance_id"] for r in rows] == [2, 1]


def test_shape_normalizes_json_string_ids():
    cached = [
        {"att_id": "1", "employee_odoo_id": "7", "employee_name": "M", "check_in": "2026-06-01T06:00:00+00:00"},
        {"att_id": "2", "employee_odoo_id": "7", "employee_name": "M", "check_in": "2026-06-02T06:00:00+00:00"},
    ]
    rows = missing_wc.shape_rows(cached, _people(), resolved={2})
    assert [r["attendance_id"] for r in rows] == [1]
    assert rows[0]["employee_odoo_id"] == 7


# ---- DB-backed cache/resolve ----

pg = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs Postgres")


@pg
def test_cache_write_read_round_trip():
    from zira_dashboard import db
    missing_wc.write_cache([{"att_id": 1, "employee_odoo_id": 7}])
    assert missing_wc._read_cache() == [{"att_id": 1, "employee_odoo_id": 7}]
    db.execute("UPDATE missing_wc_cache SET snapshot = '[]'::jsonb WHERE id = 1")


@pg
def test_resolve_and_resolved_ids():
    from zira_dashboard import db
    db.execute("DELETE FROM missing_wc_resolved WHERE attendance_id = %s", (999002,))
    missing_wc.resolve(999002, "assigned", name="Maria", wc_name="Dismantler 1")
    assert 999002 in missing_wc.resolved_ids()
    db.execute("DELETE FROM missing_wc_resolved WHERE attendance_id = %s", (999002,))
