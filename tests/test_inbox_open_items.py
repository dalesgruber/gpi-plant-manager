"""inbox_open_items mirror table + has_human_event_since (Postgres)."""
import os
from datetime import datetime, timedelta, timezone

import pytest

from zira_dashboard import db

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs Postgres")

KEY = "test:openitems:1"


@pytest.fixture(autouse=True)
def _clean():
    db.bootstrap_schema()
    db.execute("DELETE FROM inbox_open_items WHERE item_key = %s", (KEY,))
    yield
    db.execute("DELETE FROM inbox_open_items WHERE item_key = %s", (KEY,))


def test_inbox_open_items_round_trips():
    db.execute(
        "INSERT INTO inbox_open_items (item_key, item_kind, person_name, category_label, priority) "
        "VALUES (%s, %s, %s, %s, %s)",
        (KEY, "missing_wc", "Maria", "Missing WC", "urgent"),
    )
    rows = db.query(
        "SELECT item_kind, person_name, first_seen, last_seen FROM inbox_open_items WHERE item_key = %s",
        (KEY,),
    )
    assert rows and rows[0]["item_kind"] == "missing_wc"
    assert rows[0]["first_seen"] is not None and rows[0]["last_seen"] is not None
