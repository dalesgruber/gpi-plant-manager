from __future__ import annotations

from datetime import date

from zira_dashboard import staffing
from zira_dashboard.rotation_suggestions import (
    TRIM_SAW_SKILL,
    TrimSawHistory,
    _valid_trim_saw_pair,
    suggest_trim_saw_pair,
)


TARGET_DAY = date(2026, 7, 6)


def person(name: str, level: int, *, active: bool = True, reserve: bool = False):
    return staffing.Person(
        name=name,
        active=active,
        reserve=reserve,
        skills={TRIM_SAW_SKILL: level},
    )


def empty_history():
    return TrimSawHistory(appearance_counts={}, most_recent_names=set())


def test_valid_trim_saw_pair_rules():
    assert _valid_trim_saw_pair(3, 1) is True
    assert _valid_trim_saw_pair(3, 0) is True
    assert _valid_trim_saw_pair(2, 2) is True
    assert _valid_trim_saw_pair(2, 1) is False
    assert _valid_trim_saw_pair(1, 1) is False
    assert _valid_trim_saw_pair(0, 2) is False


def test_level_three_default_can_pair_with_level_one():
    roster = [person("Jesus Martinez", 3), person("Rosa", 1), person("Carlos", 2)]

    pair = suggest_trim_saw_pair(
        TARGET_DAY,
        roster,
        pinned_names=["Jesus Martinez"],
        unavailable_names=[],
        history=empty_history(),
    )

    assert pair == ["Jesus Martinez", "Carlos"]
    assert _valid_trim_saw_pair(3, 2)


def test_level_two_default_gets_level_two_or_three_partner():
    roster = [person("Jesus Martinez", 2), person("Luis", 1), person("Rosa", 2)]

    pair = suggest_trim_saw_pair(
        TARGET_DAY,
        roster,
        pinned_names=["Jesus Martinez"],
        unavailable_names=[],
        history=empty_history(),
    )

    assert pair == ["Jesus Martinez", "Rosa"]


def test_level_one_default_requires_level_three_partner():
    roster = [person("Jesus Martinez", 1), person("Luis", 2), person("Rosa", 3)]

    pair = suggest_trim_saw_pair(
        TARGET_DAY,
        roster,
        pinned_names=["Jesus Martinez"],
        unavailable_names=[],
        history=empty_history(),
    )

    assert pair == ["Jesus Martinez", "Rosa"]


def test_recent_history_reduces_candidate_rank():
    roster = [person("Alicia", 3), person("Beatriz", 3), person("Carlos", 2)]
    history = TrimSawHistory(
        appearance_counts={"Alicia": 4, "Beatriz": 0, "Carlos": 0},
        most_recent_names={"Alicia"},
    )

    pair = suggest_trim_saw_pair(
        TARGET_DAY,
        roster,
        pinned_names=[],
        unavailable_names=[],
        history=history,
    )

    assert pair == ["Beatriz", "Carlos"]


def test_level_three_still_outranks_level_two_when_similarly_due():
    roster = [person("Alicia", 3), person("Beatriz", 2), person("Carlos", 2)]

    pair = suggest_trim_saw_pair(
        TARGET_DAY,
        roster,
        pinned_names=[],
        unavailable_names=[],
        history=empty_history(),
    )

    assert pair[0] == "Alicia"
    assert set(pair) == {"Alicia", "Beatriz"}


def test_unavailable_and_reserve_people_are_excluded():
    roster = [
        person("Pinned Off", 3),
        person("Reserve Pro", 3, reserve=True),
        person("Available Pro", 3),
        person("Available Two", 2),
    ]

    pair = suggest_trim_saw_pair(
        TARGET_DAY,
        roster,
        pinned_names=["Pinned Off"],
        unavailable_names=["Pinned Off"],
        history=empty_history(),
    )

    assert pair == ["Available Pro", "Available Two"]


def test_no_safe_pair_returns_partial_assignment():
    roster = [person("Jesus Martinez", 1), person("Luis", 2)]

    pair = suggest_trim_saw_pair(
        TARGET_DAY,
        roster,
        pinned_names=["Jesus Martinez"],
        unavailable_names=[],
        history=empty_history(),
    )

    assert pair == ["Jesus Martinez"]


def test_history_uses_published_snapshot_when_present():
    from zira_dashboard.rotation_suggestions import _history_from_schedule_rows

    rows = [
        {
            "day": date(2026, 7, 3),
            "assignments": {"Trim Saw 1": ["Draft Person"]},
            "published_snapshot": {"assignments": {"Trim Saw 1": ["Posted Person"]}},
        },
        {
            "day": date(2026, 7, 2),
            "assignments": {"Trim Saw 1": ["Posted Person", "Other"]},
            "published_snapshot": None,
        },
    ]

    history = _history_from_schedule_rows(rows)

    assert history.appearance_counts == {"Posted Person": 2, "Other": 1}
    assert history.most_recent_names == {"Posted Person"}


def test_load_trim_saw_history_queries_only_recent_non_testing_rows(monkeypatch):
    from zira_dashboard import db
    from zira_dashboard.rotation_suggestions import _load_trim_saw_history

    captured = {}

    def fake_query(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "day": date(2026, 7, 3),
                "assignments": {"Trim Saw 1": ["Alicia"]},
                "published_snapshot": None,
            }
        ]

    monkeypatch.setattr(db, "query", fake_query)

    history = _load_trim_saw_history(date(2026, 7, 6))

    assert history.appearance_counts == {"Alicia": 1}
    assert history.most_recent_names == {"Alicia"}
    assert "LIMIT %s" in captured["sql"]
    assert captured["params"] == (date(2026, 7, 6), 20)


def test_smart_defaults_replaces_only_trim_saw_and_excludes_full_day_time_off(monkeypatch):
    from zira_dashboard import rotation_suggestions

    roster = [
        person("Jesus Martinez", 3),
        person("Off Person", 3),
        person("Rotation Two", 2),
        person("Repair Default", 2),
    ]
    base = {
        "Trim Saw 1": ["Jesus Martinez", "Off Person"],
        "Repair 1": ["Repair Default"],
    }

    monkeypatch.setattr(
        rotation_suggestions,
        "_load_trim_saw_history",
        lambda day: empty_history(),
    )

    smart = rotation_suggestions.smart_defaults_for_day(
        TARGET_DAY,
        roster,
        base,
        time_off_entries=[{"name": "Off Person", "hours": None}],
    )

    assert smart["Trim Saw 1"] == ["Jesus Martinez", "Rotation Two"]
    assert smart["Repair 1"] == ["Repair Default"]
    assert base["Trim Saw 1"] == ["Jesus Martinez", "Off Person"]


def test_smart_defaults_excludes_people_already_defaulted_elsewhere(monkeypatch):
    from zira_dashboard import rotation_suggestions

    roster = [
        person("Jesus Martinez", 3),
        person("Repair Default", 3),
        person("Rotation Two", 2),
    ]
    base = {
        "Trim Saw 1": ["Jesus Martinez"],
        "Repair 1": ["Repair Default"],
    }

    monkeypatch.setattr(
        rotation_suggestions,
        "_load_trim_saw_history",
        lambda day: empty_history(),
    )

    smart = rotation_suggestions.smart_defaults_for_day(TARGET_DAY, roster, base, [])

    assert smart["Trim Saw 1"] == ["Jesus Martinez", "Rotation Two"]
