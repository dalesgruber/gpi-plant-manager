"""Odoo faults surfaced by inbox time-off actions must read as clean,
actionable English — never the raw ``<Fault N: '...\\n...'>`` repr.

Regression for the inbox card that showed
``<Fault 2: 'The following employees are not supposed to work during that
period:\\n Gerardo Vergara Quintero'>`` after an Approve click.
"""
import json
import xmlrpc.client
from datetime import date

from zira_dashboard.routes import exceptions as exceptions_route

_WORK_SCHEDULE_FAULT = xmlrpc.client.Fault(
    2,
    "The following employees are not supposed to work during that period:\n"
    " Gerardo Vergara Quintero",
)


def _body(resp):
    return json.loads(resp.body.decode())


def test_friendly_error_strips_fault_repr_and_collapses_newlines():
    msg = exceptions_route._friendly_odoo_error(_WORK_SCHEDULE_FAULT)
    assert "<Fault" not in msg
    assert "\n" not in msg
    # The specific employee context Odoo gave is preserved.
    assert "Gerardo Vergara Quintero" in msg


def test_friendly_error_adds_working_schedule_hint_for_schedule_conflict():
    msg = exceptions_route._friendly_odoo_error(_WORK_SCHEDULE_FAULT)
    assert "Working Schedule" in msg


def test_friendly_error_cleans_generic_fault():
    fault = xmlrpc.client.Fault(3, "Some other Odoo problem")
    msg = exceptions_route._friendly_odoo_error(fault)
    assert "<Fault" not in msg
    assert "Some other Odoo problem" in msg


def test_friendly_error_passes_through_plain_exception():
    msg = exceptions_route._friendly_odoo_error(ValueError("plain boom"))
    assert msg == "plain boom"


# --------------------------------------------------------------------------
# Local-record fallback: the work-schedule rejection no longer hard-fails.
# The absence is recorded locally (poller-proof `local_record` row), the
# Odoo copy is refused with a note, and the would-be "denied" kiosk popup
# is pre-suppressed. Any OTHER Odoo error keeps the friendly-500 contract.
# --------------------------------------------------------------------------


def _fallback_row():
    return {
        "id": 71, "person_odoo_id": 7, "person_name": "Gerardo Vergara",
        "leave_type": "Absence", "shape": "full_day",
        "date_from": date(2026, 7, 3), "date_to": date(2026, 7, 3),
        "hour_from": None, "hour_to": None, "note": None,
        "state": "confirm", "odoo_leave_id": 88, "sync_error": None,
        "originating_kiosk_user": True,
    }


def _wire_fallback(monkeypatch, row, events):
    """Patch every collaborator of the local-record fallback; append a tag
    to ``events`` per side effect so tests can assert ordering."""
    from unittest.mock import MagicMock

    from zira_dashboard import db, employee_notifications, odoo_client, time_off_sync

    monkeypatch.setattr(exceptions_route, "_load_time_off_request", lambda rid: row)

    def _raise(_leave_id):
        raise _WORK_SCHEDULE_FAULT

    monkeypatch.setattr(odoo_client, "approve_leave", _raise)
    monkeypatch.setattr(
        odoo_client, "refuse_leave",
        lambda lid: events.append(("refuse", lid)))
    monkeypatch.setattr(
        odoo_client, "post_leave_message",
        lambda lid, msg: events.append(("chatter", lid, msg)))
    monkeypatch.setattr(
        employee_notifications, "suppress_resolution",
        lambda pid, req, kind: events.append(("suppress", pid, req["id"], kind)))
    monkeypatch.setattr(
        employee_notifications, "unsuppress_resolution",
        lambda rid, kind: events.append(("unsuppress", rid, kind)))
    monkeypatch.setattr(
        db, "execute",
        lambda sql, params=None: events.append(("sql", sql, params)))
    monkeypatch.setattr(
        time_off_sync, "cascade_on_state_change",
        lambda old, new: events.append(("cascade", old["state"], new["state"])))
    monkeypatch.setattr(exceptions_route, "_refresh_time_off_surfaces", lambda: None)
    decision = MagicMock()
    monkeypatch.setattr(exceptions_route.time_off_audit, "record_decision", decision)
    inbox_event = MagicMock()
    monkeypatch.setattr(exceptions_route.inbox_log, "log_event_safe", inbox_event)
    return decision, inbox_event


