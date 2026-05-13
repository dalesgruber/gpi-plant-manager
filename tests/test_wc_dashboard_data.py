"""Unit tests for wc_dashboard_data helpers.

Pure functions only — these tests don't need a DB and run unconditionally.
"""
from __future__ import annotations


def test_slug_simple():
    from zira_dashboard.wc_dashboard_data import slug_for_wc
    assert slug_for_wc("Repair 1") == "repair-1"


def test_slug_lowercases():
    from zira_dashboard.wc_dashboard_data import slug_for_wc
    assert slug_for_wc("REPAIR 1") == "repair-1"


def test_slug_collapses_punctuation():
    from zira_dashboard.wc_dashboard_data import slug_for_wc
    assert slug_for_wc("Hand Build #1") == "hand-build-1"


def test_slug_strips_leading_trailing_hyphens():
    from zira_dashboard.wc_dashboard_data import slug_for_wc
    assert slug_for_wc("  Bay 4  ") == "bay-4"
    assert slug_for_wc("--repair-1--") == "repair-1"


def test_slug_collapses_runs_of_hyphens():
    from zira_dashboard.wc_dashboard_data import slug_for_wc
    assert slug_for_wc("Repair   1") == "repair-1"
    assert slug_for_wc("Hand // Build") == "hand-build"


def test_slug_keeps_digits():
    from zira_dashboard.wc_dashboard_data import slug_for_wc
    assert slug_for_wc("Trim Saw 12") == "trim-saw-12"


def test_slug_empty_input():
    from zira_dashboard.wc_dashboard_data import slug_for_wc
    assert slug_for_wc("") == ""
    assert slug_for_wc("   ") == ""


import os
from datetime import date as _date

import pytest


def test_wc_by_slug_resolves_known_slug(monkeypatch):
    from zira_dashboard import wc_dashboard_data

    class _Loc:
        def __init__(self, name): self.name = name

    from zira_dashboard import staffing
    monkeypatch.setattr(
        staffing, "LOCATIONS",
        [_Loc("Repair 1"), _Loc("Hand Build #1")],
    )
    loc = wc_dashboard_data.wc_by_slug("repair-1")
    assert loc is not None and loc.name == "Repair 1"
    loc2 = wc_dashboard_data.wc_by_slug("hand-build-1")
    assert loc2 is not None and loc2.name == "Hand Build #1"


def test_wc_by_slug_unknown_returns_none(monkeypatch):
    from zira_dashboard import wc_dashboard_data, staffing
    monkeypatch.setattr(staffing, "LOCATIONS", [])
    assert wc_dashboard_data.wc_by_slug("ghost") is None


def test_assigned_operators_for_wc(monkeypatch):
    from zira_dashboard import wc_dashboard_data, staffing

    monkeypatch.setattr(staffing, "load_schedule", lambda d: staffing.Schedule(
        day=d, published=True,
        assignments={"Repair 1": ["Christian", "Jose L"], "Repair 2": ["Alice"]},
    ))
    out = wc_dashboard_data.assigned_operators_for_wc("Repair 1", _date(2026, 5, 13))
    assert out == ["Christian", "Jose L"]


def test_assigned_operators_unassigned_returns_empty(monkeypatch):
    from zira_dashboard import wc_dashboard_data, staffing
    monkeypatch.setattr(staffing, "load_schedule", lambda d: staffing.Schedule(
        day=d, published=True, assignments={},
    ))
    assert wc_dashboard_data.assigned_operators_for_wc("Repair 1", _date(2026, 5, 13)) == []


def test_pallets_banner_data(monkeypatch):
    """Pallets banner: today's units vs prorated target for THIS WC."""
    from zira_dashboard import wc_dashboard_data, work_centers_store
    # 200-unit/day WC; shift is half elapsed → prorated target 100.
    class _Loc:
        name = "Repair 1"
    monkeypatch.setattr(
        wc_dashboard_data, "_load_wc", lambda nm: _Loc() if nm == "Repair 1" else None
    )
    monkeypatch.setattr(work_centers_store, "goal_per_day", lambda loc: 200)
    monkeypatch.setattr(wc_dashboard_data, "_units_today_for_wc", lambda nm, d: 87)
    monkeypatch.setattr(wc_dashboard_data, "_shift_elapsed_fraction", lambda d: 0.5)

    out = wc_dashboard_data.pallets_banner("Repair 1", _date(2026, 5, 13))
    assert out["units_today"] == 87
    assert out["target_today"] == 100  # 200 * 0.5
    assert out["target_full_day"] == 200
    assert out["pct_of_target"] == pytest.approx(87.0)  # 87/100*100


