# Dashboards Date/Time Range Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a date/time range picker to the three dashboard tabs (Recycling VS, New VS, Work Centers) with presets (Today, Yesterday, This Week, Last Week, Last Month, Custom). Every dashboard metric and chart scopes to the chosen range, with per-day custom hours and breaks honored. "Today" remains the default and reproduces existing behavior.

**Architecture:** A new `DashboardRange` value object owns range semantics. A `range_picker` module parses query params into a range and exposes preset definitions. `leaderboard.py` grows a range-aware aggregator that calls the existing per-day fetch in parallel and combines results, clipping each day to the range window. `progress.py` grows a daily-aggregate helper for multi-day ranges. Routes consume the range, pick the right helper, and pass a small picker partial template into every dashboard view.

**Dependencies:** Polish bundle (`2026-04-28-dashboards-polish.md`) ships first. This plan inherits the polished single-day label format and the per-person headline calc.

**Tech Stack:** FastAPI / Jinja2 / pytest / dataclass / zoneinfo. No new external deps.

---

## File Structure

- `src/zira_dashboard/range_picker.py` — new: `DashboardRange` dataclass, preset definitions, `from_query_params()` parser
- `src/zira_dashboard/leaderboard.py` — modified: add `window_start_utc` / `window_end_utc` params to `fetch_station_day`; add `StationRangeTotal`, `fetch_station_range`, `leaderboard_range`
- `src/zira_dashboard/progress.py` — modified: add `progress_buckets_daily`
- `src/zira_dashboard/routes/value_streams.py` — modified: both handlers consume `DashboardRange`; pick single-day vs multi-day code paths; build per-day grace clipped to range
- `src/zira_dashboard/routes/dashboard.py` — modified: consume `DashboardRange`; aggregate metrics across range
- `src/zira_dashboard/templates/_dashboard_range_picker.html` — new: picker partial (chips + custom popover)
- `src/zira_dashboard/templates/recycling.html` — modified: include picker; branch progress chart on `is_single_day`; multi-day label collapse with `(N)` count; bar tick start/end logic
- `src/zira_dashboard/templates/new_vs.html` — modified: same template-level changes
- `src/zira_dashboard/templates/index.html` — modified: same template-level changes
- `tests/test_range_picker.py` — new: parser + presets
- `tests/test_leaderboard_range.py` — new: per-day window clipping + range aggregation
- `tests/test_progress_daily.py` — new: daily-aggregate buckets
- `tests/test_dashboards_range.py` — new: route-level integration

---

### Task 1: `DashboardRange` + preset parser

**Files:**
- Create: `src/zira_dashboard/range_picker.py`
- Test: `tests/test_range_picker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_range_picker.py
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from zira_dashboard.range_picker import DashboardRange, from_query_params

CT = ZoneInfo("America/Chicago")


def _fixed_now():
    # Wed, Apr 22, 2026, 09:00 local
    return datetime(2026, 4, 22, 9, 0, tzinfo=CT)


def test_today_preset_uses_shift_start_to_now(monkeypatch):
    monkeypatch.setattr("zira_dashboard.range_picker._now_local", _fixed_now)
    monkeypatch.setattr("zira_dashboard.shift_config.shift_start_for", lambda d: time(7, 0))
    r = from_query_params({"preset": "today"})
    assert r.preset == "today"
    assert r.start_local.date() == date(2026, 4, 22)
    assert r.start_local.timetz() == time(7, 0, tzinfo=CT)
    assert r.end_local == _fixed_now()
    assert r.is_single_day is True
    assert r.is_today is True


def test_yesterday_preset_full_shift_day(monkeypatch):
    monkeypatch.setattr("zira_dashboard.range_picker._now_local", _fixed_now)
    monkeypatch.setattr("zira_dashboard.shift_config.shift_start_for", lambda d: time(7, 0))
    monkeypatch.setattr("zira_dashboard.shift_config.shift_end_for",   lambda d: time(15, 0))
    r = from_query_params({"preset": "yesterday"})
    assert r.start_local.date() == date(2026, 4, 21)
    assert r.start_local.timetz() == time(7, 0, tzinfo=CT)
    assert r.end_local.date() == date(2026, 4, 21)
    assert r.end_local.timetz() == time(15, 0, tzinfo=CT)
    assert r.is_single_day is True
    assert r.is_today is False


def test_this_week_starts_monday(monkeypatch):
    monkeypatch.setattr("zira_dashboard.range_picker._now_local", _fixed_now)  # Wed
    r = from_query_params({"preset": "this_week"})
    assert r.start_local.date() == date(2026, 4, 20)  # Mon
    assert r.start_local.timetz() == time(0, 0, tzinfo=CT)
    assert r.end_local == _fixed_now()
    assert r.is_single_day is False


def test_last_week_full_mon_to_sun(monkeypatch):
    monkeypatch.setattr("zira_dashboard.range_picker._now_local", _fixed_now)
    r = from_query_params({"preset": "last_week"})
    assert r.start_local.date() == date(2026, 4, 13)  # prev Mon
    assert r.end_local.date() == date(2026, 4, 19)    # prev Sun
    assert r.is_single_day is False


def test_last_month_previous_calendar_month(monkeypatch):
    monkeypatch.setattr("zira_dashboard.range_picker._now_local", _fixed_now)
    r = from_query_params({"preset": "last_month"})
    assert r.start_local.date() == date(2026, 3, 1)
    assert r.end_local.date() == date(2026, 3, 31)


def test_custom_day_legacy_day_param(monkeypatch):
    monkeypatch.setattr("zira_dashboard.range_picker._now_local", _fixed_now)
    monkeypatch.setattr("zira_dashboard.shift_config.shift_start_for", lambda d: time(7, 0))
    monkeypatch.setattr("zira_dashboard.shift_config.shift_end_for",   lambda d: time(15, 0))
    r = from_query_params({"day": "2026-04-15"})
    assert r.preset == "custom_day"
    assert r.start_local.date() == date(2026, 4, 15)
    assert r.end_local.date() == date(2026, 4, 15)
    assert r.is_single_day is True


def test_custom_window_with_times():
    r = from_query_params({
        "preset": "custom",
        "from": "2026-04-21T14:00",
        "to":   "2026-04-21T15:11",
    })
    assert r.is_single_day is True
    assert r.start_local.timetz() == time(14, 0, tzinfo=CT)
    assert r.end_local.timetz()   == time(15, 11, tzinfo=CT)


def test_no_params_defaults_to_today(monkeypatch):
    monkeypatch.setattr("zira_dashboard.range_picker._now_local", _fixed_now)
    monkeypatch.setattr("zira_dashboard.shift_config.shift_start_for", lambda d: time(7, 0))
    r = from_query_params({})
    assert r.preset == "today"
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_range_picker.py -v`
Expected: ImportError — module doesn't exist yet.

