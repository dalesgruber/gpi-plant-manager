"""Postgres-gated tests for custom_dashboards_store."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="custom_dashboards_store tests need Postgres",
)


@pytest.fixture(autouse=True)
def _clean():
    from zira_dashboard import db
    db.init_pool()
    db.bootstrap_schema()
    db.execute("DELETE FROM custom_dashboards WHERE slug LIKE 'cdt-%'")
    db.execute("DELETE FROM widget_definitions WHERE name LIKE 'cdt-%'")
    yield
    db.execute("DELETE FROM custom_dashboards WHERE slug LIKE 'cdt-%'")
    db.execute("DELETE FROM widget_definitions WHERE name LIKE 'cdt-%'")


def test_save_dashboard_returns_slug():
    from zira_dashboard import custom_dashboards_store
    row = custom_dashboards_store.save_dashboard(
        name="cdt-repair-1", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    assert row["slug"] == "cdt-repair-1"
    assert row["scope_kind"] == "wc"
    assert row["scope_value"] == "Repair 1"


def test_save_dashboard_slug_collision_suffix():
    from zira_dashboard import custom_dashboards_store
    a = custom_dashboards_store.save_dashboard(
        name="cdt-clash", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    b = custom_dashboards_store.save_dashboard(
        name="cdt-clash", scope_kind="wc", scope_value="Repair 2", theme="dark",
    )
    assert a["slug"] == "cdt-clash"
    assert b["slug"] == "cdt-clash-2"


def test_save_dashboard_with_id_updates():
    from zira_dashboard import custom_dashboards_store
    row = custom_dashboards_store.save_dashboard(
        name="cdt-rename", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    updated = custom_dashboards_store.save_dashboard(
        name="cdt-renamed", scope_kind="group", scope_value="Repairs", theme="light",
        id=row["id"],
    )
    assert updated["id"] == row["id"]
    assert updated["name"] == "cdt-renamed"
    assert updated["slug"] == "cdt-renamed"
    assert updated["scope_kind"] == "group"
    assert updated["theme"] == "light"


def test_get_dashboard_by_id_and_slug():
    from zira_dashboard import custom_dashboards_store
    row = custom_dashboards_store.save_dashboard(
        name="cdt-fetch", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    by_id = custom_dashboards_store.get_dashboard(row["id"])
    by_slug = custom_dashboards_store.get_dashboard("cdt-fetch")
    assert by_id["id"] == row["id"]
    assert by_slug["id"] == row["id"]


def test_list_dashboards_includes_widget_count():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-list", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    rows = [r for r in custom_dashboards_store.list_dashboards() if r["slug"].startswith("cdt-")]
    target = next(r for r in rows if r["slug"] == "cdt-list")
    assert target["widget_count"] == 1


def test_delete_dashboard_cascades_placements():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store, db
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-cascade", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-cwd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    custom_dashboards_store.delete_dashboard(dash["id"])
    rows = db.query(
        "SELECT COUNT(*) AS n FROM dashboard_widgets WHERE dashboard_id = %s",
        (dash["id"],),
    )
    assert int(rows[0]["n"]) == 0


def test_add_placement_returns_row():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-add", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-add-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    p = custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"],
        x=0, y=0, w=6, h=4, data_overrides={"group": "Dismantlers"},
    )
    assert isinstance(p["id"], int)
    assert p["x"] == 0
    assert p["w"] == 6
    assert p["data_overrides"] == {"group": "Dismantlers"}


def test_list_placements_joins_definition():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-listp", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-listp-wd", type="goat_race", visual={"color": "#22c55e"},
        default_data={"group": "Repairs"},
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    placements = custom_dashboards_store.list_placements(dash["id"])
    assert len(placements) == 1
    p = placements[0]
    assert p["type"] == "goat_race"
    assert p["name"] == "cdt-listp-wd"
    assert p["visual"] == {"color": "#22c55e"}
    assert p["default_data"] == {"group": "Repairs"}
    assert p["x"] == 0


def test_update_placement_changes_position():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-update", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-update-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    p = custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    custom_dashboards_store.update_placement(p["id"], x=2, y=3, w=8, h=6)
    refreshed = custom_dashboards_store.list_placements(dash["id"])[0]
    assert refreshed["x"] == 2 and refreshed["y"] == 3
    assert refreshed["w"] == 8 and refreshed["h"] == 6


def test_update_placement_changes_overrides():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-ovr", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-ovr-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    p = custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    custom_dashboards_store.update_placement(p["id"], data_overrides={"group": "Dismantlers"})
    refreshed = custom_dashboards_store.list_placements(dash["id"])[0]
    assert refreshed["data_overrides"] == {"group": "Dismantlers"}


def test_delete_placement():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-delp", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-delp-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    p = custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    custom_dashboards_store.delete_placement(p["id"])
    assert custom_dashboards_store.list_placements(dash["id"]) == []
