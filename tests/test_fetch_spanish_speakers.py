"""Exact Odoo Spanish skill-level lookup tests."""
from __future__ import annotations

from unittest import mock

from zira_dashboard import odoo_client


def _fake_execute(model, method, *args, **kwargs):
    if model == "hr.skill":
        return [{"id": 7, "name": "Spanish"}]
    if model == "hr.employee.skill":
        return [
            {"employee_id": [11, "Ana"], "skill_level_id": [101, "Basic"]},
            {"employee_id": [12, "Beto"], "skill_level_id": [103, "Fluent"]},
            {"employee_id": 13, "skill_level_id": False},
        ]
    raise AssertionError(f"unexpected call {model}.{method}")


def test_returns_employee_to_spanish_skill_level_id():
    with mock.patch.object(odoo_client, "execute", side_effect=_fake_execute):
        assert odoo_client.fetch_spanish_skill_level_ids() == {11: 101, 12: 103}


def test_no_spanish_skill_returns_empty_mapping():
    def no_skill(model, method, *args, **kwargs):
        if model == "hr.skill":
            return []
        raise AssertionError("employee skills must not be queried")
    with mock.patch.object(odoo_client, "execute", side_effect=no_skill):
        assert odoo_client.fetch_spanish_skill_level_ids() == {}
