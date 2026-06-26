"""Undo: load + mark-undone helpers and the reverse endpoint."""
import os

import pytest

from zira_dashboard import db, inbox_log

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs Postgres")

KP = "test:undo:"


@pytest.fixture(autouse=True)
def _clean():
    db.bootstrap_schema()
    db.execute("DELETE FROM inbox_events WHERE item_key LIKE %s", (KP + "%",))
    yield
    db.execute("DELETE FROM inbox_events WHERE item_key LIKE %s", (KP + "%",))


def test_get_event_and_mark_undone():
    eid = inbox_log.record_event(
        item_kind="missing_wc", item_key=KP + "1", person_name="Maria",
        category_label="Missing WC", action="dismiss",
        actor_upn="dale@gruberpallets.com", actor_name="Dale", reversible=True)
    ev = inbox_log.get_event(eid)
    assert ev is not None
    assert ev["item_key"] == KP + "1"
    assert ev["action"] == "dismiss"
    assert ev["undone_at"] is None

    undo_id = inbox_log.record_event(
        item_kind="missing_wc", item_key=KP + "1", person_name="Maria",
        category_label="Missing WC", action="undo", actor_upn="dale@gruberpallets.com",
        actor_name="Dale")
    inbox_log.mark_undone(eid, undo_id)
    ev2 = inbox_log.get_event(eid)
    assert ev2["undone_at"] is not None
    assert ev2["undo_event_id"] == undo_id


def test_get_event_missing_returns_none():
    assert inbox_log.get_event(-1) is None
