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


def test_insert_then_for_submitter_round_trip():
    new_id = feedback_store.insert(
        message="Round-trip test message",
        submitter="tester@gruberpallets.com",
        page_url="/recycling",
        task_type="bug",
        odoo_task_id=999001,
    )
    assert isinstance(new_id, int)
    rows = feedback_store.for_submitter("tester@gruberpallets.com", limit=50)
    match = next((r for r in rows if r["id"] == new_id), None)
    assert match is not None
    assert match["message"] == "Round-trip test message"
    assert match["task_type"] == "bug"
    assert match["odoo_task_id"] == 999001
    db.execute("DELETE FROM feedback WHERE id = %s", (new_id,))
