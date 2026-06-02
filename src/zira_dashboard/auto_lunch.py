"""Auto-lunch worker: sign employees out for lunch and back in, creating the
unpaid gap in Odoo. Fixed schedules use the day's Lunch break; Odoo-flexible
schedules trigger on elapsed time since first clock-in. One lunch per day.

The decision logic (decide / lunch_window_for_day / flex_window) is pure and
unit-testable. run_tick() wires the I/O (settings, schedule, open-attendance
cache, punch log) around it.

See docs/superpowers/specs/2026-06-02-auto-lunch-timeclock-design.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import shift_config

_log = logging.getLogger(__name__)

TERMINAL = ("done", "skipped", "ended_by_employee")


@dataclass(frozen=True)
class Window:
    out_at: datetime
    in_at: datetime


@dataclass(frozen=True)
class Transition:
    new_state: str
    action: str | None  # None | 'clock_out' | 'clock_in'
    at: datetime | None = None


def lunch_window_for_day(breaks, day: date) -> Window | None:
    """(out_at, in_at) for the break named 'lunch' on `day` in site-local tz,
    or None if there's no lunch break. `breaks` is shift_config.breaks_for(day)."""
    for b in breaks:
        if (getattr(b, "name", "") or "").strip().lower() == "lunch":
            out_at = datetime.combine(day, b.start, tzinfo=shift_config.SITE_TZ)
            in_at = datetime.combine(day, b.end, tzinfo=shift_config.SITE_TZ)
            return Window(out_at, in_at)
    return None


def flex_window(first_clock_in: datetime, after_hours: float, minutes: int) -> Window:
    out_at = first_clock_in + timedelta(hours=float(after_hours))
    in_at = out_at + timedelta(minutes=int(minutes))
    return Window(out_at, in_at)


def decide(run_state: str, is_clocked_in: bool, window: Window, now: datetime) -> Transition:
    """One state-machine step. See the spec's Part-5 table."""
    if run_state == "pending":
        if now >= window.out_at:
            if is_clocked_in:
                return Transition("auto_out", "clock_out", window.out_at)
            return Transition("skipped", None)
        return Transition("pending", None)
    if run_state == "auto_out":
        if now >= window.in_at:
            if not is_clocked_in:
                return Transition("done", "clock_in", window.in_at)
            return Transition("done", None)
        return Transition("auto_out", None)
    return Transition(run_state, None)
