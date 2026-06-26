"""Assignment credit from the inbox logs an inbox_event; staffing-page credits don't.

Collaborators are monkeypatched, so no Postgres/Odoo needed. Verifies the
`source == 'inbox'` gate on POST /api/staffing/attribute (the credit endpoint is
shared with the staffing page, which must NOT write inbox audit rows).
"""
import pytest
from fastapi.testclient import TestClient

from zira_dashboard import inbox_log, staffing_transfer, wc_attributions
from zira_dashboard.app import app
from zira_dashboard.routes import staffing as staffing_route

client = TestClient(app)


@pytest.fixture
def events(monkeypatch):
    captured = []
    monkeypatch.setattr(wc_attributions, "add", lambda *a, **k: 1)
    monkeypatch.setattr(staffing_transfer, "decide_and_apply", lambda *a, **k: {})
    monkeypatch.setattr(staffing_route, "invalidate_today_cache", lambda *a, **k: None)
    monkeypatch.setattr(staffing_route, "_bust_assignments_todo_cache", lambda *a, **k: None)
    monkeypatch.setattr(inbox_log, "log_event_safe", lambda **kw: captured.append(kw) or 1)
    return captured


def _post(source=None):
    body = {
        "day": "2026-06-26",
        "wc_name": "Saw 1",
        "person_name": "Maria",
        "start_utc": "2026-06-26T13:00:00",
    }
    if source:
        body["source"] = source
    return client.post("/api/staffing/attribute", json=body)


def test_inbox_credit_logs_assignment_event(events):
    r = _post(source="inbox")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(events) == 1
    e = events[0]
    assert e["item_kind"] == "assignment"
    assert e["item_key"] == "assignment:Saw 1:2026-06-26T13:00:00"
    assert e["action"] == "assign"
    assert e["after_value"] == "Maria"


def test_staffing_page_credit_logs_nothing(events):
    r = _post()  # no source -> not an inbox action
    assert r.status_code == 200 and r.json()["ok"] is True
    assert events == []
