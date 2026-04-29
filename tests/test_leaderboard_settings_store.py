import os
import pytest

from zira_dashboard import db, leaderboard_settings_store as store


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)


@pytest.fixture(autouse=True)
def _clean():
    db.execute("DELETE FROM leaderboard_wc_settings WHERE wc_name LIKE 'TestWC%'")
    yield
    db.execute("DELETE FROM leaderboard_wc_settings WHERE wc_name LIKE 'TestWC%'")


def test_snapshot_empty_returns_empty_dict():
    snap = store.snapshot()
    # filter to test rows so other data in the table doesn't matter
    test_rows = {k: v for k, v in snap.items() if k.startswith("TestWC")}
    assert test_rows == {}


def test_set_order_assigns_left_to_right_indexes():
    store.set_order(["TestWCa", "TestWCb", "TestWCc"])
    snap = store.snapshot()
    assert snap["TestWCa"]["sort_order"] == 0
    assert snap["TestWCb"]["sort_order"] == 1
    assert snap["TestWCc"]["sort_order"] == 2
    # default is_inactive is False
    assert snap["TestWCa"]["is_inactive"] is False
    assert snap["TestWCb"]["is_inactive"] is False
    assert snap["TestWCc"]["is_inactive"] is False


def test_set_order_preserves_is_inactive_flag():
    store.set_inactive("TestWCx", True)
    store.set_order(["TestWCx", "TestWCy"])
    snap = store.snapshot()
    assert snap["TestWCx"]["sort_order"] == 0
    assert snap["TestWCx"]["is_inactive"] is True  # preserved across set_order
    assert snap["TestWCy"]["sort_order"] == 1
    assert snap["TestWCy"]["is_inactive"] is False


def test_set_inactive_preserves_sort_order():
    store.set_order(["TestWCp", "TestWCq"])
    store.set_inactive("TestWCp", True)
    snap = store.snapshot()
    assert snap["TestWCp"]["sort_order"] == 0  # preserved across set_inactive
    assert snap["TestWCp"]["is_inactive"] is True
    # toggle back off
    store.set_inactive("TestWCp", False)
    snap = store.snapshot()
    assert snap["TestWCp"]["sort_order"] == 0
    assert snap["TestWCp"]["is_inactive"] is False


def test_set_order_skips_blank_and_non_string_entries():
    # mix of valid + blank/whitespace entries; non-string values are skipped.
    store.set_order(["TestWCm", "", "   ", "TestWCn"])
    snap = store.snapshot()
    assert snap["TestWCm"]["sort_order"] == 0
    assert snap["TestWCn"]["sort_order"] == 3
    # blank entries did not produce rows
    assert "" not in snap
    assert "   " not in snap
