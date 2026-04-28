"""Shift window helpers and target defaults, backed by schedule_store."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from . import schedule_store

SITE_TZ = ZoneInfo("America/Chicago")

# Target throughput per station per DAY (pallets). Edit as needed.
TARGET_PER_DAY = {
    "Dismantler": 325,
    "Repair": 220,
    "Other": 0,
}


def _sched():
    return schedule_store.current()


def shift_start() -> time:
    return _sched().shift_start


def shift_end() -> time:
    return _sched().shift_end


def work_weekdays() -> frozenset[int]:
    return _sched().work_weekdays


def breaks() -> tuple:
    """Tuple of Break objects (start, end, name)."""
    return _sched().breaks


def productive_minutes_per_day() -> int:
    """Shift minutes minus all scheduled breaks (lunch + cleanup included)."""
    def _mins(t): return t.hour * 60 + t.minute
    s, e = shift_start(), shift_end()
    total = _mins(e) - _mins(s)
    for b in breaks():
        total -= _mins(b.end) - _mins(b.start)
    return max(0, total)


def in_shift(local_dt: datetime) -> bool:
    if local_dt.weekday() not in work_weekdays():
        return False
    t = local_dt.time()
    if t < shift_start() or t >= shift_end():
        return False
    for b in breaks():
        if b.start <= t < b.end:
            return False
    return True


def shift_start_for(day: date) -> time:
    """Return the shift start for `day`, honoring per-day custom_hours
    overrides set in the per-day schedule. Falls back to the global
    schedule when no override is set."""
    # Lazy import to avoid the shift_config → staffing → schedule_store cycle.
    from . import staffing
    sched = staffing.load_schedule(day)
    ch = sched.custom_hours
    if ch and isinstance(ch.get("start"), str):
        try:
            return time.fromisoformat(ch["start"])
        except ValueError:
            pass
    return shift_start()


def shift_end_for(day: date) -> time:
    from . import staffing
    sched = staffing.load_schedule(day)
    ch = sched.custom_hours
    if ch and isinstance(ch.get("end"), str):
        try:
            return time.fromisoformat(ch["end"])
        except ValueError:
            pass
    return shift_end()


def breaks_for(day: date) -> tuple:
    """Return the breaks tuple for `day`, honoring per-day custom_hours.

    A custom_hours override with an empty `breaks` list means "no breaks
    today" — not "fall back to global." Only when custom_hours itself is
    None (or omits the breaks key) do we use the global break list.
    """
    from . import staffing
    from .schedule_store import Break
    sched = staffing.load_schedule(day)
    ch = sched.custom_hours
    if ch and isinstance(ch.get("breaks"), list):
        out = []
        for b in ch["breaks"]:
            if not isinstance(b, dict):
                continue
            try:
                bs = time.fromisoformat(b["start"])
                be = time.fromisoformat(b["end"])
            except (ValueError, KeyError, TypeError):
                continue
            name = str(b.get("name") or "Break")
            out.append(Break(bs, be, name))
        return tuple(out)
    return breaks()


def productive_minutes_for(day: date) -> int:
    """Total productive minutes for `day` (shift duration minus breaks),
    honoring custom_hours."""
    def _mins(t): return t.hour * 60 + t.minute
    s, e = shift_start_for(day), shift_end_for(day)
    total = _mins(e) - _mins(s)
    for b in breaks_for(day):
        total -= _mins(b.end) - _mins(b.start)
    return max(0, total)


def in_shift_on(local_dt: datetime) -> bool:
    """Day-aware twin of in_shift(): derives the day from local_dt and
    consults per-day custom_hours."""
    if local_dt.weekday() not in work_weekdays():
        return False
    day = local_dt.date()
    t = local_dt.time()
    if t < shift_start_for(day) or t >= shift_end_for(day):
        return False
    for b in breaks_for(day):
        if b.start <= t < b.end:
            return False
    return True


def shift_elapsed_minutes(day: date, now: datetime) -> int:
    """Productive shift minutes elapsed on `day` as of `now` (site-local).
    Honors per-day custom_hours."""
    if day.weekday() not in work_weekdays():
        return 0
    s = shift_start_for(day)
    e = shift_end_for(day)
    start = datetime.combine(day, s, tzinfo=SITE_TZ)
    end = datetime.combine(day, e, tzinfo=SITE_TZ)
    effective = min(now.astimezone(SITE_TZ), end)
    if effective <= start:
        return 0
    total = int((effective - start).total_seconds() // 60)
    for b in breaks_for(day):
        bs_dt = datetime.combine(day, b.start, tzinfo=SITE_TZ)
        be_dt = datetime.combine(day, b.end, tzinfo=SITE_TZ)
        lo = max(bs_dt, start)
        hi = min(be_dt, effective)
        if hi > lo:
            total -= int((hi - lo).total_seconds() // 60)
    return max(0, total)