- [ ] **Step 3: Implement `range_picker.py`**

Create `src/zira_dashboard/range_picker.py`:

```python
"""Dashboard date/time range parser + presets.

A `DashboardRange` represents a (start_local, end_local) window for the
dashboard pages. Six presets exist; "today" is the default. The parser
accepts query params from any dashboard route and returns a normalized
range.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Mapping

from . import shift_config

CT = shift_config.SITE_TZ


def _now_local() -> datetime:
    return datetime.now(CT)


@dataclass(frozen=True)
class DashboardRange:
    start_local: datetime
    end_local:   datetime
    preset:      str
    label:       str

    @property
    def is_single_day(self) -> bool:
        return self.start_local.date() == self.end_local.date()

    @property
    def is_today(self) -> bool:
        return self.preset == "today"

    @property
    def days(self) -> list[date]:
        d, end = self.start_local.date(), self.end_local.date()
        out: list[date] = []
        while d <= end:
            out.append(d)
            d = d + timedelta(days=1)
        return out


def _today_range() -> DashboardRange:
    now = _now_local()
    s = shift_config.shift_start_for(now.date())
    start = datetime.combine(now.date(), s, tzinfo=CT)
    return DashboardRange(start, now, "today", "Today")


def _yesterday_range() -> DashboardRange:
    y = (_now_local() - timedelta(days=1)).date()
    s = shift_config.shift_start_for(y)
    e = shift_config.shift_end_for(y)
    return DashboardRange(
        datetime.combine(y, s, tzinfo=CT),
        datetime.combine(y, e, tzinfo=CT),
        "yesterday", "Yesterday",
    )


def _this_week_range() -> DashboardRange:
    now = _now_local()
    monday = now.date() - timedelta(days=now.weekday())
    return DashboardRange(
        datetime.combine(monday, time(0, 0), tzinfo=CT),
        now, "this_week", "This Week",
    )


def _last_week_range() -> DashboardRange:
    now = _now_local()
    this_mon = now.date() - timedelta(days=now.weekday())
    last_mon = this_mon - timedelta(days=7)
    last_sun = last_mon + timedelta(days=6)
    return DashboardRange(
        datetime.combine(last_mon, time(0, 0), tzinfo=CT),
        datetime.combine(last_sun, time(23, 59, 59), tzinfo=CT),
        "last_week", "Last Week",
    )


def _last_month_range() -> DashboardRange:
    now = _now_local()
    first_this = now.date().replace(day=1)
    last_prev = first_this - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    return DashboardRange(
        datetime.combine(first_prev, time(0, 0), tzinfo=CT),
        datetime.combine(last_prev,  time(23, 59, 59), tzinfo=CT),
        "last_month", "Last Month",
    )


def _custom_day_range(day_str: str) -> DashboardRange:
    d = date.fromisoformat(day_str)
    s = shift_config.shift_start_for(d)
    e = shift_config.shift_end_for(d)
    return DashboardRange(
        datetime.combine(d, s, tzinfo=CT),
        datetime.combine(d, e, tzinfo=CT),
        "custom_day", d.strftime("%b %-d, %Y") if hasattr(d, "strftime") else str(d),
    )


def _custom_range(from_str: str, to_str: str) -> DashboardRange:
    s = datetime.fromisoformat(from_str).replace(tzinfo=CT)
    e = datetime.fromisoformat(to_str).replace(tzinfo=CT)
    if s.date() == e.date():
        label = f"{s.strftime('%b %d')}, {s.strftime('%H:%M')}–{e.strftime('%H:%M')}"
    else:
        label = f"{s.strftime('%b %d')} – {e.strftime('%b %d, %Y')}"
    return DashboardRange(s, e, "custom", label)


def from_query_params(params: Mapping[str, str]) -> DashboardRange:
    preset = params.get("preset")
    if preset == "today":
        return _today_range()
    if preset == "yesterday":
        return _yesterday_range()
    if preset == "this_week":
        return _this_week_range()
    if preset == "last_week":
        return _last_week_range()
    if preset == "last_month":
        return _last_month_range()
    if preset == "custom" and "from" in params and "to" in params:
        return _custom_range(params["from"], params["to"])
    if preset == "custom_day" and "day" in params:
        return _custom_day_range(params["day"])

    # Legacy: ?day=YYYY-MM-DD without preset = custom_day
    if "day" in params and params["day"]:
        return _custom_day_range(params["day"])

    return _today_range()
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_range_picker.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/range_picker.py tests/test_range_picker.py
git commit -m "feat(range): add DashboardRange parser and presets"
```

---

### Task 2: Per-day window clipping in `fetch_station_day`

