"""Unit tests for awards.py — computation engine.

These tests stub production_history.daily_records and
work_centers_store.members so they don't need DATABASE_URL or a
running Zira/cached_leaderboard.
"""
from __future__ import annotations

from datetime import date


def _stub_data(monkeypatch, *, records, members_map):
    """records: list of dicts (day, person, wc, units, hours, downtime)
    members_map: {group_name: [wc_name, ...]}"""
    from zira_dashboard import production_history, work_centers_store

    class _FakeLoc:
        def __init__(self, name): self.name = name

    monkeypatch.setattr(
        production_history,
        "daily_records",
        lambda s, e, c=None: [r for r in records if s <= r["day"] <= e],
    )
    monkeypatch.setattr(
        work_centers_store,
        "members",
        lambda kind, name: [_FakeLoc(n) for n in members_map.get(name, [])],
    )


def test_person_days_in_group_sums_units_and_hours_per_day(monkeypatch):
    """Per-(person, day), units and hours sum across the group's WCs."""
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 4, 1), "person": "Alice", "wc": "Repair 1",
             "units": 60.0, "hours": 4.0, "downtime": 0.0},
            {"day": date(2026, 4, 1), "person": "Alice", "wc": "Repair 2",
             "units": 40.0, "hours": 4.0, "downtime": 0.0},
            {"day": date(2026, 4, 1), "person": "Bob", "wc": "Repair 1",
             "units": 50.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1", "Repair 2"]},
    )
    from zira_dashboard import awards
    rows = awards.person_days_in_group("Repairs", date(2026, 4, 1), date(2026, 4, 1))
    by_person = {r["name"]: r for r in rows}
    assert by_person["Alice"]["units"] == 100.0
    assert by_person["Alice"]["hours"] == 8.0
    assert by_person["Bob"]["units"] == 50.0


def test_person_days_in_group_excludes_zero_unit_days(monkeypatch):
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 4, 1), "person": "Alice", "wc": "Repair 1",
             "units": 0.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    rows = awards.person_days_in_group("Repairs", date(2026, 4, 1), date(2026, 4, 1))
    assert rows == []


def test_monthly_badges_top_3_by_units(monkeypatch):
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 4, 1), "person": "A", "wc": "Repair 1",
             "units": 100.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 5), "person": "B", "wc": "Repair 1",
             "units": 90.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 7), "person": "C", "wc": "Repair 1",
             "units": 80.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 12), "person": "D", "wc": "Repair 1",
             "units": 70.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 15), "person": "E", "wc": "Repair 1",
             "units": 60.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    badges = awards.monthly_badges("Repairs", 2026, 4)
    assert [b["position"] for b in badges] == [1, 2, 3]
    assert [b["name"] for b in badges] == ["A", "B", "C"]
    assert badges[0]["units"] == 100.0


def test_monthly_badges_tiebreak_by_pph(monkeypatch):
    """Equal units — fewer hours (higher pph) ranks ahead."""
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 4, 1), "person": "Slow", "wc": "Repair 1",
             "units": 100.0, "hours": 10.0, "downtime": 0.0},
            {"day": date(2026, 4, 2), "person": "Fast", "wc": "Repair 1",
             "units": 100.0, "hours": 5.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    badges = awards.monthly_badges("Repairs", 2026, 4)
    assert badges[0]["name"] == "Fast"
    assert badges[1]["name"] == "Slow"


def test_monthly_badges_tiebreak_by_name_when_pph_equal(monkeypatch):
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 4, 1), "person": "Bob", "wc": "Repair 1",
             "units": 100.0, "hours": 5.0, "downtime": 0.0},
            {"day": date(2026, 4, 2), "person": "Anne", "wc": "Repair 1",
             "units": 100.0, "hours": 5.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    badges = awards.monthly_badges("Repairs", 2026, 4)
    assert badges[0]["name"] == "Anne"


def test_monthly_badges_only_within_month(monkeypatch):
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 3, 31), "person": "X", "wc": "Repair 1",
             "units": 999.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 1), "person": "Y", "wc": "Repair 1",
             "units": 50.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 5, 1), "person": "Z", "wc": "Repair 1",
             "units": 999.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    badges = awards.monthly_badges("Repairs", 2026, 4)
    assert [b["name"] for b in badges] == ["Y"]


def test_annual_top_days_top_3_by_units(monkeypatch):
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 1, 5), "person": "A", "wc": "Repair 1",
             "units": 100.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 6, 1), "person": "B", "wc": "Repair 1",
             "units": 200.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 12, 31), "person": "C", "wc": "Repair 1",
             "units": 150.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 7, 7), "person": "D", "wc": "Repair 1",
             "units": 50.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    top = awards.annual_top_days("Repairs", 2026)
    assert [t["name"] for t in top] == ["B", "C", "A"]


def test_goat_returns_max_single_day(monkeypatch):
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2025, 1, 1), "person": "Old", "wc": "Repair 1",
             "units": 200.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 1), "person": "New", "wc": "Repair 1",
             "units": 250.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    monkeypatch.setattr(awards, "_all_time_range", lambda: (date(2025, 1, 1), date(2026, 4, 1)))
    g = awards.goat("Repairs")
    assert g["name"] == "New"
    assert g["units"] == 250.0


def test_goat_first_to_set_on_tie(monkeypatch):
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2025, 6, 1), "person": "First", "wc": "Repair 1",
             "units": 200.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 1), "person": "Tied", "wc": "Repair 1",
             "units": 200.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    monkeypatch.setattr(awards, "_all_time_range", lambda: (date(2025, 1, 1), date(2026, 12, 31)))
    g = awards.goat("Repairs")
    assert g["name"] == "First"


def test_goat_returns_none_when_no_data(monkeypatch):
    _stub_data(monkeypatch, records=[], members_map={"Repairs": ["Repair 1"]})
    from zira_dashboard import awards
    monkeypatch.setattr(awards, "_all_time_range", lambda: (date(2026, 1, 1), date(2026, 1, 1)))
    assert awards.goat("Repairs") is None
