"""Postgres-gated tests for widget_definitions_store."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="widget_definitions_store tests need Postgres",
)


@pytest.fixture(autouse=True)
def _clean():
    from zira_dashboard import db
    db.init_pool()
    db.bootstrap_schema()
    db.execute("DELETE FROM widget_definitions WHERE name LIKE 'wt-%'")
    yield
    db.execute("DELETE FROM widget_definitions WHERE name LIKE 'wt-%'")


def test_save_inserts_and_returns_row():
    from zira_dashboard import widget_definitions_store
    row = widget_definitions_store.save(
        name="wt-pallets", type="pallets_by_wc",
        visual={"color": "#22c55e", "sort": "desc"},
        default_data={"group": "Repairs"},
    )
    assert isinstance(row["id"], int)
    assert row["name"] == "wt-pallets"
    assert row["type"] == "pallets_by_wc"
    assert row["visual"] == {"color": "#22c55e", "sort": "desc"}
    assert row["default_data"] == {"group": "Repairs"}


def test_save_with_id_updates():
    from zira_dashboard import widget_definitions_store
    row = widget_definitions_store.save(
        name="wt-edit", type="goat_race", visual={}, default_data={"group": "Repairs"},
    )
    updated = widget_definitions_store.save(
        name="wt-edit-renamed", type="goat_race", visual={"color": "#ff0000"},
        default_data={"group": "Dismantlers"}, id=row["id"],
    )
    assert updated["id"] == row["id"]
    assert updated["name"] == "wt-edit-renamed"
    assert updated["visual"] == {"color": "#ff0000"}
    assert updated["default_data"] == {"group": "Dismantlers"}


def test_get_returns_row_or_none():
    from zira_dashboard import widget_definitions_store
    assert widget_definitions_store.get(999_999_999) is None
    row = widget_definitions_store.save(
        name="wt-get", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    fetched = widget_definitions_store.get(row["id"])
    assert fetched["id"] == row["id"]
    assert fetched["name"] == "wt-get"


def test_list_definitions_ordered_by_type_then_name():
    from zira_dashboard import widget_definitions_store
    widget_definitions_store.save(name="wt-z-pal", type="pallets_by_wc", visual={}, default_data={"group": "Repairs"})
    widget_definitions_store.save(name="wt-a-rib", type="ribbons", visual={}, default_data={"group": "Repairs"})
    widget_definitions_store.save(name="wt-a-pal", type="pallets_by_wc", visual={}, default_data={"group": "Repairs"})
    rows = [r for r in widget_definitions_store.list_definitions() if r["name"].startswith("wt-")]
    types_in_order = [r["type"] for r in rows]
    assert types_in_order[0] == "pallets_by_wc"
    names = [r["name"] for r in rows if r["type"] == "pallets_by_wc"]
    assert names == sorted(names, key=str.lower)


def test_delete_removes_row():
    from zira_dashboard import widget_definitions_store
    row = widget_definitions_store.save(
        name="wt-del", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    widget_definitions_store.delete(row["id"])
    assert widget_definitions_store.get(row["id"]) is None


def test_delete_raises_when_referenced():
    """Cannot delete a definition that any dashboard_widgets row references.
    Depends on custom_dashboards_store (Task 4) — will fail until that ships."""
    from zira_dashboard import widget_definitions_store, custom_dashboards_store, db
    wd = widget_definitions_store.save(
        name="wt-referenced", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    dash = custom_dashboards_store.save_dashboard(
        name="wt-host-dash", scope_kind="group", scope_value="Repairs", theme="dark",
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"],
        x=0, y=0, w=4, h=4, data_overrides={},
    )
    with pytest.raises(Exception):
        widget_definitions_store.delete(wd["id"])
    db.execute("DELETE FROM custom_dashboards WHERE slug LIKE 'wt-%'")


def test_usage_count():
    """Depends on custom_dashboards_store (Task 4)."""
    from zira_dashboard import widget_definitions_store, custom_dashboards_store, db
    wd = widget_definitions_store.save(
        name="wt-usage", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    assert widget_definitions_store.usage_count(wd["id"]) == 0
    dash = custom_dashboards_store.save_dashboard(
        name="wt-usage-dash", scope_kind="group", scope_value="Repairs", theme="dark",
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=4, y=0, w=4, h=4, data_overrides={},
    )
    assert widget_definitions_store.usage_count(wd["id"]) == 2
    db.execute("DELETE FROM custom_dashboards WHERE slug LIKE 'wt-%'")