**Files:**
- Modify: `src/zira_dashboard/leaderboard.py`
- Test: `tests/test_leaderboard_range.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_leaderboard_range.py
from datetime import date, datetime, time, timezone
from unittest.mock import MagicMock

import pytest

from zira_dashboard.leaderboard import fetch_station_day, day_window_utc
from zira_dashboard.stations import Station


def _mk_client(rows):
    c = MagicMock()
    c.get_readings.return_value = {"data": rows, "lastValue": None}
    return c


def test_window_clips_samples_outside_range(monkeypatch):
    monkeypatch.setattr("zira_dashboard.shift_config.shift_start_for", lambda d: time(7, 0))
    monkeypatch.setattr("zira_dashboard.shift_config.shift_end_for",   lambda d: time(15, 0))
    monkeypatch.setattr("zira_dashboard.shift_config.work_weekdays",   lambda: frozenset({0,1,2,3,4}))
    rows = [
        {"event_date": "2026-04-22T13:00:00Z", "units": 1, "status": "Working", "duration": 0},
        {"event_date": "2026-04-22T14:30:00Z", "units": 1, "status": "Working", "duration": 0},  # in window
        {"event_date": "2026-04-22T15:30:00Z", "units": 1, "status": "Working", "duration": 0},  # out
    ]
    client = _mk_client(rows)
    s = Station(meter_id="m", name="WC-1", category="Repair", cell="Recycling")
    start_iso, end_iso = day_window_utc(date(2026, 4, 22))
    win_start = datetime(2026, 4, 22, 19, 0, tzinfo=timezone.utc)  # 14:00 CT
    win_end   = datetime(2026, 4, 22, 20, 11, tzinfo=timezone.utc) # 15:11 CT
    result = fetch_station_day(
        client, s, start_iso, end_iso,
        now_utc=None,
        window_start_utc=win_start, window_end_utc=win_end,
    )
    # Only the 14:30 sample (= 19:30 UTC) is inside the window
    assert result.units == 1
    assert len(result.samples) == 1
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_leaderboard_range.py::test_window_clips_samples_outside_range -v`
Expected: TypeError — extra kwargs not yet supported.

- [ ] **Step 3: Add window params to `fetch_station_day`**

In `src/zira_dashboard/leaderboard.py`, change the signature:

```python
def fetch_station_day(
    client: ZiraClient,
    station: Station,
    start_iso: str,
    end_iso: str,
    now_utc: datetime | None = None,
    window_start_utc: datetime | None = None,
    window_end_utc:   datetime | None = None,
) -> StationTotal:
```

Inside, just before the existing `samples.sort(...)` line, add filtering:

```python
if window_start_utc is not None or window_end_utc is not None:
    ws = window_start_utc or datetime.min.replace(tzinfo=timezone.utc)
    we = window_end_utc   or datetime.max.replace(tzinfo=timezone.utc)
    samples       = [s for s in samples       if ws <= s[0] < we]
    downtime_rows = [d for d in downtime_rows if ws <= d[0] < we]
```

Also adjust `eval_end` to be capped by the window end:

```python
eval_end = min(shift_end_local.astimezone(timezone.utc), end_of_day)
if now_utc is not None:
    eval_end = min(eval_end, now_utc)
if window_end_utc is not None:
    eval_end = min(eval_end, window_end_utc)
```

And cap `eval_start` symmetrically (new variable) only when needed for the active-interval lookup — but since `_active_intervals` is built from already-filtered samples, no further change needed.

The `total` aggregate is computed before filtering. Recompute `total` from the filtered samples after windowing:

```python
if window_start_utc is not None or window_end_utc is not None:
    ws = window_start_utc or datetime.min.replace(tzinfo=timezone.utc)
    we = window_end_utc   or datetime.max.replace(tzinfo=timezone.utc)
    samples       = [s for s in samples       if ws <= s[0] < we]
    downtime_rows = [d for d in downtime_rows if ws <= d[0] < we]
    total         = sum(u for _, u in samples)
    count         = len(samples)
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_leaderboard_range.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/leaderboard.py tests/test_leaderboard_range.py
git commit -m "feat(range): clip samples and downtime to optional window in fetch_station_day"
```

---

### Task 3: `StationRangeTotal` + range aggregator

**Files:**
- Modify: `src/zira_dashboard/leaderboard.py`
- Test: `tests/test_leaderboard_range.py`

- [ ] **Step 1: Write failing test**

```python
def test_leaderboard_range_aggregates_two_days(monkeypatch):
    from zira_dashboard.range_picker import DashboardRange
    from zira_dashboard.leaderboard import leaderboard_range
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
    monkeypatch.setattr("zira_dashboard.shift_config.shift_start_for", lambda d: time(7, 0))
    monkeypatch.setattr("zira_dashboard.shift_config.shift_end_for",   lambda d: time(15, 0))
    monkeypatch.setattr("zira_dashboard.shift_config.work_weekdays",   lambda: frozenset({0,1,2,3,4}))

    # Stub fetch_station_day to return controlled per-day totals
    from zira_dashboard.leaderboard import StationTotal
    s1 = Station(meter_id="m", name="WC-1", category="Repair", cell="Recycling")
    calls = {}
    def fake_fetch(client, station, start_iso, end_iso, now_utc=None,
                   window_start_utc=None, window_end_utc=None):
        d = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).date()
        calls[d] = (window_start_utc, window_end_utc)
        units = 10 if d == date(2026, 4, 20) else 20
        return StationTotal(station, units=units, reading_count=1, truncated=False,
                            downtime_minutes=2, active_minutes=60,
                            last_reading_at=None, last_status=None,
                            samples=(), active_intervals=())
    monkeypatch.setattr("zira_dashboard.leaderboard.fetch_station_day", fake_fetch)

    r = DashboardRange(
        start_local=datetime(2026, 4, 20, 7, 0, tzinfo=CT),
        end_local=  datetime(2026, 4, 21, 15, 0, tzinfo=CT),
        preset="custom", label="Apr 20–21",
    )
    results = leaderboard_range(MagicMock(), [s1], r, now_utc=None)
    assert len(results) == 1
    assert results[0].units == 30
    assert results[0].downtime_minutes == 4
    assert date(2026, 4, 20) in calls and date(2026, 4, 21) in calls
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_leaderboard_range.py::test_leaderboard_range_aggregates_two_days -v`

