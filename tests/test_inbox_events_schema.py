"""inbox_events: the unified Exception Inbox activity log table (Postgres)."""
import os

import pytest

from zira_dashboard import db

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres"
)

KEY = "test:schema:inbox_events"


@pytest.fixture(autouse=True)
def _clean():
    db.bootstrap_schema()
    db.execute("DELETE FROM inbox_events WHERE item_key = %s", (KEY,))
    yield
    db.execute("DELETE FROM inbox_events WHERE item_key = %s", (KEY,))


def test_inbox_events_table_round_trips():
    db.execute(
        "INSERT INTO inbox_events "
        "(item_kind, item_key, person_name, category_label, action, actor_upn) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        ("time_off", KEY, "Maria", "Time off", "approve", "dale@gruberpallets.com"),
    )
    rows = db.query(
        "SELECT item_kind, action, actor_upn, reversible, undone_at, resolved_at "
        "FROM inbox_events WHERE item_key = %s",
        (KEY,),
    )
    assert rows and rows[0]["item_kind"] == "time_off"
    assert rows[0]["action"] == "approve"
    assert rows[0]["reversible"] is False  # column default
    assert rows[0]["undone_at"] is None
    assert rows[0]["resolved_at"] is not None
