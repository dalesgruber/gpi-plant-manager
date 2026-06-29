"""Endpoint test for GET /staffing/forklift (forklift leaderboard page)."""
from __future__ import annotations


def _client(monkeypatch):
    """Stub forklift_awards.leaderboard so the page renders without DB."""
    from fastapi.testclient import TestClient
    from zira_dashboard import forklift_awards, forklift_settings
    from zira_dashboard.app import app

    # current() reads the DB; pin it to defaults so the render path is exercised.
    monkeypatch.setattr(forklift_settings, "current", lambda: forklift_settings.DEFAULT)
    monkeypatch.setattr(forklift_awards, "leaderboard", lambda *a, **k: {
        "overall": [{"name": "Trent", "driver_id": "d1", "score": 86.0,
                     "days": 12, "calls": 513}],
        "most_calls": [{"name": "Trent", "driver_id": "d1", "calls": 513,
                        "on_time": 500, "late": 13, "ontime_pct": 97.5,
                        "avg_ms": 42000}],
        "on_time": [{"name": "Isidro", "driver_id": "d2", "calls": 471,
                     "ontime_pct": 98.5, "on_time": 464, "late": 7,
                     "avg_ms": 51000}],
        "fastest": [{"name": "Trent", "driver_id": "d1", "calls": 513,
                     "avg_ms": 42000, "ontime_pct": 97.5, "on_time": 500,
                     "late": 13}],
    })
    return TestClient(app)


def test_forklift_leaderboard_renders_four_cards(monkeypatch):
    page = _client(monkeypatch).get("/staffing/forklift").text
    assert "Overall score" in page and "Most calls" in page
    assert "On-time" in page and "Fastest" in page
    assert "Trent" in page


def test_forklift_leaderboard_degrades_on_failure(monkeypatch):
    """A store/compute failure must degrade to an empty page, never a 500."""
    from fastapi.testclient import TestClient
    from zira_dashboard import forklift_awards
    from zira_dashboard.app import app

    def _boom(*a, **k):
        raise RuntimeError("store down")

    # The route must tolerate a raised leaderboard/settings/score-config call.
    monkeypatch.setattr(forklift_awards, "leaderboard", _boom)
    r = TestClient(app).get("/staffing/forklift")
    assert r.status_code == 200
    assert "Overall score" in r.text  # cards still render, just empty
