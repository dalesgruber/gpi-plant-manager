"""Sync orchestration tests against live Postgres.

Skips when DATABASE_URL is unset. The sync module now writes to the
people / skills / person_skills tables directly; tests stub the four
fetch_* helpers on odoo_client and assert against the resulting rows.
"""

import json
import os
from datetime import datetime, timezone

import pytest

from zira_dashboard import odoo_sync


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)


@pytest.fixture(autouse=True)
def _clean_sync_state():
    from zira_dashboard import db
    # Wipe sync state + the test odoo_id rows so each test starts fresh.
    db.execute("DELETE FROM app_settings WHERE key = 'odoo_last_sync'")
    db.execute("DELETE FROM person_skills WHERE person_id IN (SELECT id FROM people WHERE odoo_id BETWEEN 99000 AND 99999)")
    db.execute("DELETE FROM people WHERE odoo_id BETWEEN 99000 AND 99999")
    db.execute("DELETE FROM skills WHERE name IN ('TestRepair', 'TestDismantler')")
    yield
    db.execute("DELETE FROM person_skills WHERE person_id IN (SELECT id FROM people WHERE odoo_id BETWEEN 99000 AND 99999)")
    db.execute("DELETE FROM people WHERE odoo_id BETWEEN 99000 AND 99999")
    db.execute("DELETE FROM skills WHERE name IN ('TestRepair', 'TestDismantler')")


def _stub_client(monkeypatch, employees, skills_for, columns_meta, buckets):
    monkeypatch.setattr(odoo_sync.odoo_client, "fetch_employees", lambda: employees)
    monkeypatch.setattr(odoo_sync.odoo_client, "fetch_skills_for", lambda ids: skills_for)
    monkeypatch.setattr(odoo_sync.odoo_client, "fetch_skill_columns_with_types", lambda: columns_meta)
    monkeypatch.setattr(odoo_sync.odoo_client, "fetch_skill_level_buckets", lambda: buckets)


def test_sync_skips_when_within_ttl(monkeypatch):
    from zira_dashboard import db
    db.execute(
        "INSERT INTO app_settings (key, value, updated_at) "
        "VALUES ('odoo_last_sync', %s::jsonb, now())",
        (json.dumps(datetime.now(timezone.utc).isoformat()),),
    )
    monkeypatch.setattr(odoo_sync.odoo_client, "fetch_employees",
                        lambda: (_ for _ in ()).throw(AssertionError("should not call")))
    result = odoo_sync.sync(force=False)
    assert result.refreshed is False
    assert result.ok is True


def test_sync_force_refreshes_even_within_ttl(monkeypatch):
    _stub_client(
        monkeypatch,
        employees=[{"id": 99001, "name": "TestAlice", "active": True, "work_email": False}],
        skills_for={99001: [{"skill_id": 10, "skill_name": "TestRepair", "level_id": 103}]},
        columns_meta=[
            {"name": "TestRepair", "type": "Production Skills"},
            {"name": "TestDismantler", "type": "Production Skills"},
        ],
        buckets={103: 3},
    )
    result = odoo_sync.sync(force=True)
    assert result.refreshed is True
    assert result.employee_count == 1
    assert result.skill_column_count == 2
    from zira_dashboard import db
    rows = db.query(
        "SELECT pe.name, ps.level, sk.name AS skill_name "
        "FROM people pe JOIN person_skills ps ON ps.person_id = pe.id "
        "JOIN skills sk ON sk.id = ps.skill_id WHERE pe.odoo_id = 99001"
    )
    assert rows == [{"name": "TestAlice", "level": 3, "skill_name": "TestRepair"}]


def test_sync_preserves_local_reserve_flag(monkeypatch):
    from zira_dashboard import db
    # Pre-create the person with reserve=True locally.
    db.execute(
        "INSERT INTO people (odoo_id, name, active, reserve) VALUES (99002, 'TestBob', TRUE, TRUE)"
    )
    _stub_client(
        monkeypatch,
        employees=[{"id": 99002, "name": "TestBob", "active": True, "work_email": False}],
        skills_for={99002: []},
        columns_meta=[{"name": "TestRepair", "type": "Production Skills"}],
        buckets={},
    )
    odoo_sync.sync(force=True)
    rows = db.query("SELECT reserve FROM people WHERE odoo_id = 99002")
    assert rows[0]["reserve"] is True


def test_sync_returns_error_on_odoo_failure(monkeypatch):
    monkeypatch.setattr(odoo_sync.odoo_client, "fetch_employees",
                        lambda: (_ for _ in ()).throw(odoo_sync.odoo_client.OdooAuthError("nope")))
    result = odoo_sync.sync(force=True)
    assert result.ok is False
    assert "nope" in (result.error or "")
    assert result.refreshed is False