- [ ] **Step 3: Add `StationRangeTotal` + `leaderboard_range`**

In `src/zira_dashboard/leaderboard.py`:

```python
@dataclass(frozen=True)
class StationRangeTotal:
    station: Station
    units: int
    reading_count: int
    truncated: bool
    downtime_minutes: int
    active_minutes: int
    last_reading_at: datetime | None
    last_status: str | None
    samples: tuple[tuple[datetime, int], ...]
    active_intervals: tuple[tuple[datetime, datetime], ...]
    per_day: tuple[tuple[date, StationTotal], ...]


def fetch_station_range(
    client: ZiraClient,
    station: Station,
    range,  # DashboardRange — avoid import cycle, duck-type
    now_utc: datetime | None = None,
) -> StationRangeTotal:
    per_day: list[tuple[date, StationTotal]] = []
    for d in range.days:
        if d.weekday() not in __import__(
            "zira_dashboard.shift_config", fromlist=["work_weekdays"]
        ).work_weekdays():
            continue
        start_iso, end_iso = day_window_utc(d)
        # Compute the day's window: range ∩ that-day's-shift, in UTC
        from .shift_config import SITE_TZ, shift_start_for, shift_end_for
        day_shift_start = datetime.combine(d, shift_start_for(d), tzinfo=SITE_TZ)
        day_shift_end   = datetime.combine(d, shift_end_for(d),   tzinfo=SITE_TZ)
        win_s = max(range.start_local, day_shift_start).astimezone(timezone.utc)
        win_e = min(range.end_local,   day_shift_end).astimezone(timezone.utc)
        if win_e <= win_s:
            continue
        per_day.append((d, fetch_station_day(
            client, station, start_iso, end_iso,
            now_utc=now_utc,
            window_start_utc=win_s, window_end_utc=win_e,
        )))
    units = sum(t.units for _, t in per_day)
    reading_count = sum(t.reading_count for _, t in per_day)
    truncated = any(t.truncated for _, t in per_day)
    downtime = sum(t.downtime_minutes for _, t in per_day)
    active = sum(t.active_minutes for _, t in per_day)
    samples = tuple(s for _, t in per_day for s in t.samples)
    intervals = tuple(i for _, t in per_day for i in t.active_intervals)
    last_t = per_day[-1][1] if per_day else None
    return StationRangeTotal(
        station=station, units=units, reading_count=reading_count, truncated=truncated,
        downtime_minutes=downtime, active_minutes=active,
        last_reading_at=last_t.last_reading_at if last_t else None,
        last_status=last_t.last_status if last_t else None,
        samples=samples, active_intervals=intervals,
        per_day=tuple(per_day),
    )


def leaderboard_range(
    client: ZiraClient,
    stations: list[Station],
    range,  # DashboardRange
    now_utc: datetime | None = None,
) -> list[StationRangeTotal]:
    with ThreadPoolExecutor(max_workers=min(10, len(stations) or 1)) as pool:
        results = list(pool.map(
            lambda s: fetch_station_range(client, s, range, now_utc),
            stations,
        ))
    results.sort(key=lambda r: (-r.units, r.station.name))
    return results
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_leaderboard_range.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/leaderboard.py tests/test_leaderboard_range.py
git commit -m "feat(range): add StationRangeTotal and leaderboard_range aggregator"
```

---

### Task 4: `progress_buckets_daily` for multi-day ranges

**Files:**
- Modify: `src/zira_dashboard/progress.py`
- Test: `tests/test_progress_daily.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_progress_daily.py
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from zira_dashboard.progress import progress_buckets_daily
from zira_dashboard.range_picker import DashboardRange

CT = ZoneInfo("America/Chicago")


class _FakeStationRangeTotal:
    def __init__(self, station, per_day):
        self.station = station
        self.per_day = per_day  # tuple[(date, StationTotal-ish), ...]


class _FakePerDay:
    def __init__(self, units, active_minutes):
        self.units = units
        self.active_minutes = active_minutes


def test_daily_buckets_one_per_workday(monkeypatch):
    monkeypatch.setattr("zira_dashboard.shift_config.shift_start_for", lambda d: time(7, 0))
    monkeypatch.setattr("zira_dashboard.shift_config.shift_end_for",   lambda d: time(15, 0))
    monkeypatch.setattr("zira_dashboard.shift_config.work_weekdays",   lambda: frozenset({0,1,2,3,4}))
    monkeypatch.setattr("zira_dashboard.shift_config.breaks_for", lambda d: ())
    monkeypatch.setattr("zira_dashboard.settings_store.station_target", lambda s: 60)

    r = DashboardRange(
        start_local=datetime(2026, 4, 20, 0, 0, tzinfo=CT),  # Mon
        end_local=  datetime(2026, 4, 22, 23, 59, tzinfo=CT),  # Wed
        preset="custom", label="3 days",
    )

    class _S:
        name = "WC-1"
    g = [_FakeStationRangeTotal(_S(), per_day=(
        (date(2026,4,20), _FakePerDay(units=100, active_minutes=480)),
        (date(2026,4,21), _FakePerDay(units=120, active_minutes=480)),
        (date(2026,4,22), _FakePerDay(units= 50, active_minutes=240)),
    ))]
    buckets = progress_buckets_daily(g, r, now_utc=datetime(2026,4,22,12,0,tzinfo=timezone.utc))
    assert len(buckets) == 3
    assert buckets[0]["label"].startswith("Mon") or "4/20" in buckets[0]["label"]
    assert buckets[0]["actual"] == 100
    assert buckets[2]["in_progress"] is True
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_progress_daily.py -v`

