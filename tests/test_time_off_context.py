from datetime import date

from zira_dashboard import time_off_context as ctx


def test_department_for_person_returns_distinct_departments(monkeypatch):
    monkeypatch.setattr(ctx.db, "query",
                        lambda sql, params: [{"department": "Recycled"}, {"department": "New"}])
    assert ctx.department_for_person(7) == {"Recycled", "New"}


def test_coverage_for_uses_department_scope_when_known(monkeypatch):
    monkeypatch.setattr(ctx, "department_for_person", lambda pid: {"Recycled"})
    seen = {}
    def fake_query(sql, params):
        seen["sql"] = sql
        seen["params"] = params
        return [{"n": 2}]
    monkeypatch.setattr(ctx.db, "query", fake_query)

    result = ctx.coverage_for(7, date(2026, 6, 30), date(2026, 7, 2))

    assert result == {"count": 2, "scope": "department"}
    assert "ANY(%s)" in seen["sql"]
    assert seen["params"][0] == 7  # exclude requester


def test_coverage_for_falls_back_to_plant_when_no_department(monkeypatch):
    monkeypatch.setattr(ctx, "department_for_person", lambda pid: set())
    monkeypatch.setattr(ctx.db, "query", lambda sql, params: [{"n": 5}])

    result = ctx.coverage_for(7, date(2026, 6, 30), date(2026, 7, 2))

    assert result == {"count": 5, "scope": "plant"}


def test_balance_for_returns_remaining_and_unit(monkeypatch):
    monkeypatch.setattr(ctx.db, "query",
                        lambda sql, params: [{"available": 24.0, "unit": "hours"}])
    assert ctx.balance_for(7, 3) == {"remaining": 24.0, "unit": "hours"}


def test_balance_for_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(ctx.db, "query", lambda sql, params: [])
    assert ctx.balance_for(7, 3) is None


def test_request_amount_hours_and_days():
    assert ctx.request_amount(
        {"hour_from": 8.0, "hour_to": 12.0, "date_from": date(2026, 7, 3),
         "date_to": date(2026, 7, 3)}) == (4.0, "hours")
    assert ctx.request_amount(
        {"hour_from": None, "hour_to": None, "date_from": date(2026, 6, 30),
         "date_to": date(2026, 7, 2)}) == (3.0, "days")
