"""inbox_log: write/read the Exception Inbox activity log + best-effort wrapper."""
import os

import pytest

from zira_dashboard import db, inbox_log

_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs Postgres")

KEY = "test:inbox-log:1"


@pytest.fixture(autouse=True)
def _clean():
    if os.environ.get("DATABASE_URL"):
        db.bootstrap_schema()
        db.execute("DELETE FROM inbox_events WHERE item_key = %s", (KEY,))
    yield
    if os.environ.get("DATABASE_URL"):
        db.execute("DELETE FROM inbox_events WHERE item_key = %s", (KEY,))


@_db
def test_record_event_round_trips_with_actor():
    eid = inbox_log.record_event(
        item_kind="missing_wc",
        item_key=KEY,
        person_name="Maria",
        category_label="Missing WC",
        action="assign",
        outcome="Assigned to Saw 1",
        after_value="Saw 1",
        actor_upn="dale@gruberpallets.com",
        actor_name="Dale Gruber",
        source="inbox",
        reversible=True,
    )
    assert isinstance(eid, int)
    rows = [r for r in inbox_log.recent_events(days=1) if r["item_key"] == KEY]
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "assign"
    assert r["after_value"] == "Saw 1"
    assert r["actor_upn"] == "dale@gruberpallets.com"
    assert r["reversible"] is True
    assert r["undone_at"] is None


@_db
def test_record_event_allows_null_actor_for_auto_resolved():
    inbox_log.record_event(
        item_kind="late",
        item_key=KEY,
        person_name="Tomas",
        category_label="Late",
        action="auto_resolved",
        outcome="Auto-resolved",
        actor_upn=None,
        actor_name=None,
        source="auto",
    )
    rows = [r for r in inbox_log.recent_events(days=1) if r["item_key"] == KEY]
    assert rows[0]["actor_upn"] is None


def test_log_event_safe_swallows_errors(monkeypatch):
    def boom(**kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(inbox_log, "record_event", boom)
    # Best-effort: a logging failure must never raise into the caller.
    result = inbox_log.log_event_safe(
        item_kind="missing_wc",
        item_key="x",
        person_name=None,
        category_label="Missing WC",
        action="assign",
    )
    assert result is None
