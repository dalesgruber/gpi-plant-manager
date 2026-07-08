"""Machine breakdown detection and exclusion math for the Exception Inbox.

Mirrors missing_wc.py's role for its category, but with more state: a
breakdown incident persists (machine_breakdowns), tracks per-operator
snoozes (breakdown_snoozes), and drives a per-operator time exclusion
(wc_time_attributions source='breakdown') that mirrors the existing
source='testing' mechanism -- except testing zeroes UNITS (credited to no
one) while a breakdown zeroes EXPECTED minutes (units earned before the
breakdown are kept).
"""

from __future__ import annotations

from datetime import date, datetime


def excluded_minutes_for_windows(
    windows: list[tuple[datetime, datetime | None]],
    day: date,
    productive_minutes_in_window,
) -> float:
    """Pure. Sum of productive_minutes_in_window(day, start, end) over each
    CLOSED [start, end) window (end is not None and end > start); open or
    zero/negative-span windows are skipped. `productive_minutes_in_window`
    is injected (matches shift_config.productive_minutes_in_window's
    signature) so this is testable without shift config or timezones,
    mirroring routes/leaderboards.py's averages_for_wc DI style."""
    total = 0.0
    for start, end in windows:
        if end is None or end <= start:
            continue
        total += productive_minutes_in_window(day, start, end)
    return total


def excluded_minutes_overlapping(
    windows: list[tuple[datetime, datetime | None]],
    start_utc: datetime,
    end_utc: datetime,
    now_utc: datetime,
    day: date,
    productive_minutes_in_window,
) -> float:
    """Pure. Sum of productive_minutes_in_window(day, lo, hi) for the overlap
    of each breakdown window (open windows capped at now_utc) with
    [start_utc, end_utc). Used to shrink one work segment's productive
    minutes (recycling per-WC expected) to honor a breakdown exclusion,
    without needing a whole-day total."""
    clipped: list[tuple[datetime, datetime]] = []
    for w_start, w_end in windows:
        w_end = w_end if w_end is not None else now_utc
        lo = max(w_start, start_utc)
        hi = min(w_end, end_utc)
        if hi > lo:
            clipped.append((lo, hi))
    return excluded_minutes_for_windows(clipped, day, productive_minutes_in_window)
