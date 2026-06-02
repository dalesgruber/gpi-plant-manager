"""Department-transfer decision logic. staffing.load_roster and odoo_client are
stubbed; no DB / Odoo needed."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from zira_dashboard import staffing, staffing_transfer, odoo_client


WIN_START = datetime(2026, 6, 2, 13, 0, tzinfo=timezone.utc)


@pytest.fixture
def roster(monkeypatch):
    people = [
        staffing.Person(name="Lauro", employee_id=5),
        staffing.Person(name="Legacy Lou", employee_id=None),
    ]
    monkeypatch.setattr(staffing, "load_roster", lambda: people)
    return people


def test_skips_when_no_employee_id(roster, monkeypatch):
    monkeypatch.setattr(odoo_client, "get_current_attendance",
                        lambda eid: (_ for _ in ()).throw(AssertionError("must not call Odoo")))
    out = staffing_transfer.decide_and_apply("Legacy Lou", "Junior #2", WIN_START)
    assert out["transfer"] == "skipped_no_employee"


def test_no_op_when_already_in_dept(roster, monkeypatch):
    monkeypatch.setattr(odoo_client, "_department_id_for_wc", lambda wc: 9)
    monkeypatch.setattr(odoo_client, "get_current_attendance", lambda eid: {
        "id": 1, "check_in": "2026-06-02 12:00:00",
        "department_id": 9, "department_name": "07 New",
    })
    transfer_called = {"n": 0}
    monkeypatch.setattr(odoo_client, "transfer",
                        lambda *a, **k: transfer_called.__setitem__("n", 1) or (1, 2))
    out = staffing_transfer.decide_and_apply("Lauro", "Junior #2", WIN_START)
    assert out["transfer"] == "already_in_dept"
    assert transfer_called["n"] == 0


def test_transfers_when_dept_differs(roster, monkeypatch):
    monkeypatch.setattr(odoo_client, "_department_id_for_wc", lambda wc: 9)
    monkeypatch.setattr(odoo_client, "get_current_attendance", lambda eid: {
        "id": 1, "check_in": "2026-06-02 12:00:00",
        "department_id": 3, "department_name": "01 Recycled",
    })
    captured = {}
    def fake_transfer(eid, wc, ts):
        captured.update(eid=eid, wc=wc, ts=ts)
        return (100, 200)
    monkeypatch.setattr(odoo_client, "transfer", fake_transfer)

    out = staffing_transfer.decide_and_apply("Lauro", "Junior #2", WIN_START)

    assert out["transfer"] == "moved"
    assert out["closed_id"] == 100 and out["new_id"] == 200
    assert out["from_dept"] == "01 Recycled"
    assert out["to_dept"] == "New"  # Junior #2's Location.department
    assert captured["ts"] == WIN_START


def test_transfer_ts_clamps_to_checkin(roster, monkeypatch):
    monkeypatch.setattr(odoo_client, "_department_id_for_wc", lambda wc: 9)
    monkeypatch.setattr(odoo_client, "get_current_attendance", lambda eid: {
        "id": 1, "check_in": "2026-06-02 14:00:00",  # AFTER window start
        "department_id": 3, "department_name": "01 Recycled",
    })
    captured = {}
    monkeypatch.setattr(odoo_client, "transfer",
                        lambda eid, wc, ts: captured.update(ts=ts) or (1, 2))
    staffing_transfer.decide_and_apply("Lauro", "Junior #2", WIN_START)
    assert captured["ts"] == datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)


def test_opens_new_punch_when_none(roster, monkeypatch):
    monkeypatch.setattr(odoo_client, "_department_id_for_wc", lambda wc: 9)
    monkeypatch.setattr(odoo_client, "get_current_attendance", lambda eid: None)
    captured = {}
    monkeypatch.setattr(odoo_client, "clock_in",
                        lambda eid, wc, ts: captured.update(eid=eid, wc=wc, ts=ts) or 300)
    out = staffing_transfer.decide_and_apply("Lauro", "Junior #2", WIN_START)
    assert out["transfer"] == "opened"
    assert out["new_id"] == 300
    assert captured["ts"] == WIN_START
