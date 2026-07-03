from fastapi.testclient import TestClient

from zira_dashboard import api_keys
from zira_dashboard.app import app
from zira_dashboard.deps import templates

client = TestClient(app)


def _extract_api_section() -> str:
    import re
    from pathlib import Path

    html = Path("src/zira_dashboard/templates/settings.html").read_text()
    match = re.search(r"<section class=\"panel\" id=\"api-panel\".*?</section>", html, re.DOTALL)
    assert match, "api-panel section missing from settings.html"
    return match.group(0)


def test_api_settings_section_renders_keys_and_create_form():
    rendered = templates.env.from_string(_extract_api_section()).render(
        active_section="api",
        api_keys_rows=[
            {
                "id": 1,
                "name": "CRM",
                "key_prefix": "gpi_live_abcd",
                "scopes": ["admin:*"],
                "allowed_ips": [],
                "created_at": None,
                "last_used_at": None,
                "revoked_at": None,
            }
        ],
        new_api_key="gpi_live_once",
    )
    assert "API Keys" in rendered
    assert "gpi_live_once" in rendered
    assert 'action="/settings/api-keys"' in rendered
    assert 'action="/settings/api-keys/1/revoke"' in rendered
    assert 'name="scope_admin"' in rendered


def test_api_settings_page_route_renders(monkeypatch):
    from zira_dashboard import (
        auto_lunch_settings,
        db,
        odoo_sync,
        rounding_system_store,
        saturday_schedule_store,
        schedule_store,
        shift_config,
        staffing,
        work_centers_store,
        work_schedule_store,
    )

    monkeypatch.setattr(
        api_keys,
        "list_keys",
        lambda: [
            {
                "id": 1,
                "name": "CRM",
                "key_prefix": "gpi_live_abcd",
                "scopes": ["admin:*"],
                "allowed_ips": [],
                "created_at": None,
                "last_used_at": None,
                "revoked_at": None,
            }
        ],
    )
    monkeypatch.setattr(odoo_sync, "sync", lambda force=False: None)
    monkeypatch.setattr(shift_config, "productive_minutes_per_day", lambda: 480)
    monkeypatch.setattr(staffing, "load_roster", lambda: [])
    monkeypatch.setattr(
        work_centers_store,
        "effective",
        lambda loc: {
            "goal_per_day": 0,
            "min_ops": loc.min_ops,
            "max_ops": loc.max_ops,
            "required_skills": [],
            "note": "",
            "groups": [],
            "department": "",
            "default_people": [],
        },
    )
    monkeypatch.setattr(work_centers_store, "all_group_names", lambda kind: [])
    monkeypatch.setattr(work_centers_store, "synced_departments", lambda: [])
    monkeypatch.setattr(work_centers_store, "registered_groups", lambda: [])
    monkeypatch.setattr(schedule_store, "current", lambda: schedule_store.DEFAULT_SCHEDULE)
    monkeypatch.setattr(saturday_schedule_store, "current", lambda: saturday_schedule_store.DEFAULT)
    monkeypatch.setattr(work_schedule_store, "all_overrides", lambda: [])
    monkeypatch.setattr(rounding_system_store, "all_systems", lambda: [])
    monkeypatch.setattr(rounding_system_store, "department_map", lambda: {})
    monkeypatch.setattr(db, "query", lambda sql, params=None: [])
    monkeypatch.setattr(auto_lunch_settings, "current", lambda: auto_lunch_settings.DEFAULT)

    response = client.get("/settings?section=api")

    assert response.status_code == 200
    assert 'id="api-panel"' in response.text
    assert "API Keys" in response.text
    assert "CRM" in response.text
    assert 'action="/settings/api-keys"' in response.text


def test_api_scope_parser_defaults_to_read():
    from zira_dashboard.routes import settings

    assert settings._parse_api_key_scopes({}) == ["object:read"]
    assert settings._parse_api_key_scopes({"scope_admin": "on"}) == ["admin:*"]
    assert settings._parse_api_key_scopes(
        {"scope_read": "on", "scope_write": "on"}
    ) == ["object:read", "object:write"]
