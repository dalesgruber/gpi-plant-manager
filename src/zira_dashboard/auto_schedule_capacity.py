from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class AutoExpansion:
    unassigned_people: int
    centers_to_enable: int | None
    usable_centers: tuple[str, ...]


@dataclass(frozen=True)
class MinimumCrewBalance:
    """How enabled minimum staffing compares with people waiting."""

    unassigned_people: int
    open_minimum_slots: int
    direction: str
    center_count: int
    slot_delta: int
    recommended_centers: tuple[str, ...]


def analyze_minimum_crew_balance(
    *,
    unassigned_people: int,
    enabled_centers: Sequence[str],
    disabled_centers: Sequence[str],
    open_minimum_slots_by_center: Mapping[str, int],
    center_order: Mapping[str, int],
) -> MinimumCrewBalance:
    """Recommend the fewest On/Off changes using minimum crew slots only."""
    waiting = max(0, int(unassigned_people))
    enabled = tuple(dict.fromkeys(enabled_centers))
    disabled = tuple(dict.fromkeys(disabled_centers))
    slots = {
        name: max(0, int(open_minimum_slots_by_center.get(name, 0)))
        for name in (*enabled, *disabled)
    }
    open_minimum_slots = sum(slots[name] for name in enabled)
    delta = open_minimum_slots - waiting
    if delta == 0:
        return MinimumCrewBalance(waiting, open_minimum_slots, "ready", 0, 0, ())

    candidates = enabled if delta > 0 else disabled
    ordered = sorted(
        (name for name in candidates if slots[name] > 0),
        key=lambda name: (
            slots[name] if delta > 0 else -slots[name],
            center_order.get(name, 1_000_000),
            name.lower(),
        ),
    )
    covered = 0
    recommended = []
    for name in ordered:
        recommended.append(name)
        covered += slots[name]
        if covered >= abs(delta):
            break
    return MinimumCrewBalance(
        waiting,
        open_minimum_slots,
        "turn_off" if delta > 0 else "turn_on",
        len(recommended),
        abs(delta),
        tuple(recommended),
    )


def analyze_auto_expansion(
    *,
    unassigned_people: int,
    disabled_centers: Sequence[str],
    open_slots_by_center: Mapping[str, int],
    center_order: Mapping[str, int],
) -> AutoExpansion:
    remaining = max(0, int(unassigned_people))
    usable_names = tuple(
        sorted(
            (
                center
                for center in dict.fromkeys(disabled_centers)
                if int(open_slots_by_center.get(center, 0)) > 0
            ),
            key=lambda center: (
                -int(open_slots_by_center.get(center, 0)),
                center_order.get(center, 1_000_000),
                center.lower(),
            ),
        )
    )
    if remaining == 0:
        return AutoExpansion(0, 0, usable_names)

    covered = 0
    for count, center in enumerate(usable_names, start=1):
        covered += max(0, int(open_slots_by_center.get(center, 0)))
        if covered >= remaining:
            return AutoExpansion(remaining, count, usable_names)
    return AutoExpansion(remaining, None, usable_names)
