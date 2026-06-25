"""DB-free unit tests for feedback_store helpers."""

from zira_dashboard import feedback_store


def test_insert_passes_all_columns(monkeypatch):
    seen = {}

    def fake_cursor():
        class _Cur:
            def __enter__(self_):
                return self_
            def __exit__(self_, *a):
                return False
            def execute(self_, sql, params):
                seen["sql"] = sql
                seen["params"] = params
            def fetchone(self_):
                return {"id": 42}
        return _Cur()

    monkeypatch.setattr(feedback_store.db, "cursor", fake_cursor)

    new_id = feedback_store.insert(
        message="hi",
        submitter="dale@x.com",
        page_url="/p",
        task_type="bug",
        odoo_task_id=7,
    )

    assert new_id == 42
    assert seen["params"] == ("dale@x.com", "/p", "bug", 7, "hi")


def test_for_submitter_clamps_limit_and_filters(monkeypatch):
    seen = []

    def fake_query(sql, params):
        seen.append((sql, params))
        return []

    monkeypatch.setattr(feedback_store.db, "query", fake_query)

    feedback_store.for_submitter("dale@x.com", limit=0)
    feedback_store.for_submitter("dale@x.com", limit=9999)

    assert "WHERE submitter = %s" in seen[0][0]
    assert seen[0][1] == ("dale@x.com", 1)
    assert seen[1][1] == ("dale@x.com", 500)


def test_for_submitter_uses_default_limit_for_invalid_values(monkeypatch):
    seen = []
    monkeypatch.setattr(
        feedback_store.db, "query",
        lambda sql, params: seen.append((sql, params)) or [],
    )
    feedback_store.for_submitter("dale@x.com", limit="nope")
    assert seen[0][1] == ("dale@x.com", 100)
