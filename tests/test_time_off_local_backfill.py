"""time_off_local_backfill — replays locally-recorded absences
(time_off_requests.local_record) into Odoo once Odoo will accept them.

A local record exists because Odoo rejected validation (no working hours on
the day: a holiday record or a calendar gap). When the Odoo data is later
corrected, the reconciler drafts/confirms/approves the refused copy and
hands ownership back to the poller. Attempts are prediction-gated so a
doomed replay never leaves the Odoo copy churning or pending.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from zira_dashboard import time_off_local_backfill as bf


@pytest.fixture
def fake_db(monkeypatch):
    captured: dict = {"queries": [], "executes": []}

    def fake_query(sql, params=None):
        captured["queries"].append((sql, params))
        return captured.get("query_result", [])

    def fake_execute(sql, params=None):
        captured["executes"].append((sql, params))

    monkeypatch.setattr(bf.db, "query", fake_query)
    monkeypatch.setattr(bf.db, "execute", fake_execute)
    return captured


def _row(**over):
    row = {
        "id": 80, "person_odoo_id": 13, "shape": "full_day",
        "holiday_status_id": 78,
        "date_from": date(2026, 7, 3), "date_to": date(2026, 7, 3),
        "hour_from": None, "hour_to": None, "note": None,
        "odoo_leave_id": 112,
    }
    row.update(over)
    return row


def _wire_odoo(monkeypatch, *, employees=None, cal_hours=None, holidays=None,
               leave_state="refuse"):
    """Stub the odoo_client surface the reconciler reads. Returns the dict
    of action mocks (draft/approve/refuse/create) for assertions."""
    oc = bf.odoo_client
    monkeypatch.setattr(oc, "fetch_employees", lambda: employees if employees is not None else [
        {"id": 13, "name": "Gerardo Garcia", "resource_calendar_id": [1, "Plant 40 hours/week"]},
    ])
    monkeypatch.setattr(oc, "fetch_calendar_hours", lambda ids: cal_hours if cal_hours is not None else {
        1: {"0": ["07:00", "15:30"], "1": ["07:00", "15:30"], "2": ["07:00", "15:30"],
            "3": ["07:00", "15:30"], "4": ["07:00", "15:30"]},
    })
    monkeypatch.setattr(oc, "fetch_public_holidays", lambda s, e: holidays if holidays is not None else [])
    monkeypatch.setattr(oc, "fetch_leave_state", lambda lid: leave_state)
    mocks = {
        "draft": MagicMock(), "approve": MagicMock(return_value="validate"),
        "refuse": MagicMock(), "create": MagicMock(return_value=555),
    }
    monkeypatch.setattr(oc, "draft_leave", mocks["draft"])
    monkeypatch.setattr(oc, "approve_leave", mocks["approve"])
    monkeypatch.setattr(oc, "refuse_leave", mocks["refuse"])
    monkeypatch.setattr(oc, "create_leave", mocks["create"])
    return mocks


def test_run_once_no_rows_makes_no_odoo_calls(monkeypatch, fake_db):
    fetch = MagicMock()
    monkeypatch.setattr(bf.odoo_client, "fetch_employees", fetch)

    assert bf.run_once() == 0
    fetch.assert_not_called()


def test_holiday_on_the_day_blocks_the_attempt(monkeypatch, fake_db):
    fake_db["query_result"] = [_row()]
    mocks = _wire_odoo(monkeypatch, holidays=[
        # The exact prod shape: '4th of July' covering 2026-07-03 (UTC
        # datetimes; day-granularity blocking is intentionally conservative).
        {"id": 1, "name": "4th of July",
         "date_from": "2026-07-03 05:00:00", "date_to": "2026-07-04 04:59:59"},
    ])

    assert bf.run_once() == 0
    mocks["draft"].assert_not_called()
    mocks["approve"].assert_not_called()
    mocks["refuse"].assert_not_called()


def test_uncovered_weekday_blocks_the_attempt(monkeypatch, fake_db):
    # Saturday absence, Mon-Fri calendar: Odoo can never hold it.
    fake_db["query_result"] = [_row(date_from=date(2026, 7, 11),
                                    date_to=date(2026, 7, 11))]
    mocks = _wire_odoo(monkeypatch)

    assert bf.run_once() == 0
    mocks["draft"].assert_not_called()
    mocks["approve"].assert_not_called()


def test_multi_day_span_needs_only_one_working_day(monkeypatch, fake_db):
    # Fri..Sun span: Friday works -> Odoo computes >0 days -> attempt runs.
    fake_db["query_result"] = [_row(date_from=date(2026, 7, 10),
                                    date_to=date(2026, 7, 12))]
    mocks = _wire_odoo(monkeypatch)

    assert bf.run_once() == 1
    mocks["approve"].assert_called_once()


def test_replays_refused_leave_and_hands_ownership_back(monkeypatch, fake_db):
    fake_db["query_result"] = [_row()]
    mocks = _wire_odoo(monkeypatch, leave_state="refuse")
    unsuppress = MagicMock()
    monkeypatch.setattr(bf.employee_notifications, "unsuppress_resolution",
                        unsuppress)

    assert bf.run_once() == 1

    # refuse -> draft -> (confirm+approve via approve_leave)
    mocks["draft"].assert_called_once_with(112)
    mocks["approve"].assert_called_once_with(112)
    mocks["create"].assert_not_called()
    # Local row hands ownership back to the poller.
    clears = [e for e in fake_db["executes"]
              if "local_record = FALSE" in e[0]]
    assert len(clears) == 1
    assert "synced_to_odoo = TRUE" in clears[0][0]
    # The denied-popup suppression is retired: from here on the leave is a
    # normal Odoo-owned record and genuine resolutions must notify again.
    unsuppress.assert_called_once_with(80, "time_off_denied")


def test_failed_replay_resettles_the_odoo_copy(monkeypatch, fake_db):
    import xmlrpc.client
    fake_db["query_result"] = [_row()]
    mocks = _wire_odoo(monkeypatch)
    mocks["approve"].side_effect = xmlrpc.client.Fault(
        2, "The following employees are not supposed to work during that period:\n X")

    assert bf.run_once() == 0

    # The Odoo copy must not linger pending: re-refused.
    mocks["refuse"].assert_called_once_with(112)
    assert not [e for e in fake_db["executes"]
                if "local_record = FALSE" in e[0]]


def test_recreates_leave_when_odoo_copy_was_deleted(monkeypatch, fake_db):
    fake_db["query_result"] = [_row()]
    mocks = _wire_odoo(monkeypatch, leave_state=None)
    monkeypatch.setattr(bf.employee_notifications, "unsuppress_resolution",
                        MagicMock())

    assert bf.run_once() == 1

    mocks["create"].assert_called_once()
    mocks["draft"].assert_not_called()
    mocks["approve"].assert_called_once_with(555)
    # The new leave id is linked to the local row BEFORE the approval
    # workflow runs, so a concurrent poll tick maps the new leave onto this
    # row (still flagged) instead of inserting a duplicate mirror row.
    link = [e for e in fake_db["executes"] if "odoo_leave_id = %s" in e[0]]
    assert link and link[0][1][0] == 555


def test_adopts_leave_hr_already_validated(monkeypatch, fake_db):
    # HR fixed the data and re-approved the leave directly in Odoo — the
    # reconciler just adopts it without touching the workflow.
    fake_db["query_result"] = [_row()]
    mocks = _wire_odoo(monkeypatch, leave_state="validate")
    monkeypatch.setattr(bf.employee_notifications, "unsuppress_resolution",
                        MagicMock())

    assert bf.run_once() == 1
    mocks["draft"].assert_not_called()
    mocks["approve"].assert_not_called()
    assert [e for e in fake_db["executes"] if "local_record = FALSE" in e[0]]


def test_backfill_warmer_registered():
    from zira_dashboard import app as app_module
    entry = next(
        (e for e in app_module._WARMERS
         if e[0] == "time-off local backfill"), None)
    assert entry is not None, "backfill warmer not registered in _WARMERS"
    assert entry[2] >= 3600  # hourly at most — replays are deliberate, not hot