def test_approve_records_locally_when_odoo_rejects_schedule(monkeypatch):
    events: list = []
    row = _fallback_row()
    decision, inbox_event = _wire_fallback(monkeypatch, row, events)

    resp = exceptions_route._approve_time_off_sync(
        71, actor_upn="dale@gruberpallets.com", actor_name="Dale", source="inbox")

    assert resp.status_code == 200
    body = _body(resp)
    assert body["ok"] is True
    assert body["approved"] is True
    assert body["state"] == "validate"
    assert body["recorded_locally"] is True
    assert "recorded" in body["warning"].lower()
    assert body["decision"]["result_state"] == "validate"

    # The Odoo copy was refused, and the local row became a poller-proof
    # approved record.
    assert ("refuse", 88) in events
    local_updates = [e for e in events
                     if e[0] == "sql" and "local_record = TRUE" in e[1]]
    assert len(local_updates) == 1
    assert "state = 'validate'" in local_updates[0][1]
    # sync_error is rendered on the employee's kiosk detail page — the
    # local-record marker must not surface there as a red error.
    assert "sync_error = NULL" in local_updates[0][1]
    assert not [e for e in events if e[0] == "unsuppress"]
    assert ("cascade", "confirm", "validate") in events

    # Ordering: popup suppression BEFORE the Odoo refuse (closes the poll
    # race), refuse BEFORE the local write (a failed refuse must leave the
    # row untouched).
    tags = [e[0] for e in events]
    assert tags.index("suppress") < tags.index("refuse")
    assert tags.index("refuse") < tags.index("sql")
    suppress = next(e for e in events if e[0] == "suppress")
    assert suppress[1:] == (7, 71, "time_off_denied")

    # Chatter note on the refused leave, best effort.
    assert any(e[0] == "chatter" and e[1] == 88 for e in events)

    # Audit trail spells out the local-only recording.
    decision.assert_called_once()
    assert "Recorded in Plant Manager" in decision.call_args.kwargs["reason"]
    assert decision.call_args.kwargs["result_state"] == "validate"
    inbox_event.assert_called_once()
    assert "recorded locally" in inbox_event.call_args.kwargs["outcome"]


def test_approve_falls_back_to_500_when_refuse_also_fails(monkeypatch):
    events: list = []
    row = _fallback_row()
    decision, inbox_event = _wire_fallback(monkeypatch, row, events)

    from zira_dashboard import odoo_client

    def _refuse_boom(_lid):
        raise xmlrpc.client.Fault(4, "refuse failed too")

    monkeypatch.setattr(odoo_client, "refuse_leave", _refuse_boom)

    resp = exceptions_route._approve_time_off_sync(71, source="inbox")

    # Nothing half-recorded: the original friendly contract survives.
    assert resp.status_code == 500
    err = _body(resp)["error"]
    assert "<Fault" not in err
    assert "\n" not in err
    assert "Working Schedule" in err
    assert not [e for e in events if e[0] == "sql"]
    assert not [e for e in events if e[0] == "cascade"]
    decision.assert_not_called()
    inbox_event.assert_not_called()
    # The pre-inserted suppression row must not outlive the aborted
    # fallback — a later genuine Odoo-side denial must still notify.
    assert ("unsuppress", 71, "time_off_denied") in events
    tags = [e[0] for e in events]
    assert tags.index("unsuppress") > tags.index("suppress")


def test_approve_local_record_tolerates_chatter_failure(monkeypatch):
    events: list = []
    row = _fallback_row()
    decision, _inbox_event = _wire_fallback(monkeypatch, row, events)

    from zira_dashboard import odoo_client

    def _chatter_boom(_lid, _msg):
        raise xmlrpc.client.Fault(5, "chatter down")

    monkeypatch.setattr(odoo_client, "post_leave_message", _chatter_boom)

    resp = exceptions_route._approve_time_off_sync(71, source="inbox")

    assert resp.status_code == 200
    assert _body(resp)["recorded_locally"] is True
    decision.assert_called_once()


def test_approve_keeps_friendly_500_for_other_odoo_errors(monkeypatch):
    from unittest.mock import MagicMock

    from zira_dashboard import odoo_client

    row = _fallback_row()
    monkeypatch.setattr(exceptions_route, "_load_time_off_request", lambda rid: row)

    def _raise(_leave_id):
        raise xmlrpc.client.Fault(3, "Some other Odoo problem")

    monkeypatch.setattr(odoo_client, "approve_leave", _raise)
    refuse = MagicMock()
    monkeypatch.setattr(odoo_client, "refuse_leave", refuse)

    resp = exceptions_route._approve_time_off_sync(71, source="inbox")

    assert resp.status_code == 500
    err = _body(resp)["error"]
    assert "<Fault" not in err
    assert "Some other Odoo problem" in err
    refuse.assert_not_called()


