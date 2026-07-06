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
