"""Round-trip test for feedback_store (needs Postgres)."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres"
)

from zira_dashboard import db, feedback_store


@pytest.fixture(autouse=True)
def _schema():
    db.init_pool()
    db.bootstrap_schema()
    yield


def test_insert_then_recent_round_trip():
    new_id = feedback_store.insert(
        message="Round-trip test message",
        submitter="tester@gruberpallets.com",
        page_url="/recycling",
        category="Idea",
    )
    assert isinstance(new_id, int)
    rows = feedback_store.recent(limit=50)
    match = next((r for r in rows if r["id"] == new_id), None)
    assert match is not None
    assert match["message"] == "Round-trip test message"
    assert match["submitter"] == "tester@gruberpallets.com"
    assert match["category"] == "Idea"
    db.execute("DELETE FROM feedback WHERE id = %s", (new_id,))
