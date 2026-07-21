"""DB-free unit tests for forklift_store's pure parsing helpers. Unlike
tests/test_forklift_store.py (whole-module, DATABASE_URL-gated), these exercise
the JSONB-parsing logic directly so they run everywhere."""
from zira_dashboard import forklift_store


def test_recent_claim_seconds_calls_weighted_mean(monkeypatch):
    from zira_dashboard import db
    monkeypatch.setattr(db, "query", lambda *a, **k: [{"wms": 3_000_000, "calls": 20}])
    assert forklift_store.recent_claim_seconds(90) == 150.0


def test_recent_claim_seconds_none_on_no_calls(monkeypatch):
    from zira_dashboard import db
    monkeypatch.setattr(db, "query", lambda *a, **k: [{"wms": 0, "calls": 0}])
    assert forklift_store.recent_claim_seconds(90) is None
