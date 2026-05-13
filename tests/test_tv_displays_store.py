"""Postgres-gated tests for tv_displays_store.

Each test cleans 'st-' prefix rows so it doesn't collide with the seed
list or any real displays Dale has saved.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="tv_displays_store tests need Postgres",
)


@pytest.fixture(autouse=True)
def _clean_displays():
    from zira_dashboard import db
    db.init_pool()
    db.bootstrap_schema()
    db.execute("DELETE FROM tv_displays WHERE slug LIKE 'st-%'")
    yield
    db.execute("DELETE FROM tv_displays WHERE slug LIKE 'st-%'")


def test_save_inserts_and_returns_slug():
    from zira_dashboard import tv_displays_store
    row = tv_displays_store.save(name="st-repair-1", kind="wc", wc_name="Repair 1", theme="dark")
    assert row["slug"] == "st-repair-1"
    assert row["theme"] == "dark"
    assert row["kind"] == "wc"
    assert row["wc_name"] == "Repair 1"
    assert isinstance(row["id"], int)


def test_save_collision_suffixes_slug():
    from zira_dashboard import tv_displays_store
    a = tv_displays_store.save(name="st-clash", kind="wc", wc_name="Repair 1", theme="dark")
    b = tv_displays_store.save(name="st-clash", kind="wc", wc_name="Repair 2", theme="dark")
    assert a["slug"] == "st-clash"
    assert b["slug"] == "st-clash-2"
    c = tv_displays_store.save(name="st-clash", kind="wc", wc_name="Repair 3", theme="dark")
    assert c["slug"] == "st-clash-3"


def test_save_with_id_updates_existing():
    from zira_dashboard import tv_displays_store
    row = tv_displays_store.save(name="st-edit", kind="wc", wc_name="Repair 1", theme="dark")
    updated = tv_displays_store.save(
        name="st-edit", kind="wc", wc_name="Repair 2", theme="light", id=row["id"],
    )
    assert updated["id"] == row["id"]
    assert updated["slug"] == "st-edit"
    assert updated["wc_name"] == "Repair 2"
    assert updated["theme"] == "light"


def test_save_rename_regenerates_slug_without_collision_on_self():
    from zira_dashboard import tv_displays_store
    row = tv_displays_store.save(name="st-renamable", kind="wc", wc_name="Repair 1", theme="dark")
    again = tv_displays_store.save(
        name="st-renamable", kind="wc", wc_name="Repair 1", theme="dark", id=row["id"],
    )
    assert again["slug"] == "st-renamable"
    renamed = tv_displays_store.save(
        name="st-was-renamed", kind="wc", wc_name="Repair 1", theme="dark", id=row["id"],
    )
    assert renamed["slug"] == "st-was-renamed"


def test_set_theme_updates_only_theme():
    from zira_dashboard import tv_displays_store
    row = tv_displays_store.save(name="st-theme", kind="wc", wc_name="Repair 1", theme="dark")
    tv_displays_store.set_theme(row["id"], "light")
    fetched = tv_displays_store.by_slug("st-theme")
    assert fetched["theme"] == "light"
    assert fetched["wc_name"] == "Repair 1"


def test_delete_removes_row():
    from zira_dashboard import tv_displays_store
    row = tv_displays_store.save(name="st-deleteme", kind="wc", wc_name="Repair 1", theme="dark")
    tv_displays_store.delete(row["id"])
    assert tv_displays_store.by_slug("st-deleteme") is None


def test_by_slug_returns_none_for_missing():
    from zira_dashboard import tv_displays_store
    assert tv_displays_store.by_slug("st-not-there") is None


def test_list_displays_returns_all_rows():
    from zira_dashboard import tv_displays_store
    tv_displays_store.save(name="st-a", kind="wc", wc_name="Repair 1", theme="dark")
    tv_displays_store.save(name="st-b", kind="vs_recycling", wc_name=None, theme="light")
    rows = tv_displays_store.list_displays()
    slugs = [r["slug"] for r in rows]
    assert "st-a" in slugs
    assert "st-b" in slugs


def test_seed_defaults_if_empty_seeds_when_empty(monkeypatch):
    from zira_dashboard import tv_displays_store, staffing, db

    class _Loc:
        def __init__(self, name): self.name = name

    monkeypatch.setattr(staffing, "LOCATIONS", [
        _Loc("Junior 2"), _Loc("Repair 1"), _Loc("Repair 2"), _Loc("Repair 3"),
        _Loc("Dismantler 1"), _Loc("Dismantler 2"), _Loc("Dismantler 3"), _Loc("Dismantler 4"),
    ])
    db.execute("DELETE FROM tv_displays")
    tv_displays_store.seed_defaults_if_empty()
    rows = tv_displays_store.list_displays()
    names = [r["name"] for r in rows]
    assert "Recycling VS" in names
    assert "New VS" in names
    assert "Repair 1" in names
    assert "Dismantler 4" in names
    assert len(rows) == 10
    tv_displays_store.seed_defaults_if_empty()
    assert len(tv_displays_store.list_displays()) == 10


def test_save_custom_kind_stores_dashboard_id():
    from zira_dashboard import tv_displays_store, custom_dashboards_store, db
    dash = custom_dashboards_store.save_dashboard(
        name="st-cust-dash", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    row = tv_displays_store.save(
        name="st-cust-tv", kind="custom", wc_name=None,
        custom_dashboard_id=dash["id"], theme="dark",
    )
    assert row["kind"] == "custom"
    assert row["custom_dashboard_id"] == dash["id"]
    fetched = tv_displays_store.by_slug("st-cust-tv")
    assert fetched["custom_dashboard_id"] == dash["id"]
    rows = tv_displays_store.list_displays()
    target = next(r for r in rows if r["slug"] == "st-cust-tv")
    assert target["custom_dashboard_id"] == dash["id"]
    db.execute("DELETE FROM tv_displays WHERE slug = 'st-cust-tv'")
    db.execute("DELETE FROM custom_dashboards WHERE slug = 'st-cust-dash'")


def test_seed_defaults_skips_missing_wc(monkeypatch, caplog):
    from zira_dashboard import tv_displays_store, staffing, db
    import logging

    class _Loc:
        def __init__(self, name): self.name = name

    monkeypatch.setattr(staffing, "LOCATIONS", [_Loc("Repair 1")])
    db.execute("DELETE FROM tv_displays")
    with caplog.at_level(logging.WARNING):
        tv_displays_store.seed_defaults_if_empty()
    rows = tv_displays_store.list_displays()
    names = [r["name"] for r in rows]
    assert "Recycling VS" in names
    assert "New VS" in names
    assert "Repair 1" in names
    assert "Junior 2" not in names
    assert "Dismantler 1" not in names
    assert len(rows) == 3
