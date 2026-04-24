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


def shift_elapsed_minutes(day: date, now: datetime) -> int:
    """Productive shift minutes elapsed on `day` as of `now` (site-local)."""
    if day.weekday() not in work_weekdays():
        return 0
    start = datetime.combine(day, shift_start(), tzinfo=SITE_TZ)
    end = datetime.combine(day, shift_end(), tzinfo=SITE_TZ)
    effective = min(now.astimezone(SITE_TZ), end)
    if effective <= start:
        return 0
    total = int((effective - start).total_seconds() // 60)
    for b in breaks():
        bs_dt = datetime.combine(day, b.start, tzinfo=SITE_TZ)
        be_dt = datetime.combine(day, b.end, tzinfo=SITE_TZ)
        lo = max(bs_dt, start)
        hi = min(be_dt, effective)
        if hi > lo:
            total -= int((hi - lo).total_seconds() // 60)
    return max(0, total)
