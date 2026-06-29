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
    from zira_dashboard import _http_cache, awards, production_history, work_centers_store
    from zira_dashboard.app import app
    monkeypatch.setattr(awards, "monthly_badges", lambda *a, **k: [])
    monkeypatch.setattr(awards, "annual_top_days", lambda *a, **k: [])
    monkeypatch.setattr(awards, "annual_best_avg_group", lambda *a, **k: None)
    monkeypatch.setattr(awards, "annual_best_avg_wc", lambda *a, **k: None)
    monkeypatch.setattr(awards, "goat", lambda *a, **k: None)
    monkeypatch.setattr(awards, "_load_overrides", lambda: [])
    monkeypatch.setattr(production_history, "daily_records", lambda *a, **k: [])
    monkeypatch.setattr(work_centers_store, "registered_groups", lambda: ["Repairs"])
    monkeypatch.setattr(work_centers_store, "members", lambda *a, **k: [])
    # The page is served from the response cache on repeat renders — start
    # each test cold so the stubs above are what actually renders.
    _http_cache.invalidate_all_cache()
    return TestClient(app)


def test_trophies_page_renders_with_no_data(monkeypatch):
    """Empty data: page returns 200 and the body mentions 'Trophy'."""
    r = _client(monkeypatch).get("/trophies")
    assert r.status_code == 200
    assert "Trophy" in r.text


def test_override_endpoint_replace(monkeypatch):
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


# ---- Task 12: forklift section in the shared trophy case ----------------

def _forklift_client(monkeypatch):
    """Stub both production awards and forklift awards so /trophies renders
    the forklift section without DB."""
    import datetime as _dt

    from fastapi.testclient import TestClient
    from zira_dashboard import (
        _http_cache, awards, forklift_awards as fa, forklift_settings,
        production_history, work_centers_store,
    )
    from zira_dashboard.app import app

    # Production side: empty so only the forklift section carries data.
    monkeypatch.setattr(awards, "monthly_badges", lambda *a, **k: [])
    monkeypatch.setattr(awards, "annual_top_days", lambda *a, **k: [])
    monkeypatch.setattr(awards, "annual_best_avg_group", lambda *a, **k: None)
    monkeypatch.setattr(awards, "annual_best_avg_wc", lambda *a, **k: None)
    monkeypatch.setattr(awards, "goat", lambda *a, **k: None)
    monkeypatch.setattr(awards, "_load_overrides", lambda: [])
    monkeypatch.setattr(production_history, "daily_records", lambda *a, **k: [])
    monkeypatch.setattr(work_centers_store, "registered_groups", lambda: ["Repairs"])
    monkeypatch.setattr(work_centers_store, "members", lambda *a, **k: [])

    # Forklift side.
    monkeypatch.setattr(forklift_settings, "current", lambda: forklift_settings.DEFAULT)
    monkeypatch.setattr(fa, "goat", lambda cfg=None: {
        "name": "Trent", "driver_id": "d1", "score": 86.0,
        "day": _dt.date(2026, 4, 14)})
    monkeypatch.setattr(fa, "annual_top_days", lambda y, cfg=None, n=3: [])
    monkeypatch.setattr(fa, "monthly_badges", lambda y, m, cfg=None, n=3: [])
    monkeypatch.setattr(fa, "annual_best_ontime", lambda y, min_calls=50: None)
    monkeypatch.setattr(fa, "annual_fastest", lambda y, min_calls=50: None)

    _http_cache.invalidate_all_cache()
    return TestClient(app)


def test_trophy_case_renders_forklift_section(monkeypatch):
    page = _forklift_client(monkeypatch).get("/trophies").text
    assert "Forklift" in page and "Trent" in page


def test_override_accepts_forklift_scope(monkeypatch):
    from fastapi.testclient import TestClient
    from zira_dashboard import db
    from zira_dashboard.app import app

    spy = MagicMock()
    monkeypatch.setattr(db, "execute", spy)
    r = TestClient(app).post("/api/awards/override", json={
        "scope": "forklift_goat", "action": "replace", "name": "Isidro"})
    assert r.status_code in (200, 303)
