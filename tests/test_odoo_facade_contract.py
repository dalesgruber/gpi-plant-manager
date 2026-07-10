from zira_dashboard import odoo_client


def test_facade_uses_execute_replaced_after_import(monkeypatch):
    calls = []

    def fake(model, method, *args, **kwargs):
        calls.append((model, method, args, kwargs))
        if model == "hr.skill.type":
            return [{"id": 1, "name": "Production Skills"}]
        if model == "hr.skill":
            return [{"id": 11, "name": "Planer", "skill_type_id": [1, "Production Skills"]}]
        raise AssertionError(model)

    monkeypatch.setattr(odoo_client, "execute", fake)
    assert odoo_client.fetch_skill_columns_with_types() == [
        {"id": 11, "name": "Planer", "type": "Production Skills"}
    ]
    assert [call[0:2] for call in calls] == [
        ("hr.skill.type", "search_read"),
        ("hr.skill", "search_read"),
    ]


def test_facade_leave_cache_remains_assignable(monkeypatch):
    expected = [{"id": 7, "name": "Vacation", "request_unit": "day"}]
    monkeypatch.setattr(odoo_client, "_leave_types_cache", (expected, float("inf")))
    monkeypatch.setattr(
        odoo_client,
        "execute",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cache miss")),
    )
    assert odoo_client.fetch_leave_types() is expected


def test_facade_department_helper_is_resolved_at_call_time(monkeypatch):
    calls = []
    monkeypatch.setenv("ODOO_KIOSK_DEPARTMENT_FIELD", "x_kiosk_department_id")
    monkeypatch.setattr(odoo_client, "_department_id_for_wc", lambda wc: 44)
    monkeypatch.setattr(
        odoo_client,
        "execute",
        lambda model, method, *args, **kwargs: calls.append(
            (model, method, args, kwargs)
        )
        or 91,
    )
    attendance_id = odoo_client.clock_in(3, "Repair 2", odoo_client.datetime.now(odoo_client.UTC))
    assert attendance_id == 91
    assert calls[-1][0:2] == ("hr.attendance", "create")
    assert calls[-1][2][0]["x_kiosk_department_id"] == 44
