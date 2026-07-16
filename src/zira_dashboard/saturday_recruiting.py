"""Pure domain rules for optional Saturday work recruiting."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .shift_config import SITE_TZ


class SaturdayRecruitingError(ValueError):
    """Base error for invalid Saturday recruiting domain inputs."""


class InvalidAvailability(SaturdayRecruitingError):
    """Raised when a partial Saturday commitment is outside allowed hours."""


@dataclass(frozen=True)
class Opening:
    wc_id: int
    wc_name: str
    requested_count: int
    required_skills: tuple[str, ...]


@dataclass(frozen=True)
class Commitment:
    person_id: int
    eligible_wc_ids: frozenset[int]


@dataclass(frozen=True)
class Coverage:
    total: int
    filled_by_wc: dict[int, int]
    wc_by_person: dict[int, int]


def response_deadline(
    day: date,
    work_weekdays: frozenset[int],
    shift_start_for: Callable[[date], time],
) -> datetime:
    """Return the prior configured workday's site-local shift start."""
    if day.weekday() != 5:
        raise SaturdayRecruitingError("Saturday recruiting requires a Saturday")
    cursor = day - timedelta(days=1)
    for _ in range(14):
        if cursor.weekday() in work_weekdays:
            return datetime.combine(cursor, shift_start_for(cursor), tzinfo=SITE_TZ)
        cursor -= timedelta(days=1)
    raise SaturdayRecruitingError("No prior configured plant workday")


def format_deadline(value: datetime) -> str:
    """Format the persisted deadline consistently for all employee surfaces."""
    local = value.astimezone(SITE_TZ)
    clock = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.strftime('%A, %B')} {local.day} at {clock}"


def format_time_range(start: time, end: time) -> str:
    """Format full or partial commitment hours as a concise range."""

    def clock(value: time) -> str:
        return datetime.combine(date.min, value).strftime("%I:%M %p").lstrip("0")

    return f"{clock(start)}–{clock(end)}"


def validate_availability(start: time, end: time, shift_start: time, shift_end: time) -> None:
    """Require an availability interval on half-hours inside the Saturday shift."""

    def on_half_hour(value: time) -> bool:
        return value.minute in (0, 30) and value.second == 0 and value.microsecond == 0

    if not on_half_hour(start) or not on_half_hour(end):
        raise InvalidAvailability("Availability must use 30-minute increments")
    if start < shift_start or end > shift_end or start >= end:
        raise InvalidAvailability("Availability must stay within the Saturday shift")


def eligible_work_centers(
    skill_levels: Mapping[str, int], openings: Sequence[Opening]
) -> frozenset[int]:
    """Return work centers whose every required skill is exactly level 2 or 3."""
    return frozenset(
        opening.wc_id
        for opening in openings
        if opening.required_skills
        and all(int(skill_levels.get(skill, 0)) in (2, 3) for skill in opening.required_skills)
    )


def match_commitments(
    openings: Sequence[Opening], commitments: Sequence[Commitment]
) -> Coverage | None:
    """Find deterministic coverage, rematching flexible people when required."""
    slots = [
        (opening.wc_id, index)
        for opening in sorted(openings, key=lambda opening: opening.wc_id)
        for index in range(opening.requested_count)
    ]
    by_person = {commitment.person_id: commitment for commitment in commitments}
    if len(by_person) != len(commitments) or len(by_person) > len(slots):
        return None

    person_for_slot: dict[tuple[int, int], int] = {}

    def assign(person_id: int, seen: set[tuple[int, int]]) -> bool:
        for slot in slots:
            if slot[0] not in by_person[person_id].eligible_wc_ids or slot in seen:
                continue
            seen.add(slot)
            prior = person_for_slot.get(slot)
            if prior is None or assign(prior, seen):
                person_for_slot[slot] = person_id
                return True
        return False

    for person_id in sorted(by_person):
        if not assign(person_id, set()):
            return None

    wc_by_person = {person_id: slot[0] for slot, person_id in person_for_slot.items()}
    filled_by_wc = {opening.wc_id: 0 for opening in openings}
    for wc_id in wc_by_person.values():
        filled_by_wc[wc_id] += 1
    return Coverage(len(by_person), filled_by_wc, wc_by_person)