- [ ] **Step 3: Add `progress_buckets_daily`**

In `src/zira_dashboard/progress.py`, append:

```python
def progress_buckets_daily(
    group: Iterable["StationRangeTotal"],
    range,  # DashboardRange
    now_utc: datetime,
) -> list[dict]:
    """One bucket per workday in `range`. Bar height = units. Target line =
    sum across stations of (per-day target/hr × per-day active hours)."""
    from . import settings_store
    from .shift_config import SITE_TZ, work_weekdays, shift_start_for, shift_end_for

    group = list(group)
    if not group:
        return []
    today_local = now_utc.astimezone(SITE_TZ).date()
    # Index per_day by date for O(1) lookup
    per_day_idx: list[dict[date, object]] = []
    for st in group:
        per_day_idx.append({d: t for d, t in st.per_day})

    out: list[dict] = []
    for d in range.days:
        if d.weekday() not in work_weekdays():
            continue
        actual = sum(
            (idx.get(d).units if idx.get(d) else 0) for idx in per_day_idx
        )
        # Target = sum across stations of hourly-target × per-day active hours
        target = 0.0
        for st, idx in zip(group, per_day_idx):
            t = idx.get(d)
            if not t:
                continue
            hrs = t.active_minutes / 60.0
            target += settings_store.station_target(st.station) * hrs
        in_progress = d == today_local
        out.append({
            "label": d.strftime("%a %-m/%-d") if hasattr(d, "strftime") else str(d),
            "actual": actual,
            "target": int(round(target if not in_progress else actual)),
            "in_progress": in_progress,
        })
    return out
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_progress_daily.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/progress.py tests/test_progress_daily.py
git commit -m "feat(range): daily-aggregate progress buckets for multi-day ranges"
```

---

### Task 5: Range picker partial template

**Files:**
- Create: `src/zira_dashboard/templates/_dashboard_range_picker.html`

- [ ] **Step 1: Create the partial**

```html
{# Inputs: range (DashboardRange), current_path (str) #}
<div class="range-picker" data-current="{{ range.preset }}">
  <a href="{{ current_path }}?preset=today"      class="chip{% if range.preset == 'today' %} active{% endif %}">Today</a>
  <a href="{{ current_path }}?preset=yesterday"  class="chip{% if range.preset == 'yesterday' %} active{% endif %}">Yesterday</a>
  <a href="{{ current_path }}?preset=this_week"  class="chip{% if range.preset == 'this_week' %} active{% endif %}">This Week</a>
  <a href="{{ current_path }}?preset=last_week"  class="chip{% if range.preset == 'last_week' %} active{% endif %}">Last Week</a>
  <a href="{{ current_path }}?preset=last_month" class="chip{% if range.preset == 'last_month' %} active{% endif %}">Last Month</a>
  <button type="button" class="chip{% if range.preset in ('custom','custom_day') %} active{% endif %}"
          onclick="document.getElementById('range-custom-popover').classList.toggle('open')">
    Custom ▾
  </button>

  <div id="range-custom-popover" class="popover">
    <form method="get" action="{{ current_path }}">
      <input type="hidden" name="preset" value="custom">
      <label>From <input type="date" name="from_date" required></label>
      <label>Time <input type="time" name="from_time" placeholder="optional"></label>
      <label>To   <input type="date" name="to_date"   required></label>
      <label>Time <input type="time" name="to_time"   placeholder="optional"></label>
      <div class="row">
        <button type="button" onclick="this.closest('.popover').classList.remove('open')">Cancel</button>
        <button type="submit" class="primary"
                onclick="
                  var f=this.form;
                  f.elements['from'].value = f.from_date.value + 'T' + (f.from_time.value || '00:00');
                  f.elements['to'].value   = f.to_date.value   + 'T' + (f.to_time.value   || '23:59');
                  return true;">
          Apply
        </button>
      </div>
      <input type="hidden" name="from">
      <input type="hidden" name="to">
    </form>
  </div>
</div>

<style>
  .range-picker { display: flex; gap: 0.35rem; align-items: center; flex-wrap: wrap; }
  .range-picker .chip {
    display: inline-block; padding: 0.25rem 0.6rem; border-radius: 999px;
    background: var(--panel); border: 1px solid var(--border, #444);
    color: var(--fg); font-size: 0.85rem; text-decoration: none; cursor: pointer;
  }
  .range-picker .chip.active { background: var(--accent, #4a8); color: #000; border-color: transparent; }
  .range-picker .popover { display: none; position: absolute; z-index: 50;
    margin-top: 0.4rem; background: var(--panel); border: 1px solid var(--border, #444);
    padding: 0.6rem; border-radius: 6px; }
  .range-picker .popover.open { display: block; }
  .range-picker .popover label { display: flex; gap: 0.4rem; align-items: center; margin: 0.25rem 0; }
  .range-picker .popover .row { display: flex; gap: 0.4rem; justify-content: flex-end; margin-top: 0.6rem; }
</style>
```

- [ ] **Step 2: Commit**

```bash
git add src/zira_dashboard/templates/_dashboard_range_picker.html
git commit -m "feat(range): add range picker partial"
```

---

### Task 6: Wire range into the recycling route

**Files:**
- Modify: `src/zira_dashboard/routes/value_streams.py`
- Modify: `src/zira_dashboard/templates/recycling.html`
- Test: `tests/test_dashboards_range.py`

