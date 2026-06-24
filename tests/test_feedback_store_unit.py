"""DB-free unit tests for feedback_store helpers."""

from zira_dashboard import feedback_store


def test_recent_clamps_limit_before_query(monkeypatch):
    seen = []

    def fake_query(sql, params):
        seen.append((sql, params))
        return []

    monkeypatch.setattr(feedback_store.db, "query", fake_query)

    feedback_store.recent(limit=0)
    feedback_store.recent(limit=9999)

    assert seen[0][1] == (1,)
    assert seen[1][1] == (500,)


def test_recent_uses_default_limit_for_invalid_values(monkeypatch):
    seen = []

    def fake_query(sql, params):
        seen.append((sql, params))
        return []

    monkeypatch.setattr(feedback_store.db, "query", fake_query)

    feedback_store.recent(limit="not-a-number")

    assert seen[0][1] == (200,)
