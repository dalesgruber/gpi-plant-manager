"""Supervisor scheduler time-off editor contract tests."""

from datetime import date

from fastapi.testclient import TestClient

from zira_dashboard.app import app
from zira_dashboard.routes import staffing as staffing_routes


def test_supervisor_edit_stages_same_odoo_leave_and_queues_push(monkeypatch):
    staged, queued = [], []
    monkeypatch.setattr(staffing_routes, "_editable_time_off_for_day", lambda rid, day: {
        "id": rid, "shape": "midday_gap", "holiday_status_id": 5,
        "odoo_leave_id": 701, "date_from": date(2026, 7, 17),
        "date_to": date(2026, 7, 17),
    })
    monkeypatch.setattr(
        staffing_routes, "_stage_supervisor_time_off_edit",
        lambda **kwargs: (staged.append(kwargs), True)[1],
    )
    monkeypatch.setattr(staffing_routes, "_scheduler_shift_bounds", lambda _day: (6.0, 14.0))
    monkeypatch.setattr(staffing_routes, "invalidate_today_cache", lambda: None)
    monkeypatch.setattr(staffing_routes, "_queue_time_off_push", queued.append)

    response = staffing_routes._edit_scheduler_time_off(91, {
        "day": "2026-07-17", "date_from": "2026-07-18", "date_to": "2026-07-18",
        "time_from": "09:00", "time_to": "11:00",
    })

    assert response.status_code == 200
    assert staged[0]["request_id"] == 91
    assert staged[0]["holiday_status_id"] == 5
    assert staged[0]["day"] == date(2026, 7, 17)
    assert queued == [91]


def test_supervisor_cancel_stages_cancel_and_queues_push(monkeypatch):
    staged, queued = [], []
    monkeypatch.setattr(
        staffing_routes, "_editable_time_off_for_day",
        lambda rid, day: {"id": rid, "odoo_leave_id": 701},
    )
    monkeypatch.setattr(
        staffing_routes, "_stage_supervisor_time_off_cancel",
        lambda request_id, day: (staged.append((request_id, day)), True)[1],
    )
    monkeypatch.setattr(staffing_routes, "invalidate_today_cache", lambda: None)
    monkeypatch.setattr(staffing_routes, "_queue_time_off_push", queued.append)

    response = staffing_routes._cancel_scheduler_time_off(91, {"day": "2026-07-17"})

    assert response.status_code == 200
    assert staged == [(91, date(2026, 7, 17))]
    assert queued == [91]


def test_supervisor_edit_rejects_invalid_partial_window(monkeypatch):
    monkeypatch.setattr(staffing_routes, "_editable_time_off_for_day", lambda *_: {
        "id": 91, "shape": "midday_gap", "holiday_status_id": 5,
    })
    monkeypatch.setattr(staffing_routes, "_scheduler_shift_bounds", lambda _day: (6.0, 14.0))

    response = TestClient(app).post("/api/staffing/time-off/91/edit", json={
        "day": "2026-07-17", "date_from": "2026-07-17", "date_to": "2026-07-17",
        "time_from": "12:00", "time_to": "09:00",
    })

    assert response.status_code == 422


def test_supervisor_endpoints_reject_local_or_out_of_day_record(monkeypatch):
    monkeypatch.setattr(staffing_routes, "_editable_time_off_for_day", lambda *_: None)

    response = TestClient(app).post(
        "/api/staffing/time-off/91/cancel", json={"day": "2026-07-17"},
    )

    assert response.status_code == 404


def test_supervisor_edit_does_not_queue_when_conditional_stage_loses_race(monkeypatch):
    """A stale preflight lookup must not stage or push an ineligible leave."""
    invalidations, queued = [], []
    monkeypatch.setattr(staffing_routes, "_editable_time_off_for_day", lambda *_: {
        "id": 91, "shape": "full_day", "holiday_status_id": 5,
    })
    monkeypatch.setattr(staffing_routes, "_stage_supervisor_time_off_edit", lambda **_: False)
    monkeypatch.setattr(staffing_routes, "invalidate_today_cache", lambda: invalidations.append(True))
    monkeypatch.setattr(staffing_routes, "_queue_time_off_push", queued.append)

    response = staffing_routes._edit_scheduler_time_off(91, {
        "day": "2026-07-17", "date_from": "2026-07-17", "date_to": "2026-07-17",
    })

    assert response.status_code == 404
    assert invalidations == []
    assert queued == []


def test_staging_updates_recheck_scheduler_editability(monkeypatch):
    """Both writes return no row when reconciliation changed eligibility."""
    queries = []
    monkeypatch.setattr(
        staffing_routes.db, "query",
        lambda sql, params: (queries.append((sql, params)), [])[1],
    )
    day = date(2026, 7, 17)

    assert staffing_routes._stage_supervisor_time_off_edit(
        request_id=91, date_from=day, date_to=day, hour_from=None,
        hour_to=None, shape="full_day", holiday_status_id=5, day=day,
    ) is False
    assert staffing_routes._stage_supervisor_time_off_cancel(91, day) is False

    for sql, params in queries:
        assert "odoo_leave_id IS NOT NULL" in sql
        assert "NOT local_record" in sql
        assert "state = ANY(%s)" in sql
        assert "date_from <= %s AND date_to >= %s" in sql
        assert "RETURNING id" in sql
        assert params[-2:] == (day, day)