- [ ] **Step 1: Write failing integration test**

```python
# tests/test_dashboards_range.py
from datetime import datetime, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient

from zira_dashboard.app import app


def test_recycling_renders_picker_with_today_active():
    client = TestClient(app)
    html = client.get("/recycling").text
    assert "range-picker" in html
    assert ">Today<" in html and "active" in html


def test_recycling_accepts_yesterday_preset():
    client = TestClient(app)
    resp = client.get("/recycling?preset=yesterday")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_dashboards_range.py -v`

- [ ] **Step 3: Update `recycling` handler to consume `DashboardRange`**

In `src/zira_dashboard/routes/value_streams.py`, replace the current single-day flow with range-aware logic. The handler signature changes from `day: str | None` to accepting all picker params:

```python
@router.get("/recycling", response_class=HTMLResponse)
def recycling(request: Request,
              preset: str | None = Query(default=None),
              day:    str | None = Query(default=None),
              **_extra):
    from .. import range_picker
    from ..leaderboard import leaderboard_range
    range_ = range_picker.from_query_params(dict(request.query_params))
    is_today = range_.is_today
    stations = recycling_stations()
    now = datetime.now(timezone.utc)
    results = leaderboard_range(client, stations, range_, now_utc=now if is_today else None)
    # ... existing aggregation code, but adapted to use `range_` for grace
    #     window per-day and `is_single_day` to choose progress helper
```

The body changes:

a) **Schedule lookup** — for the "active WC" filter and `who_by_wc`, gather across all days in the range:

```python
schedules = {d: staffing.load_schedule(d) for d in range_.days}
who_by_wc: dict[str, set[str]] = {}
people_by_wc_total: dict[str, int] = {}
operator_set_by_wc: dict[str, set[str]] = {}
for d, sched in schedules.items():
    for wc_name, ops in sched.assignments.items():
        if wc_name == staffing.TIME_OFF_KEY or not ops:
            continue
        operator_set_by_wc.setdefault(wc_name, set()).update(o.strip().lower() for o in ops if o)
        people_by_wc_total[wc_name] = people_by_wc_total.get(wc_name, 0) + len(ops)
        if range_.is_single_day:
            who_by_wc[wc_name] = " + ".join(ops)
```

For multi-day ranges, the per-row label uses just the WC name plus `(N)` where `N = len(operator_set_by_wc[wc_name])`. Single-day uses `who_by_wc[wc_name]`.

b) **Active WC filter** — same threshold, but applied to the range-aggregated units:

```python
ACTIVE_UNITS_THRESHOLD = 5
active_wc_names: set[str] = set(operator_set_by_wc.keys())
for r in results:
    if r.units > ACTIVE_UNITS_THRESHOLD:
        active_wc_names.add(r.station.name)
```

c) **Per-day grace** — replace the single `grace_interval_utc` with a list of clipped grace intervals across the range:

```python
graces_per_day: list[tuple[datetime, datetime]] = []
grace_start_local_for_label = None  # for single-day "start ·" tick
for d in range_.days:
    if d.weekday() not in shift_config.work_weekdays():
        continue
    s_local = datetime.combine(d, shift_config.shift_start_for(d), tzinfo=shift_config.SITE_TZ)
    g_end_local = s_local + timedelta(minutes=60)
    # Clip to range
    win_s = max(range_.start_local, s_local)
    win_e = min(range_.end_local, g_end_local)
    if is_today and d == now.astimezone(shift_config.SITE_TZ).date():
        win_e = min(win_e, now.astimezone(shift_config.SITE_TZ))
    if win_e > win_s:
        graces_per_day.append(
            (win_s.astimezone(timezone.utc), win_e.astimezone(timezone.utc))
        )
        if grace_start_local_for_label is None:
            grace_start_local_for_label = s_local
```

d) **Productive intervals per WC** — grace becomes a list, append all that apply:

```python
productive_by_wc: dict[str, list[tuple[datetime, datetime]]] = {}
for r in active_results:
    ints = list(r.active_intervals)
    if r.station.name in operator_set_by_wc:  # scheduled on at least one day
        ints.extend(graces_per_day)
    productive_by_wc[r.station.name] = _subtract_breaks(_merge(ints))
```

`_subtract_breaks` needs to know about all days' breaks, not just one. Build `breaks_utc` as a union across days:

```python
breaks_utc: list[tuple[datetime, datetime]] = []
for d in range_.days:
    for b in shift_config.breaks_for(d):
        bs = datetime.combine(d, b.start, tzinfo=shift_config.SITE_TZ).astimezone(timezone.utc)
        be = datetime.combine(d, b.end,   tzinfo=shift_config.SITE_TZ).astimezone(timezone.utc)
        if be > bs:
            breaks_utc.append((bs, be))
```

e) **Elapsed minutes** — generalize `shift_elapsed_minutes` to a range-aware version:

```python
def _range_productive_minutes(range_) -> int:
    total = 0
    for d in range_.days:
        if d.weekday() not in shift_config.work_weekdays():
            continue
        s_local = datetime.combine(d, shift_config.shift_start_for(d), tzinfo=shift_config.SITE_TZ)
        e_local = datetime.combine(d, shift_config.shift_end_for(d),   tzinfo=shift_config.SITE_TZ)
        win_s = max(range_.start_local, s_local)
        win_e = min(range_.end_local, e_local)
        if is_today and d == now.astimezone(shift_config.SITE_TZ).date():
            win_e = min(win_e, now.astimezone(shift_config.SITE_TZ))
        if win_e > win_s:
            mins = int((win_e - win_s).total_seconds() // 60)
            for b in shift_config.breaks_for(d):
                bs = datetime.combine(d, b.start, tzinfo=shift_config.SITE_TZ)
                be = datetime.combine(d, b.end,   tzinfo=shift_config.SITE_TZ)
                lo = max(bs, win_s); hi = min(be, win_e)
                if hi > lo:
                    mins -= int((hi - lo).total_seconds() // 60)
            total += max(0, mins)
    return total

elapsed = _range_productive_minutes(range_)
```