def test_monthly_ribbons_uses_group(monkeypatch):
    """Monthly ribbons come from the WC's group, not the WC itself."""
    from zira_dashboard import wc_dashboard_data, work_centers_store, awards

    class _Loc:
        name = "Repair 1"
    monkeypatch.setattr(wc_dashboard_data, "_load_wc", lambda nm: _Loc() if nm == "Repair 1" else None)
    monkeypatch.setattr(work_centers_store, "groups", lambda loc: ["Repairs"])
    monkeypatch.setattr(
        awards, "monthly_badges",
        lambda group, year, month: [
            {"position": 1, "name": "Christian", "day": _date(2026, 5, 4), "units": 145, "pph": 16.1},
            {"position": 2, "name": "Lauro",     "day": _date(2026, 5, 9), "units": 132, "pph": 14.7},
        ] if group == "Repairs" else [],
    )
    out = wc_dashboard_data.monthly_ribbons("Repair 1", 2026, 5)
    assert out["group"] == "Repairs"
    assert len(out["entries"]) == 2
    assert out["entries"][0]["name"] == "Christian"


def test_goat_race_uses_group(monkeypatch):
    """GOAT race compares against the WC's group's all-time GOAT."""
    from zira_dashboard import wc_dashboard_data, work_centers_store, awards

    class _Loc:
        name = "Repair 1"
    monkeypatch.setattr(wc_dashboard_data, "_load_wc", lambda nm: _Loc() if nm == "Repair 1" else None)
    monkeypatch.setattr(work_centers_store, "groups", lambda loc: ["Repairs"])
    monkeypatch.setattr(
        awards, "goat",
        lambda group: {"name": "Christian", "day": _date(2026, 2, 15), "units": 145, "pph": 16.1}
            if group == "Repairs" else None,
    )
    # 87 units today vs GOAT's 145-units day, half elapsed → GOAT pace today = 145 * 0.5 = 72.5
    monkeypatch.setattr(wc_dashboard_data, "_units_today_for_wc", lambda nm, d: 87)
    monkeypatch.setattr(wc_dashboard_data, "_shift_elapsed_fraction", lambda d: 0.5)

    out = wc_dashboard_data.goat_race("Repair 1", _date(2026, 5, 13))
    assert out["group"] == "Repairs"
    assert out["units_today"] == 87
    assert out["goat"]["name"] == "Christian"
    assert out["goat"]["units"] == 145
    assert out["goat_pace_today"] == pytest.approx(72.5)
    assert out["status"] == "AHEAD"  # 87 > 72.5


def test_goat_race_status_on_pace_when_within_5pct(monkeypatch):
    from zira_dashboard import wc_dashboard_data, work_centers_store, awards

    class _Loc:
        name = "Repair 1"
    monkeypatch.setattr(wc_dashboard_data, "_load_wc", lambda nm: _Loc())
    monkeypatch.setattr(work_centers_store, "groups", lambda loc: ["Repairs"])
    monkeypatch.setattr(
        awards, "goat",
        lambda group: {"name": "Christian", "day": _date(2026, 2, 15), "units": 100, "pph": 12.5},
    )
    # 50 today, GOAT pace = 100 * 0.5 = 50 — exactly on pace.
    monkeypatch.setattr(wc_dashboard_data, "_units_today_for_wc", lambda nm, d: 50)
    monkeypatch.setattr(wc_dashboard_data, "_shift_elapsed_fraction", lambda d: 0.5)

    out = wc_dashboard_data.goat_race("Repair 1", _date(2026, 5, 13))
    assert out["status"] == "ON_PACE"


def test_goat_race_no_goat_yet(monkeypatch):
    from zira_dashboard import wc_dashboard_data, work_centers_store, awards

    class _Loc:
        name = "Repair 1"
    monkeypatch.setattr(wc_dashboard_data, "_load_wc", lambda nm: _Loc())
    monkeypatch.setattr(work_centers_store, "groups", lambda loc: ["Repairs"])
    monkeypatch.setattr(awards, "goat", lambda group: None)
    monkeypatch.setattr(wc_dashboard_data, "_units_today_for_wc", lambda nm, d: 30)
    monkeypatch.setattr(wc_dashboard_data, "_shift_elapsed_fraction", lambda d: 0.5)

    out = wc_dashboard_data.goat_race("Repair 1", _date(2026, 5, 13))
    assert out["goat"] is None
    assert out["status"] is None
