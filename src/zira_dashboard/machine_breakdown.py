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

from dataclasses import dataclass
from datetime import date, datetime, timedelta

BREAKDOWN_NO_OUTPUT_MINUTES = 15
"""Default minutes of no output (while an operator is clocked in) before a
station is flagged as broken down."""


@dataclass(frozen=True)
class StationSignal:
    wc_name: str
    last_output_utc: datetime | None  # None = no output yet today
    has_operator: bool  # at least one operator currently clocked in on this WC


@dataclass(frozen=True)
class BreakdownCandidate:
    wc_name: str
    stop_utc: datetime


def detect(
    signals: list[StationSignal],
    now: datetime,
    shift_start_utc: datetime,
    shift_end_utc: datetime,
    no_output_minutes: int = BREAKDOWN_NO_OUTPUT_MINUTES,
) -> list[BreakdownCandidate]:
    """Pure. Which stations should open a NEW breakdown incident this tick.

    A station is a candidate when it has an operator clocked in AND has
    produced nothing for >= no_output_minutes (measured from its last output,
    or from shift start if it has never produced today) AND `now` is within
    shift hours. The caller is responsible for excluding stations that
    already have an open incident, an active testing window, or were
    recently dismissed without new output since -- this function only
    applies the no-output-while-staffed rule."""
    if now < shift_start_utc or now > shift_end_utc:
        return []
    threshold = timedelta(minutes=no_output_minutes)
    out: list[BreakdownCandidate] = []
    for sig in signals:
        if not sig.has_operator:
            continue
        stop = sig.last_output_utc or shift_start_utc
        if now - stop < threshold:
            continue
        out.append(BreakdownCandidate(wc_name=sig.wc_name, stop_utc=stop))
    return out


def departed_at(
    person_name: str,
    wc_name: str,
    punch_windows: dict[str, list[tuple]],
    stop_utc: datetime,
) -> datetime | None:
    """Pure. None if the person still has an open (or not-yet-closed-since-
    the-breakdown) punch on wc_name; otherwise the UTC time of their last
    closed punch window on wc_name at/after `stop_utc` -- i.e. when they left
    the broken machine (by transfer or clock-out). `punch_windows` matches
    assignment_windows.resolve_segments's punch_windows param shape:
    {person_name: [(wc_name, start_utc, end_utc|None), ...]}."""
    windows = [w for w in punch_windows.get(person_name, []) if w[0] == wc_name]
    relevant = [(s, e) for (_wc, s, e) in windows if e is None or e > stop_utc]
    if not relevant:
        return None
    if any(e is None for _, e in relevant):
        return None
    return max(e for _, e in relevant)


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