f) **Progress chart** — branch on `range_.is_single_day`:

```python
if range_.is_single_day:
    # use existing progress_buckets, but need to adapt to range_ (single day)
    # plus the per-day grace_end_local.
    dism_progress  = progress_buckets(...)
    repair_progress = progress_buckets(...)
else:
    from ..progress import progress_buckets_daily
    dism_progress   = progress_buckets_daily(dismantlers, range_, now_utc=now)
    repair_progress = progress_buckets_daily(repairs,    range_, now_utc=now)
```

g) **Headcount denominator** — sum `people_by_wc_total[wc]` for `wc in active_wc_names`.

h) **Template context** — pass `range_`, `is_single_day`, `operator_count_by_wc` (the new `(N)` map), `current_path = "/recycling"`.

- [ ] **Step 4: Update `recycling.html` to use the picker + multi-day branches**

Add at the top of the page body (above existing tile rows):

```html
{% set current_path = "/recycling" %}
{% include "_dashboard_range_picker.html" %}
```

In the `bar_chart` macro, when `is_single_day` is False, render labels as:

```html
<div class="name">
  <span class="name-primary">{{ b.name }}</span>
  <span class="name-secondary">({{ b.operator_count or 0 }})</span>
</div>
```

Branch the progress chart's tick interval — daily mode uses one label per bucket (since each bucket already represents a day), so the `% 2 == 0` check from the polish bundle is bypassed.

Branch the bar widget's axis tick:

```html
{% if widget_target_pct is not none and is_single_day %}
  <div class="bar-row axis-row numpos-{{ numpos }}">
    <div></div>
    <div class="axis-track">
      {% if shift_start_label %}<div class="axis-tick axis-start" style="left: 0%">start · {{ shift_start_label }}</div>{% endif %}
      <div class="axis-tick" style="left: {{ widget_target_pct }}%">
        {{ 'now' if range.is_today else 'end' }} · {{ now_label if range.is_today else end_label }}
      </div>
    </div>
    {% if numpos == 'widget' %}<div></div>{% endif %}
  </div>
{% endif %}
```

Pass `end_label = range_.end_local.strftime("%H:%M")` from the route.

- [ ] **Step 5: Run, verify PASS**

Run: `pytest tests/test_dashboards_range.py -v` and the existing `pytest tests/ -k recycling -v` to make sure nothing regressed.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/value_streams.py src/zira_dashboard/templates/recycling.html tests/test_dashboards_range.py
git commit -m "feat(range): wire range picker into /recycling"
```

---

### Task 7: Wire range into the new_vs route

**Files:**
- Modify: `src/zira_dashboard/routes/value_streams.py` (the `new_vs` handler)
- Modify: `src/zira_dashboard/templates/new_vs.html`

- [ ] **Step 1: Apply the same pattern as Task 6 to the `new_vs` handler**

Same six adaptations: `range_picker.from_query_params(...)`, `leaderboard_range(...)`, range-aware schedules / active filter / grace / breaks / elapsed / progress chart / headcount.

- [ ] **Step 2: Update `new_vs.html`**

Same template changes as `recycling.html` from Task 6 — include the picker, branch labels on `is_single_day`, branch progress chart on `is_single_day`, branch bar widget axis ticks.

- [ ] **Step 3: Verify with route tests**

Add to `tests/test_dashboards_range.py`:

```python
def test_new_vs_renders_picker_with_today_active():
    client = TestClient(app)
    html = client.get("/new-vs").text
    assert "range-picker" in html


def test_new_vs_accepts_this_week_preset():
    client = TestClient(app)
    resp = client.get("/new-vs?preset=this_week")
    assert resp.status_code == 200
```

Run: `pytest tests/test_dashboards_range.py -v`

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/value_streams.py src/zira_dashboard/templates/new_vs.html tests/test_dashboards_range.py
git commit -m "feat(range): wire range picker into /new-vs"
```

---

### Task 8: Wire range into the Work Centers (index) route

**Files:**
- Modify: `src/zira_dashboard/routes/dashboard.py`
- Modify: `src/zira_dashboard/templates/index.html`

- [ ] **Step 1: Update the index handler**

Same range-aware pattern. The Work Centers page shows per-station tiles instead of bar widgets, but the `units` / `downtime_minutes` / `last_reading_at` semantics are identical to the recycling page — just consume `StationRangeTotal` instead of `StationTotal`. The "running / stopped / offline" state on the index page is only meaningful on Today; for past or windowed ranges, render the cell as muted "n/a" or hide it.

- [ ] **Step 2: Add picker include**

In `templates/index.html`, add at the top of the page body:

```html
{% set current_path = "/" %}
{% include "_dashboard_range_picker.html" %}
```

- [ ] **Step 3: Add route test**

```python
def test_index_renders_picker():
    client = TestClient(app)
    html = client.get("/").text
    assert "range-picker" in html


def test_index_accepts_last_month_preset():
    client = TestClient(app)
    resp = client.get("/?preset=last_month")
    assert resp.status_code == 200
```

