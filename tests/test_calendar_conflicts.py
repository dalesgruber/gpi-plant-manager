"""Detection tests for zira_dashboard.calendar_conflicts (pure classifier +
the Odoo/roster gather with its graceful Postgres fallback)."""

from types import SimpleNamespace

import zira_dashboard.calendar_conflicts as cc
import zira_dashboard.odoo_client as oc
import zira_dashboard.staffing as staffing_mod

MON_FRI = frozenset({0, 1, 2, 3, 4})
_MON_FRI_HOURS = {str(d): ["6", "14"] for d in range(5)}
_MON_THU_HOURS = {str(d): ["6", "14"] for d in range(4)}


def _patch_odoo(monkeypatch, employees, schedules, cal_hours):
    monkeypatch.setattr(oc, "fetch_employees", lambda: employees)
    monkeypatch.setattr(oc, "fetch_work_schedules", lambda: schedules)
    monkeypatch.setattr(oc, "fetch_calendar_hours", lambda ids: cal_hours)


def test_covers_every_plant_weekday_is_ok():
    assert cc.classify_conflict(MON_FRI, {0, 1, 2, 3, 4}, is_flexible=False, has_calendar=True) == "ok"


def test_extra_weekend_coverage_still_ok():
    assert cc.classify_conflict(MON_FRI, {0, 1, 2, 3, 4, 5}, is_flexible=False, has_calendar=True) == "ok"


def test_missing_friday_is_missing_days():
    assert cc.classify_conflict(MON_FRI, {0, 1, 2, 3}, is_flexible=False, has_calendar=True) == "missing_days"


def test_flexible_flag_is_flexible():
    assert cc.classify_conflict(MON_FRI, {0, 1, 2, 3, 4}, is_flexible=True, has_calendar=True) == "flexible"


def test_calendar_with_no_covered_weekdays_is_flexible():
    assert cc.classify_conflict(MON_FRI, set(), is_flexible=False, has_calendar=True) == "flexible"


def test_no_calendar_is_no_calendar():
    assert cc.classify_conflict(MON_FRI, set(), is_flexible=False, has_calendar=False) == "no_calendar"


def test_no_calendar_takes_precedence_over_flexible():
    assert cc.classify_conflict(MON_FRI, set(), is_flexible=True, has_calendar=False) == "no_calendar"


def test_gather_excludes_reserves_and_non_roster(monkeypatch):
    employees = [
        {"id": 1, "name": "Ana Scheduled", "resource_calendar_id": [10, "M-F"]},
        {"id": 2, "name": "Bob Reserve", "resource_calendar_id": [10, "M-F"]},
        {"id": 3, "name": "Cara NotRostered", "resource_calendar_id": [10, "M-F"]},
        {"id": 4, "name": "Dan Missing Fri", "resource_calendar_id": [11, "M-Th"]},
    ]
    schedules = [
        {"id": 10, "name": "M-F", "is_flexible": False},
        {"id": 11, "name": "M-Th", "is_flexible": False},
    ]
    _patch_odoo(monkeypatch, employees, schedules, {10: _MON_FRI_HOURS, 11: _MON_THU_HOURS})
    roster = [
        SimpleNamespace(employee_id=1, reserve=False, wage_type="hourly", is_flexible=False),
        SimpleNamespace(employee_id=2, reserve=True, wage_type="hourly", is_flexible=False),
        SimpleNamespace(employee_id=4, reserve=False, wage_type="hourly", is_flexible=False),
    ]
    monkeypatch.setattr(staffing_mod, "load_roster", lambda: roster)

    rows, notes = cc.gather_rows(MON_FRI)

    by_id = {r["odoo_id"]: r for r in rows}
    assert set(by_id) == {1, 4}
    assert by_id[1]["verdict"] == "ok"
    assert by_id[4]["verdict"] == "missing_days"
    assert by_id[4]["missing"] == {4}
    assert notes == []


def test_gather_excludes_flexible_and_salaried_people(monkeypatch):
    # Absence flow applies only to hourly + fixed-schedule people. Flexible and
    # salaried people are never declared absent, so a calendar gap isn't a
    # conflict for them — they must not be flagged.
    employees = [
        {"id": 1, "name": "Ana Hourly Fixed", "resource_calendar_id": [11, "M-Th"]},
        {"id": 2, "name": "Flora Flexible", "resource_calendar_id": [11, "M-Th"]},
        {"id": 3, "name": "Sam Salaried", "resource_calendar_id": [11, "M-Th"]},
    ]
    schedules = [{"id": 11, "name": "M-Th", "is_flexible": False}]
    _patch_odoo(monkeypatch, employees, schedules, {11: _MON_THU_HOURS})
    roster = [
        SimpleNamespace(employee_id=1, reserve=False, wage_type="hourly", is_flexible=False),
        SimpleNamespace(employee_id=2, reserve=False, wage_type="hourly", is_flexible=True),
        SimpleNamespace(employee_id=3, reserve=False, wage_type="monthly", is_flexible=False),
    ]
    monkeypatch.setattr(staffing_mod, "load_roster", lambda: roster)

    rows, notes = cc.gather_rows(MON_FRI)

    by_id = {r["odoo_id"]: r for r in rows}
    assert set(by_id) == {1}                      # flexible (2) + salaried (3) excluded
    assert by_id[1]["verdict"] == "missing_days"  # hourly fixed, missing Fri — still flagged
    assert notes == []


def test_gather_falls_back_to_all_active_when_roster_unavailable(monkeypatch):
    employees = [
        {"id": 1, "name": "Ana", "resource_calendar_id": [10, "M-F"]},
        {"id": 2, "name": "Bob NoCal", "resource_calendar_id": False},
    ]
    schedules = [{"id": 10, "name": "M-F", "is_flexible": False}]
    _patch_odoo(monkeypatch, employees, schedules, {10: _MON_FRI_HOURS})

    def _boom():
        raise RuntimeError("postgres unreachable")

    monkeypatch.setattr(staffing_mod, "load_roster", _boom)

    rows, notes = cc.gather_rows(MON_FRI)
    by_id = {r["odoo_id"]: r for r in rows}
    assert set(by_id) == {1, 2}
    assert by_id[2]["verdict"] == "no_calendar"
    assert notes and "roster unavailable" in notes[0].lower()


def test_current_conflicts_returns_only_conflicts(monkeypatch):
    employees = [
        {"id": 1, "name": "Ana OK", "resource_calendar_id": [10, "M-F"]},
        {"id": 2, "name": "Dan Missing Fri", "resource_calendar_id": [11, "M-Th"]},
    ]
    schedules = [
        {"id": 10, "name": "M-F", "is_flexible": False},
        {"id": 11, "name": "M-Th", "is_flexible": False},
    ]
    _patch_odoo(monkeypatch, employees, schedules, {10: _MON_FRI_HOURS, 11: _MON_THU_HOURS})
    monkeypatch.setattr(cc, "plant_weekdays", lambda: (MON_FRI, None))

    # Make the roster lookup unavailable so all active Odoo employees are kept
    # (no reserve filter) — exercises current_conflicts() end to end.
    def _no_roster():
        raise RuntimeError("no db")

    monkeypatch.setattr(staffing_mod, "load_roster", _no_roster)

    conflicts = cc.current_conflicts()
    assert {c["odoo_id"] for c in conflicts} == {2}