def test_refuse_surfaces_clean_message_when_odoo_rejects(monkeypatch):
    from zira_dashboard import odoo_client

    row = {
        "id": 72, "person_odoo_id": 8, "person_name": "Carlos Ortega",
        "leave_type": "Unpaid", "date_from": date(2026, 6, 27),
        "date_to": date(2026, 6, 27), "state": "confirm", "odoo_leave_id": 89,
    }
    monkeypatch.setattr(exceptions_route, "_load_time_off_request", lambda rid: row)

    def _raise(_leave_id):
        raise xmlrpc.client.Fault(3, "Some other Odoo problem")

    monkeypatch.setattr(odoo_client, "refuse_leave", _raise)

    resp = exceptions_route._refuse_time_off_sync(72, reason="No coverage", source="inbox")

    assert resp.status_code == 500
    err = _body(resp)["error"]
    assert "<Fault" not in err
    assert "Some other Odoo problem" in err


def test_approve_fallback_survives_suppression_failure(monkeypatch):
    """The suppression insert is belt-and-braces, not load-bearing: a DB
    blip there must not abort the local recording."""
    events: list = []
    row = _fallback_row()
    decision, _inbox_event = _wire_fallback(monkeypatch, row, events)

    from zira_dashboard import employee_notifications

    def _suppress_boom(_pid, _req, kind):
        raise RuntimeError("employee_notifications unavailable")

    monkeypatch.setattr(employee_notifications, "suppress_resolution",
                        _suppress_boom)

    resp = exceptions_route._approve_time_off_sync(71, source="inbox")

    assert resp.status_code == 200
    assert _body(resp)["recorded_locally"] is True
    decision.assert_called_once()


def test_refuse_local_record_row_settles_locally_without_odoo(monkeypatch):
    """Denying a locally-recorded approval must not call action_refuse on
    the already-refused Odoo copy (Odoo raises) — it settles locally,
    clearing local_record so Odoo owns the (now agreeing) row again."""
    from unittest.mock import MagicMock

    from zira_dashboard import db, odoo_client, time_off_sync

    row = dict(_fallback_row(), state="validate", local_record=True)
    monkeypatch.setattr(exceptions_route, "_load_time_off_request", lambda rid: row)
    refuse = MagicMock()
    monkeypatch.setattr(odoo_client, "refuse_leave", refuse)
    chatter = MagicMock()
    monkeypatch.setattr(odoo_client, "post_leave_message", chatter)
    executed: list = []
    monkeypatch.setattr(db, "execute",
                        lambda sql, params=None: executed.append((sql, params)))
    monkeypatch.setattr(time_off_sync, "cascade_on_state_change",
                        lambda old, new: None)
    monkeypatch.setattr(exceptions_route, "_refresh_time_off_surfaces", lambda: None)
    monkeypatch.setattr(exceptions_route.time_off_audit, "record_decision", MagicMock())
    monkeypatch.setattr(exceptions_route.inbox_log, "log_event_safe", MagicMock())

    resp = exceptions_route._refuse_time_off_sync(
        71, reason="Recorded by mistake", source="inbox")

    assert resp.status_code == 200
    assert _body(resp)["ok"] is True
    refuse.assert_not_called()
    # Deny reason still lands on the Odoo copy's chatter (best-effort).
    chatter.assert_called_once()
    settles = [e for e in executed if "state = %s" in e[0] or "'refuse'" in e[0]]
    assert settles, "expected the local settle UPDATE"


def test_set_time_off_state_clears_local_record(monkeypatch):
    """Once a manager settles a row through a route, Odoo and the mirror
    agree again — the poller may resume ownership."""
    from zira_dashboard import db, time_off_sync

    executed: list = []
    monkeypatch.setattr(db, "execute",
                        lambda sql, params=None: executed.append((sql, params)))
    monkeypatch.setattr(time_off_sync, "cascade_on_state_change",
                        lambda old, new: None)
    monkeypatch.setattr(exceptions_route, "_refresh_time_off_surfaces", lambda: None)

    exceptions_route._set_time_off_state(
        {"id": 71, "state": "validate", "local_record": True}, "refuse")

    assert len(executed) == 1
    assert "local_record = FALSE" in executed[0][0]


def test_load_time_off_request_selects_local_record(monkeypatch):
    from zira_dashboard import db

    seen = {}

    def fake_query(sql, params=None):
        seen["sql"] = sql
        return []

    monkeypatch.setattr(db, "query", fake_query)
    exceptions_route._load_time_off_request(71)
    assert "local_record" in seen["sql"]