Run: `pytest tests/test_dashboards_range.py -v`

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/dashboard.py src/zira_dashboard/templates/index.html tests/test_dashboards_range.py
git commit -m "feat(range): wire range picker into Work Centers (/)"
```

---

### Task 9: Multi-day label collapse with `(N)` count

**Files:**
- Modify: `src/zira_dashboard/routes/value_streams.py` (both handlers)
- Modify: `src/zira_dashboard/templates/recycling.html`, `new_vs.html`
- Test: `tests/test_dashboards_range.py`

This was implemented inside Task 6/7 as part of the wiring; this task is the test+verify step to ensure it works end-to-end across the multi-day code path.

- [ ] **Step 1: Add test**

```python
def test_multi_day_range_collapses_labels_to_wc_with_count(monkeypatch):
    from datetime import date
    from zira_dashboard import staffing
    monkeypatch.setattr(staffing, "load_schedule", lambda d: staffing.Schedule(
        day=d, published=True,
        assignments={"Repair-1": ["Alice"]} if d == date(2026, 4, 20)
                     else {"Repair-1": ["Bob"]},
    ))
    # ... patch leaderboard_range similarly
    client = TestClient(app)
    html = client.get("/recycling?preset=last_week").text
    # Repair-1 had 2 distinct ops across the week
    assert "Repair-1" in html and "(2)" in html
    # No "(no assignment)" italic in multi-day flow
    assert "(no assignment)" not in html
```

- [ ] **Step 2: Run, fix any wiring gaps**

Run: `pytest tests/test_dashboards_range.py::test_multi_day_range_collapses_labels_to_wc_with_count -v`

If it fails, the most likely issues are:
- `operator_count_by_wc` not being passed to the template
- Template not branching on `is_single_day`

Fix in `routes/value_streams.py` and the templates accordingly.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/routes/value_streams.py src/zira_dashboard/templates/recycling.html src/zira_dashboard/templates/new_vs.html tests/test_dashboards_range.py
git commit -m "feat(range): multi-day rows show WC name with operator-count suffix"
```

---

### Task 10: Bar widget tick: `start` / `end` for past/windowed; hidden for multi-day

**Files:**
- Modify: `src/zira_dashboard/routes/value_streams.py` (both handlers)
- Modify: `src/zira_dashboard/templates/recycling.html`, `new_vs.html`
- Test: `tests/test_dashboards_range.py`

- [ ] **Step 1: Write failing test for end-tick behavior**

```python
def test_past_day_shows_end_tick_not_now_tick(monkeypatch):
    client = TestClient(app)
    html = client.get("/recycling?preset=yesterday").text
    assert "end ·" in html
    assert "now ·" not in html


def test_multi_day_hides_both_ticks(monkeypatch):
    client = TestClient(app)
    html = client.get("/recycling?preset=last_week").text
    assert "now ·" not in html
    assert "start ·" not in html
    assert "end ·" not in html
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_dashboards_range.py -k tick -v`

- [ ] **Step 3: Implement the branch**

In the bar_chart macro of both `recycling.html` and `new_vs.html`, replace the axis-row block with:

```html
{% if widget_target_pct is not none and is_single_day %}
  <div class="bar-row axis-row numpos-{{ numpos }}">
    <div></div>
    <div class="axis-track">
      {% if shift_start_label %}
        <div class="axis-tick axis-start" style="left: 0%">start · {{ shift_start_label }}</div>
      {% endif %}
      <div class="axis-tick" style="left: {{ widget_target_pct }}%">
        {% if range.is_today %}now · {{ now_label }}{% else %}end · {{ end_label }}{% endif %}
      </div>
    </div>
    {% if numpos == 'widget' %}<div></div>{% endif %}
  </div>
{% endif %}
```

Pass `end_label = range_.end_local.strftime("%H:%M")` and `range = range_` from both handlers.

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_dashboards_range.py -k tick -v`

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/recycling.html src/zira_dashboard/templates/new_vs.html src/zira_dashboard/routes/value_streams.py tests/test_dashboards_range.py
git commit -m "feat(range): bar widget tick shows start/end for past/windowed; hidden for multi-day"
```

---

### Task 11: End-to-end smoke test across all presets

**Files:**
- Test: `tests/test_dashboards_range.py`

- [ ] **Step 1: Add smoke test**

```python
import pytest


@pytest.mark.parametrize("path", ["/", "/recycling", "/new-vs"])
@pytest.mark.parametrize("preset", ["today", "yesterday", "this_week", "last_week", "last_month"])
def test_every_dashboard_renders_every_preset(path, preset):
    client = TestClient(app)
    resp = client.get(f"{path}?preset={preset}")
    assert resp.status_code == 200, f"{path}?preset={preset} -> {resp.status_code}"
    assert "range-picker" in resp.text


def test_custom_window_with_times():
    client = TestClient(app)
    resp = client.get("/recycling?preset=custom&from=2026-04-21T14:00&to=2026-04-21T15:11")
    assert resp.status_code == 200
    assert "end ·" in resp.text  # sub-day windowed
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -v`

Investigate and fix any newly-failing tests. Common gotchas:
- `range_.end_local` for "today" can be `now`, which means `widget_target_pct = 100%` always — verify the `now ·` tick still renders inside the bar (not falling off the right edge)
- Some tests stub `staffing.load_schedule` per-day; range code calls it for every day in the range, so stubs must accept a `day` arg

- [ ] **Step 3: Commit**

```bash
git add tests/test_dashboards_range.py
git commit -m "test(range): full preset × dashboard smoke matrix"
```

---

## Done criteria

All eleven tasks committed; `pytest tests/ -v` is green; manual verification in the browser:

- Visiting `/`, `/recycling`, `/new-vs` defaults to "Today" and looks identical to pre-feature output
- Clicking each preset chip changes the data scope
- Custom popover applies arbitrary date and date+time windows
- Multi-day ranges show per-day progress bars and `(N)` operator-count labels
- Past day shows `end ·` tick on bar widgets; multi-day hides ticks entirely
- Legacy `?day=YYYY-MM-DD` URLs still work and highlight no chip (custom_day)
