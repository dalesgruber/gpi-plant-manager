from datetime import date

from fastapi.testclient import TestClient

from zira_dashboard.app import app
from zira_dashboard.routes import time_off_approvals as page


def test_pending_payload_attaches_balance_and_coverage(monkeypatch):
    monkeypatch.setattr(page, "_pending_rows", lambda today: [{
        "id": 55,
        "person_odoo_id": 7,
        "person_name": "Maria Delgado",
        "leave_type": "PTO",
        "holiday_status_id": 3,
        "date_from": date(2026, 6, 30),
        "date_to": date(2026, 7, 2),
        "hour_from": None,
        "hour_to": None,
        "state": "confirm",
    }])
    monkeypatch.setattr(
        page.time_off_context,
        "balance_for",
        lambda pid, hsid: {"remaining": 24.0, "unit": "days"},
    )
    monkeypatch.setattr(
        page.time_off_context,
        "coverage_for",
        lambda pid, df, dt: {"count": 2, "scope": "department"},
    )

    rows = page._pending_payload(date(2026, 6, 24))

    assert len(rows) == 1
    r = rows[0]
    assert r["person_name"] == "Maria Delgado"
    assert r["balance"] == {"remaining": 24.0, "unit": "days"}
    assert r["coverage"] == {"count": 2, "scope": "department"}
    assert r["over_balance"] is False
    assert r["past_due"] is False


def test_pending_payload_flags_over_balance_and_past_due(monkeypatch):
    monkeypatch.setattr(page, "_pending_rows", lambda today: [{
        "id": 56,
        "person_odoo_id": 8,
        "person_name": "Juan Morales",
        "leave_type": "Sick",
        "holiday_status_id": 4,
        "date_from": date(2026, 6, 20),
        "date_to": date(2026, 6, 20),
        "hour_from": 8.0,
        "hour_to": 12.0,
        "state": "confirm",
    }])
    monkeypatch.setattr(
        page.time_off_context,
        "balance_for",
        lambda pid, hsid: {"remaining": 2.0, "unit": "hours"},
    )
    monkeypatch.setattr(
        page.time_off_context,
        "coverage_for",
        lambda pid, df, dt: {"count": 0, "scope": "department"},
    )

    rows = page._pending_payload(date(2026, 6, 24))

    assert rows[0]["over_balance"] is True
    assert rows[0]["past_due"] is True


def test_approvals_page_renders_200(monkeypatch):
    monkeypatch.setattr(page, "_pending_payload", lambda today: [])
    monkeypatch.setattr(page.time_off_audit, "recent_decisions", lambda days=30: [])
    client = TestClient(app)

    resp = client.get("/staffing/time-off/approvals")

    assert resp.status_code == 200
    assert "Time off approvals" in resp.text


def test_approvals_page_renders_pending_context_and_recent_decisions(monkeypatch):
    monkeypatch.setattr(page, "_pending_payload", lambda today: [{
        "id": 55,
        "person_name": "Maria Delgado",
        "leave_type": "PTO",
        "date_from": date(2026, 6, 30),
        "date_to": date(2026, 7, 2),
        "balance": {"remaining": 24.0, "unit": "hours"},
        "coverage": {"count": 2, "scope": "department"},
        "over_balance": False,
        "past_due": False,
        "awaiting_second": True,
    }])
    monkeypatch.setattr(page.time_off_audit, "recent_decisions", lambda days=30: [{
        "person_name": "Juan Morales",
        "action": "deny",
        "leave_type": "Sick",
        "date_from": date(2026, 6, 20),
        "date_to": date(2026, 6, 20),
        "reason": "Coverage too thin",
        "actor_name": "Dale Gruber",
        "actor_upn": "dale@gruberpallets.com",
    }])
    client = TestClient(app)

    resp = client.get("/staffing/time-off/approvals")

    assert resp.status_code == 200
    assert "Maria Delgado" in resp.text
    assert "24 hours left" in resp.text
    assert "2 off" in resp.text
    assert "Awaiting 2nd approval" in resp.text
    assert "Juan Morales" in resp.text
    assert "Coverage too thin" in resp.text
    assert "/static/time_off_approvals.js" in resp.text
