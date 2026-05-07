"""Endpoint tests for /trophies and /api/awards/override."""
from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock

requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; this test needs Postgres.",
)


def _client(monkeypatch):
    """Stub awards.py so the trophy case page renders without DB."""
    from fastapi.testclient import TestClient
    from zira_dashboard import awards, work_centers_store
    from zira_dashboard.app import app
    monkeypatch.setattr(awards, "monthly_badges", lambda *a, **k: [])
    monkeypatch.setattr(awards, "annual_top_days", lambda *a, **k: [])
    monkeypatch.setattr(awards, "annual_best_avg_group", lambda *a, **k: None)
    monkeypatch.setattr(awards, "annual_best_avg_wc", lambda *a, **k: None)
    monkeypatch.setattr(awards, "goat", lambda *a, **k: None)
    monkeypatch.setattr(awards, "_load_overrides", lambda: [])
    monkeypatch.setattr(work_centers_store, "registered_groups", lambda: ["Repairs"])
    monkeypatch.setattr(work_centers_store, "members", lambda *a, **k: [])
    return TestClient(app)


def test_trophies_page_renders_with_no_data(monkeypatch):
    """Empty data: page returns 200 and the body mentions 'Trophy'."""
    r = _client(monkeypatch).get("/trophies")
    assert r.status_code == 200
    assert "Trophy" in r.text


def test_override_endpoint_replace(monkeypatch):
    from fastapi.testclient import TestClient
    from zira_dashboard import db, awards
    from zira_dashboard.app import app

    spy = MagicMock()
    monkeypatch.setattr(db, "execute", spy)

    client = TestClient(app)
    r = client.post(
        "/api/awards/override",
        json={
            "scope": "badge", "group_name": "Repairs",
            "year": 2026, "month": 4, "position": 1,
            "action": "replace", "name": "Replacement",
        },
    )
    assert r.status_code == 200
    spy.assert_called_once()
    sql = spy.call_args.args[0]
    assert "INSERT INTO award_overrides" in sql
    assert "ON CONFLICT" in sql


def test_override_endpoint_reset_deletes_row(monkeypatch):
    from fastapi.testclient import TestClient
    from zira_dashboard import db
    from zira_dashboard.app import app

    spy = MagicMock()
    monkeypatch.setattr(db, "execute", spy)

    client = TestClient(app)
    r = client.post(
        "/api/awards/override",
        json={
            "scope": "badge", "group_name": "Repairs",
            "year": 2026, "month": 4, "position": 1,
            "action": "reset",
        },
    )
    assert r.status_code == 200
    spy.assert_called_once()
    sql = spy.call_args.args[0]
    assert "DELETE FROM award_overrides" in sql


def test_override_endpoint_validates_scope(monkeypatch):
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app

    client = TestClient(app)
    r = client.post(
        "/api/awards/override",
        json={"scope": "not_a_real_scope", "position": 1, "action": "replace", "name": "X"},
    )
    assert r.status_code == 400


def test_override_endpoint_validates_action(monkeypatch):
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app

    client = TestClient(app)
    r = client.post(
        "/api/awards/override",
        json={"scope": "badge", "group_name": "Repairs", "year": 2026,
              "month": 4, "position": 1, "action": "garbage"},
    )
    assert r.status_code == 400
