"""Pure rounding logic for timeclock punches.

Given a raw punch timestamp, the plant-wide scheduled shift start/end
for that day, and a RoundingSettings record, returns the rounded
timestamp — or the original if no rounding rule applies.

Rounding always pulls TOWARD the scheduled boundary, never away. A 20-min
in_before window means a clock_in up to 20 min before scheduled start
rounds UP to start. Punches outside the window pass through unchanged.
Mid-shift transfer_in / transfer_out actions are never rounded — they're
not shift boundaries.

Assumption: shift_start and shift_end fall on the same site-local date
as occurred_at. Overnight shifts (where shift_end < shift_start) are not
supported; if GPI ever adds a 2nd or 3rd shift, this needs revisiting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .shift_config import SITE_TZ


@dataclass(frozen=True)
class RoundingSettings:
    """Plant-wide rounding windows, in minutes. Zero on all four = no rounding."""
    in_before_min: int
    in_after_min: int
    out_before_min: int
    out_after_min: int


def apply_rounding(
    action: str,
    occurred_at: datetime,
    shift_start: time,
    shift_end: time,
    settings: RoundingSettings,
) -> datetime:
    """Return the rounded UTC timestamp, or occurred_at unchanged if no
    rounding applies. occurred_at must be timezone-aware."""
    if action in ("transfer_in", "transfer_out"):
        return occurred_at
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")

    local = occurred_at.astimezone(SITE_TZ)
    local_date = local.date()

    if action == "clock_in":
        scheduled = datetime.combine(local_date, shift_start, tzinfo=SITE_TZ)
        window_start = scheduled - timedelta(minutes=settings.in_before_min)
        window_end = scheduled + timedelta(minutes=settings.in_after_min)
        if window_start <= local <= window_end:
            return scheduled.astimezone(occurred_at.tzinfo)
        return occurred_at

    if action == "clock_out":
        scheduled = datetime.combine(local_date, shift_end, tzinfo=SITE_TZ)
        window_start = scheduled - timedelta(minutes=settings.out_before_min)
        window_end = scheduled + timedelta(minutes=settings.out_after_min)
        if window_start <= local <= window_end:
            return scheduled.astimezone(occurred_at.tzinfo)
        return occurred_at

    return occurred_at
