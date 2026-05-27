"""Tests for time_off_balances — per-employee leave balance cache.

Each test stubs ``db.execute`` / ``db.query`` and the ``odoo_client``
surface so the test exercises only the cache upsert + error-swallow
logic. No real Odoo or Postgres call ever happens.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from zira_dashboard import time_off_balances


@pytest.fixture
def fake_db(monkeypatch):
    """Capture all ``db.execute`` calls and stub ``db.query`` to empty.

    Tests assert against ``captured["executes"]`` to verify which SQL ran
    and with what params.
    """
    captured: dict = {"executes": []}
    monkeypatch.setattr(
        time_off_balances.db, "execute",
        lambda sql, params=None: captured["executes"].append((sql, params)),
    )
    monkeypatch.setattr(
        time_off_balances.db, "query",
        lambda sql, params=None: [],
    )
    return captured


def test_refresh_for_employee_upserts_each_balance(monkeypatch, fake_db):
    """One INSERT … ON CONFLICT DO UPDATE per balance row from Odoo."""
    monkeypatch.setattr(
        time_off_balances.odoo_client, "fetch_balances_for",
        MagicMock(return_value=[
            {"holiday_status_id": 1, "unit": "days",
             "allocated_total": 15.0, "taken": 3.0, "pending": 2.0,
             "available": 12.0, "available_practical": 10.0},
            {"holiday_status_id": 2, "unit": "hours",
             "allocated_total": 0.0, "taken": 0.0, "pending": 0.0,
             "available": 0.0, "available_practical": 0.0},
        ]),
    )
    time_off_balances.refresh_for_employee(5)
    upserts = [
        e for e in fake_db["executes"]
        if "INSERT INTO time_off_balances" in e[0]
        or "UPDATE time_off_balances" in e[0]
    ]
    assert len(upserts) >= 2  # one per balance


def test_refresh_for_employee_swallows_odoo_errors(monkeypatch, fake_db):
    """Odoo raising must not propagate — caller still renders the wizard."""
    monkeypatch.setattr(
        time_off_balances.odoo_client, "fetch_balances_for",
        MagicMock(side_effect=RuntimeError("Odoo down")),
    )
    # Should not raise
    time_off_balances.refresh_for_employee(5)


def test_invalidate_one(monkeypatch, fake_db):
    """invalidate() issues a DELETE scoped to one person."""
    time_off_balances.invalidate(5)
    deletes = [
        e for e in fake_db["executes"]
        if "DELETE FROM time_off_balances" in e[0]
    ]
    assert deletes
