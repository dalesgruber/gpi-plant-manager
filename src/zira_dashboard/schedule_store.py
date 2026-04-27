"""Plant work schedule: shift hours, work days, breaks. Persisted to schedule.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from threading import RLock

SCHEDULE_PATH = Path("schedule.json")

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class Break:
    start: time
    end: time
    name: str = "Break"


@dataclass(frozen=True)
class Schedule:
    shift_start: time
    shift_end: time
    work_weekdays: frozenset[int]   # 0=Mon .. 6=Sun
    breaks: tuple[Break, ...]


DEFAULT_SCHEDULE = Schedule(
    shift_start=time(7, 0),
    shift_end=time(15, 30),
    work_weekdays=frozenset({0, 1, 2, 3, 4}),
    breaks=(
        Break(time(9, 0), time(9, 15), "Morning break"),
        Break(time(11, 0), time(11, 30), "Lunch"),
        Break(time(13, 30), time(13, 45), "Afternoon break"),
        Break(time(15, 15), time(15, 30), "Cleanup"),
    ),
)


_lock = RLock()
_cached: Schedule | None = None


def _parse_time(s: str | None) -> time | None:
    if not isinstance(s, str):
        return None
    try:
        hh, mm = s.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return None


def _format_time(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _from_dict(d: dict) -> Schedule | None:
    try:
        start = _parse_time(d.get("shift_start")) or DEFAULT_SCHEDULE.shift_start
        end = _parse_time(d.get("shift_end")) or DEFAULT_SCHEDULE.shift_end
        wd_raw = d.get("work_weekdays") or []
        wd = frozenset(int(x) for x in wd_raw if isinstance(x, (int, str)) and str(x).strip().lstrip("-").isdigit() and 0 <= int(x) <= 6)
        if not wd:
            wd = DEFAULT_SCHEDULE.work_weekdays
        brk_raw = d.get("breaks") or []
        brks = []
        for b in brk_raw:
            if not isinstance(b, dict):
                continue
            bs = _parse_time(b.get("start"))
            be = _parse_time(b.get("end"))
            if not (bs and be) or be <= bs:
                continue
            name = str(b.get("name") or "Break")[:40]
            brks.append(Break(bs, be, name))
        brks.sort(key=lambda b: b.start)
        return Schedule(start, end, wd, tuple(brks))
    except (TypeError, ValueError):
        return None


def _to_dict(s: Schedule) -> dict:
    return {
        "shift_start": _format_time(s.shift_start),
        "shift_end": _format_time(s.shift_end),
        "work_weekdays": sorted(s.work_weekdays),
        "breaks": [
            {"start": _format_time(b.start), "end": _format_time(b.end), "name": b.name}
            for b in s.breaks
        ],
    }


def _load_from_disk() -> Schedule:
    if not SCHEDULE_PATH.exists():
        return DEFAULT_SCHEDULE
    try:
        raw = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return DEFAULT_SCHEDULE
    sched = _from_dict(raw) if isinstance(raw, dict) else None
    return sched or DEFAULT_SCHEDULE


def current() -> Schedule:
    global _cached
    with _lock:
        if _cached is None:
            _cached = _load_from_disk()
        return _cached


def save(sched: Schedule) -> None:
    global _cached
    with _lock:
        SCHEDULE_PATH.write_text(json.dumps(_to_dict(sched), indent=2), encoding="utf-8")
        _cached = sched


def reload() -> Schedule:
    global _cached
    with _lock:
        _cached = _load_from_disk()
        return _cached
