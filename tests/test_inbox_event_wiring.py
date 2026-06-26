"""Phase 1 wiring: each inbox-native resolve action records one inbox_events row.

Collaborators (Odoo, suppression-table writes, inbox_log itself) are
monkeypatched, so these need no Postgres. They assert each handler calls
inbox_log.log_event_safe with the right event shape + actor.
"""
from datetime import date, datetime, timezone

from zira_dashboard import inbox_log
from zira_dashboard.routes import exceptions as exceptions_route


def _capture_events(monkeypatch):
    events = []
    monkeypatch.setattr(inbox_log, "log_event_safe",
                        lambda **kw: events.append(kw) or 1)
    return events


def test_time_off_approve_records_inbox_event(monkeypatch):
    from zira_dashboard import odoo_client

    events = _capture_events(monkeypatch)
    row = {
        "id": 55, "person_odoo_id": 7, "person_name": "Maria Delgado",
        "leave_type": "PTO", "date_from": date(2026, 6, 22),
        "date_to": date(2026, 6, 22), "hour_from": 8.5, "hour_to": 12.25,
        "state": "confirm", "odoo_leave_id": 99,
    }
    monkeypatch.setattr(exceptions_route, "_load_time_off_request", lambda rid: row)
    monkeypatch.setattr(odoo_client, "approve_leave", lambda leave_id: "validate")
    monkeypatch.setattr(exceptions_route, "_set_time_off_state", lambda old, state: None)
    monkeypatch.setattr(exceptions_route.time_off_audit, "record_decision",
                        lambda **kw: None)

    resp = exceptions_route._approve_time_off_sync(
        55, actor_upn="dale@gruberpallets.com",
        actor_name="Dale Gruber", source="inbox")

    assert resp.status_code == 200
    assert len(events) == 1
    e = events[0]
    assert e["item_kind"] == "time_off"
    assert e["item_key"] == "time_off:55"
    assert e["action"] == "approve"
    assert e["actor_upn"] == "dale@gruberpallets.com"
    assert e["person_name"] == "Maria Delgado"


def test_time_off_deny_records_inbox_event(monkeypatch):
    events = _capture_events(monkeypatch)
    row = {
        "id": 56, "person_odoo_id": 8, "person_name": "Carlos Ortega",
        "leave_type": "Unpaid", "date_from": date(2026, 6, 22),
        "date_to": date(2026, 6, 22), "state": "draft", "odoo_leave_id": None,
    }
    monkeypatch.setattr(exceptions_route, "_load_time_off_request", lambda rid: row)
    monkeypatch.setattr(exceptions_route, "_set_time_off_state", lambda old, state: None)
    monkeypatch.setattr(exceptions_route.time_off_audit, "record_decision",
                        lambda **kw: None)

    resp = exceptions_route._refuse_time_off_sync(
        56, reason="No coverage", actor_upn="dale@gruberpallets.com",
        actor_name="Dale Gruber", source="inbox")

    assert resp.status_code == 200
    assert len(events) == 1
    e = events[0]
    assert e["item_kind"] == "time_off"
    assert e["item_key"] == "time_off:56"
    assert e["action"] == "deny"
    assert e["reason"] == "No coverage"
