"""Postgres-gated tests for pinned_dashboards_store."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="pinned_dashboards_store tests need Postgres",
)


@pytest.fixture(autouse=True)
def _clean():
    from zira_dashboard import db
    db.init_pool()
    db.bootstrap_schema()
    db.execute("DELETE FROM pinned_dashboards WHERE ref LIKE 'pdt-%' OR (kind = 'wc' AND ref LIKE 'pdt-%')")
    yield
    db.execute("DELETE FROM pinned_dashboards WHERE ref LIKE 'pdt-%' OR (kind = 'wc' AND ref LIKE 'pdt-%')")


def test_pin_inserts_row():
    from zira_dashboard import pinned_dashboards_store
    pinned_dashboards_store.pin("wc", "pdt-test-wc")
    assert pinned_dashboards_store.is_pinned("wc", "pdt-test-wc") is True


def test_pin_is_idempotent():
    from zira_dashboard import pinned_dashboards_store, db
    pinned_dashboards_store.pin("custom", "pdt-test-dash")
    pinned_dashboards_store.pin("custom", "pdt-test-dash")
    rows = db.query(
        "SELECT COUNT(*) AS n FROM pinned_dashboards WHERE kind = 'custom' AND ref = 'pdt-test-dash'"
    )
    assert int(rows[0]["n"]) == 1


def test_unpin_removes_row():
    from zira_dashboard import pinned_dashboards_store
    pinned_dashboards_store.pin("wc", "pdt-rm-wc")
    pinned_dashboards_store.unpin("wc", "pdt-rm-wc")
    assert pinned_dashboards_store.is_pinned("wc", "pdt-rm-wc") is False


def test_unpin_missing_is_noop():
    from zira_dashboard import pinned_dashboards_store
    pinned_dashboards_store.unpin("wc", "pdt-never-pinned")  # no raise


def test_list_pins_ordered_by_sort_then_created():
    from zira_dashboard import pinned_dashboards_store
    pinned_dashboards_store.pin("wc", "pdt-a")
    pinned_dashboards_store.pin("wc", "pdt-b")
    pinned_dashboards_store.pin("wc", "pdt-c")
    pins = [p for p in pinned_dashboards_store.list_pins() if p["ref"].startswith("pdt-")]
    refs = [p["ref"] for p in pins]
    assert refs == ["pdt-a", "pdt-b", "pdt-c"]


def test_seed_defaults_if_empty_seeds_two_vs_pins(monkeypatch):
    from zira_dashboard import pinned_dashboards_store, db
    db.execute("DELETE FROM pinned_dashboards")
    pinned_dashboards_store.seed_defaults_if_empty()
    pins = pinned_dashboards_store.list_pins()
    kinds = [p["kind"] for p in pins]
    assert "vs_recycling" in kinds
    assert "vs_new" in kinds
    assert len(pins) == 2
    pinned_dashboards_store.seed_defaults_if_empty()
    assert len(pinned_dashboards_store.list_pins()) == 2
