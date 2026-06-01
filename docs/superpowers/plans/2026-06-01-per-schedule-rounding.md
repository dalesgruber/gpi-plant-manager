# Per-Schedule Punch Rounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each Odoo work schedule carry its own punch-rounding windows (set in the app), with shift boundaries synced from Odoo — so transportation drivers get 20-min start rounding toward their 5:45 start and no end rounding, while the plant floor is untouched.

**Architecture:** The Odoo work schedule (`resource.calendar`) is the grouping key. A new `work_schedules` table holds, per configured schedule, the four rounding windows (app-owned, edited in settings) plus per-weekday shift boundaries (synced from Odoo). Each employee's `resource_calendar_id` is synced onto `people`. At punch time, `employee → resource_calendar_id → work_schedules override` resolves the boundaries + windows fed into the unchanged `apply_rounding`; anyone without a configured override falls back to today's `global_schedule` + `rounding_settings`.

**Tech Stack:** Python (FastAPI, psycopg via `zira_dashboard.db`), Postgres, Jinja2 templates, Odoo XML-RPC via `odoo_client.execute`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-01-per-schedule-rounding-design.md`

---

## Testing & verification note

This repo's pytest suite is **Postgres-backed** (tests `skipif` when `DATABASE_URL` is unset) and targets Python ≥3.10. Per the project's environment, the suite **cannot run on the local Python 3.9** — the `pytest` steps below run in CI / Railway (or any env with Postgres + py≥3.10). Locally, every task ends with a `python -m py_compile` gate that MUST pass before commit. DB-backed tests assume the schema has been applied to the test database (same assumption as the existing `tests/test_rounding_store.py`).

## File Structure

- `src/zira_dashboard/db.py` — **modify.** Add `work_schedules` table DDL + `people.resource_calendar_id` column to the schema script.
- `src/zira_dashboard/work_schedule_store.py` — **create.** Cached, RLock-guarded store for per-schedule overrides (mirrors `rounding_store` / `schedule_store`). Owns: `WorkScheduleOverride`, `get`, `all_overrides`, `create`, `save_rounding`, `refresh_synced`, `delete`, `reload`.
- `src/zira_dashboard/odoo_client.py` — **modify.** Add `resource_calendar_id` to `fetch_employees`; add `fetch_work_schedules`, `fetch_calendar_hours`, and the pure helpers `_float_to_hhmm`, `_calendar_hours_from_lines`.
- `src/zira_dashboard/odoo_sync.py` — **modify.** Write `people.resource_calendar_id` during the employee upsert (via `_m2o_id`); add `refresh_work_schedule_hours` and call it from `sync()`.
- `src/zira_dashboard/routes/timeclock.py` — **modify.** Add `_shift_for_punch` resolver; use it in `_open_log_row`.
- `src/zira_dashboard/routes/settings.py` — **modify.** GET context (`work_schedules`, `available_schedules`); POST `/settings/work_schedule_rounding`, `.../add`, `.../remove`; `_hours_display` helper.
- `src/zira_dashboard/templates/settings.html` — **modify.** Per-schedule rounding block + "Add schedule" control; relabel global rounding as the default.
- Tests: `tests/test_work_schedules_schema.py`, `tests/test_work_schedule_store.py`, `tests/test_odoo_calendar_hours.py`, `tests/test_odoo_sync_calendars.py`, `tests/test_shift_for_punch.py`, `tests/test_settings_work_schedule_rounding.py`.

---

## Task 1: Schema — `work_schedules` table + `people.resource_calendar_id`

**Files:**
- Modify: `src/zira_dashboard/db.py` (people ALTER block ~line 202; after `rounding_settings` INSERT ~line 736)
- Test: `tests/test_work_schedules_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_work_schedules_schema.py`:

```python
"""Schema presence checks for per-schedule rounding. Postgres-backed."""

import os

import pytest

from zira_dashboard import db

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)


def test_work_schedules_table_queryable():
    # Selecting every column should not raise; an empty table is fine.
    db.query(
        "SELECT resource_calendar_id, name, work_hours, in_before_min, "
        "in_after_min, out_before_min, out_after_min, last_synced_at, "
        "updated_at FROM work_schedules LIMIT 1"
    )


def test_people_has_resource_calendar_id():
    rows = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'people' AND column_name = 'resource_calendar_id'"
    )
    assert rows, "people.resource_calendar_id column is missing"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_work_schedules_schema.py -v`
Expected: FAIL — `relation "work_schedules" does not exist` (and the column assertion fails).

- [ ] **Step 3: Add the `people.resource_calendar_id` column**

In `src/zira_dashboard/db.py`, immediately after the existing people ALTERs (the `spanish_speaker` line, ~line 202):

```sql
ALTER TABLE people ADD COLUMN IF NOT EXISTS resource_calendar_id INTEGER;
```

- [ ] **Step 4: Add the `work_schedules` table**

In `src/zira_dashboard/db.py`, immediately after the `rounding_settings` block's `INSERT ... ON CONFLICT DO NOTHING;` line (~line 736), add:

```sql
-- Per-work-schedule rounding overrides (2026-06-01). One row per Odoo
-- working schedule (resource.calendar) that gets its own punch-rounding
-- windows. `work_hours` (per-weekday "HH:MM" boundaries) is synced FROM
-- Odoo; the four window columns are app-owned (set on the settings page).
-- Row existence == an active override; employees inherit it via
-- people.resource_calendar_id. Everyone else uses rounding_settings.
CREATE TABLE IF NOT EXISTS work_schedules (
  resource_calendar_id  INTEGER PRIMARY KEY,
  name                  TEXT NOT NULL DEFAULT '',
  work_hours            JSONB NOT NULL DEFAULT '{}'::jsonb,
  in_before_min         INT NOT NULL DEFAULT 0,
  in_after_min          INT NOT NULL DEFAULT 0,
  out_before_min        INT NOT NULL DEFAULT 0,
  out_after_min         INT NOT NULL DEFAULT 0,
  last_synced_at        TIMESTAMPTZ,
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_work_schedules_schema.py -v`
Expected: PASS (after the schema is re-applied to the test DB on startup/init).

- [ ] **Step 6: Local syntax gate**

Run: `python -m py_compile src/zira_dashboard/db.py`
Expected: no output, exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/db.py tests/test_work_schedules_schema.py
git commit -m "feat(timeclock): work_schedules table + people.resource_calendar_id"
```

---

## Task 2: `work_schedule_store.py` — cached per-schedule overrides

**Files:**
- Create: `src/zira_dashboard/work_schedule_store.py`
- Test: `tests/test_work_schedule_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_work_schedule_store.py`:

```python
"""Tests for work_schedule_store load/save/refresh/cache. Postgres-backed."""

import os
from datetime import time

import pytest

from zira_dashboard import db, work_schedule_store
from zira_dashboard.rounding import RoundingSettings

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)

CAL_ID = 990001  # a test calendar id unlikely to collide


@pytest.fixture(autouse=True)
def _clean():
    db.execute("DELETE FROM work_schedules WHERE resource_calendar_id = %s", (CAL_ID,))
    work_schedule_store.reload()
    yield
    db.execute("DELETE FROM work_schedules WHERE resource_calendar_id = %s", (CAL_ID,))
    work_schedule_store.reload()


def test_create_then_get_returns_zero_rounding():
    work_schedule_store.create(CAL_ID, "Drivers")
    ws = work_schedule_store.get(CAL_ID)
    assert ws is not None
    assert ws.name == "Drivers"
    assert ws.rounding == RoundingSettings(0, 0, 0, 0)
    assert ws.work_hours == {}


def test_save_rounding_updates_only_windows():
    work_schedule_store.create(CAL_ID, "Drivers")
    work_schedule_store.refresh_synced(CAL_ID, "Drivers 5:45", {"0": ["05:45", "14:30"]})
    work_schedule_store.save_rounding(CAL_ID, RoundingSettings(20, 0, 0, 0))
    ws = work_schedule_store.get(CAL_ID)
    assert ws.rounding == RoundingSettings(20, 0, 0, 0)
    # Hours + name (Odoo-owned) survive a rounding save.
    assert ws.work_hours == {0: (time(5, 45), time(14, 30))}
    assert ws.name == "Drivers 5:45"


def test_refresh_synced_updates_only_hours_and_name():
    work_schedule_store.create(CAL_ID, "Drivers")
    work_schedule_store.save_rounding(CAL_ID, RoundingSettings(20, 0, 0, 0))
    work_schedule_store.refresh_synced(CAL_ID, "Drivers 5:45", {"0": ["05:45", "14:30"]})
    ws = work_schedule_store.get(CAL_ID)
    # Windows (app-owned) survive a sync refresh.
    assert ws.rounding == RoundingSettings(20, 0, 0, 0)
    assert ws.work_hours == {0: (time(5, 45), time(14, 30))}


def test_get_missing_returns_none():
    assert work_schedule_store.get(CAL_ID) is None


def test_cache_invalidated_on_save():
    work_schedule_store.create(CAL_ID, "Drivers")
    work_schedule_store.get(CAL_ID)  # prime cache
    db.execute("UPDATE work_schedules SET in_before_min = 99 WHERE resource_calendar_id = %s", (CAL_ID,))
    assert work_schedule_store.get(CAL_ID).rounding.in_before_min == 0  # stale cache
    work_schedule_store.reload()
    assert work_schedule_store.get(CAL_ID).rounding.in_before_min == 99


def test_delete_removes_override():
    work_schedule_store.create(CAL_ID, "Drivers")
    work_schedule_store.delete(CAL_ID)
    assert work_schedule_store.get(CAL_ID) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_work_schedule_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'zira_dashboard.work_schedule_store'`.

- [ ] **Step 3: Write the store**

Create `src/zira_dashboard/work_schedule_store.py`:

```python
"""Per-work-schedule rounding overrides, cached in-process.

Each row mirrors one Odoo working schedule (resource.calendar) that has
been given its own punch-rounding windows. The shift boundaries
(`work_hours`) are synced FROM Odoo; the four rounding windows are owned by
the app (set on the settings page). Resolution at punch time reads the
in-process cache, so it never hits the DB on the hot path — same rationale
as rounding_store / schedule_store.

A row's existence == an active override. Employees inherit a schedule's
rounding by being assigned that resource.calendar in Odoo
(people.resource_calendar_id); anyone else falls back to the plant default
(rounding_settings + global_schedule).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import time
from threading import RLock

from .rounding import RoundingSettings


@dataclass(frozen=True)
class WorkScheduleOverride:
    resource_calendar_id: int
    name: str
    work_hours: dict[int, tuple[time, time]]   # weekday 0=Mon..6=Sun -> (start, end)
    rounding: RoundingSettings


def _parse_time(s) -> time | None:
    if isinstance(s, time):
        return s
    if not isinstance(s, str):
        return None
    try:
        hh, mm = s.split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return None


def _parse_work_hours(raw) -> dict[int, tuple[time, time]]:
    """Convert JSONB {"0": ["05:45","14:30"], ...} into
    {0: (time(5,45), time(14,30)), ...}. Skips malformed entries."""
    out: dict[int, tuple[time, time]] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        try:
            wd = int(k)
        except (TypeError, ValueError):
            continue
        if not (0 <= wd <= 6) or not isinstance(v, (list, tuple)) or len(v) != 2:
            continue
        start = _parse_time(v[0])
        end = _parse_time(v[1])
        if start is None or end is None:
            continue
        out[wd] = (start, end)
    return out


def _row_to_override(row: dict) -> WorkScheduleOverride:
    return WorkScheduleOverride(
        resource_calendar_id=int(row["resource_calendar_id"]),
        name=str(row.get("name") or ""),
        work_hours=_parse_work_hours(row.get("work_hours")),
        rounding=RoundingSettings(
            in_before_min=int(row["in_before_min"]),
            in_after_min=int(row["in_after_min"]),
            out_before_min=int(row["out_before_min"]),
            out_after_min=int(row["out_after_min"]),
        ),
    )


_lock = RLock()
_cache: dict[int, WorkScheduleOverride] | None = None


def _load_from_db() -> dict[int, WorkScheduleOverride]:
    from . import db
    rows = db.query(
        "SELECT resource_calendar_id, name, work_hours, "
        "in_before_min, in_after_min, out_before_min, out_after_min "
        "FROM work_schedules"
    )
    return {int(r["resource_calendar_id"]): _row_to_override(r) for r in rows}


def _all_cached() -> dict[int, WorkScheduleOverride]:
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load_from_db()
        return _cache


def get(resource_calendar_id: int) -> WorkScheduleOverride | None:
    """Override for an Odoo calendar id, or None. Cache read — safe on the
    punch hot path."""
    if resource_calendar_id is None:
        return None
    return _all_cached().get(int(resource_calendar_id))


def all_overrides() -> list[WorkScheduleOverride]:
    """All configured overrides, sorted by name (for the settings UI)."""
    return sorted(_all_cached().values(), key=lambda o: o.name.lower())


def create(resource_calendar_id: int, name: str = "") -> None:
    """Insert an override row (rounding all-zero) if it doesn't exist. Hours
    are filled by the next sync via refresh_synced()."""
    from . import db
    db.execute(
        "INSERT INTO work_schedules (resource_calendar_id, name) "
        "VALUES (%s, %s) ON CONFLICT (resource_calendar_id) DO NOTHING",
        (int(resource_calendar_id), (name or "")[:200]),
    )
    reload()


def save_rounding(resource_calendar_id: int, r: RoundingSettings) -> None:
    """Update ONLY the four rounding windows for one schedule. Leaves the
    Odoo-owned name + work_hours untouched. Inserts the row if missing."""
    from . import db
    db.execute(
        "INSERT INTO work_schedules "
        "(resource_calendar_id, name, in_before_min, in_after_min, "
        " out_before_min, out_after_min, updated_at) "
        "VALUES (%s, '', %s, %s, %s, %s, now()) "
        "ON CONFLICT (resource_calendar_id) DO UPDATE SET "
        "in_before_min = EXCLUDED.in_before_min, "
        "in_after_min = EXCLUDED.in_after_min, "
        "out_before_min = EXCLUDED.out_before_min, "
        "out_after_min = EXCLUDED.out_after_min, "
        "updated_at = now()",
        (int(resource_calendar_id), r.in_before_min, r.in_after_min,
         r.out_before_min, r.out_after_min),
    )
    reload()


def refresh_synced(resource_calendar_id: int, name: str, work_hours: dict) -> None:
    """Update ONLY the Odoo-owned name + work_hours + last_synced_at for an
    EXISTING override row. Leaves the app-owned rounding windows untouched.
    No-op if the override row doesn't exist (we don't auto-configure every
    Odoo calendar)."""
    from . import db
    db.execute(
        "UPDATE work_schedules SET name = %s, work_hours = %s::jsonb, "
        "last_synced_at = now() WHERE resource_calendar_id = %s",
        ((name or "")[:200], json.dumps(work_hours or {}), int(resource_calendar_id)),
    )
    reload()


def delete(resource_calendar_id: int) -> None:
    from . import db
    db.execute(
        "DELETE FROM work_schedules WHERE resource_calendar_id = %s",
        (int(resource_calendar_id),),
    )
    reload()


def reload() -> dict[int, WorkScheduleOverride]:
    """Force a fresh read from Postgres, bypassing the cache."""
    global _cache
    with _lock:
        _cache = _load_from_db()
        return _cache
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_work_schedule_store.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Local syntax gate**

Run: `python -m py_compile src/zira_dashboard/work_schedule_store.py`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/work_schedule_store.py tests/test_work_schedule_store.py
git commit -m "feat(timeclock): work_schedule_store for per-schedule rounding overrides"
```

---

## Task 3: Odoo client — read work schedules + per-weekday hours

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py` (`fetch_employees` ~line 197; add new functions near `fetch_departments` ~line 188)
- Test: `tests/test_odoo_calendar_hours.py`

- [ ] **Step 1: Write the failing test** (pure — exercises the derivation helpers, no Odoo/DB)

Create `tests/test_odoo_calendar_hours.py`:

```python
"""Pure tests for the per-weekday hours derivation. No Odoo/DB needed."""

from zira_dashboard.odoo_client import _float_to_hhmm, _calendar_hours_from_lines


def test_float_to_hhmm_basic():
    assert _float_to_hhmm(5.75) == "05:45"
    assert _float_to_hhmm(14.5) == "14:30"
    assert _float_to_hhmm(0.0) == "00:00"


def test_float_to_hhmm_rounds_to_nearest_minute_with_carry():
    # 5.7583h = 5h 45.5m -> rounds to 5:46
    assert _float_to_hhmm(5.7583) == "05:46"
    # 13.999h rounds up to 14:00 (carry across the hour)
    assert _float_to_hhmm(13.9999) == "14:00"


def test_calendar_hours_outer_boundary_for_lunch_split():
    # Two attendance lines on Monday (dayofweek "0"): morning + afternoon
    # around lunch. We keep the OUTER boundary: 05:45 .. 14:30.
    rows = [
        {"calendar_id": [7, "Drivers"], "dayofweek": "0", "hour_from": 5.75, "hour_to": 11.0},
        {"calendar_id": [7, "Drivers"], "dayofweek": "0", "hour_from": 11.5, "hour_to": 14.5},
        {"calendar_id": [7, "Drivers"], "dayofweek": "1", "hour_from": 5.75, "hour_to": 14.5},
    ]
    out = _calendar_hours_from_lines(rows)
    assert out == {
        7: {
            "0": ["05:45", "14:30"],
            "1": ["05:45", "14:30"],
        }
    }


def test_calendar_hours_skips_malformed_rows():
    rows = [
        {"calendar_id": False, "dayofweek": "0", "hour_from": 7.0, "hour_to": 15.0},
        {"calendar_id": [7, "X"], "dayofweek": "nope", "hour_from": 7.0, "hour_to": 15.0},
        {"calendar_id": [7, "X"], "dayofweek": "2", "hour_from": 7.0, "hour_to": 15.5},
    ]
    out = _calendar_hours_from_lines(rows)
    assert out == {7: {"2": ["07:00", "15:30"]}}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_odoo_calendar_hours.py -v`
Expected: FAIL — `ImportError: cannot import name '_float_to_hhmm'`.

- [ ] **Step 3: Add `resource_calendar_id` to `fetch_employees`**

In `src/zira_dashboard/odoo_client.py`, change the `fetch_employees` `fields` list (~line 200) from:

```python
        fields=["id", "name", "active", "work_email", "wage_type"],
```

to:

```python
        fields=["id", "name", "active", "work_email", "wage_type", "resource_calendar_id"],
```

- [ ] **Step 4: Add the helpers + fetch functions**

In `src/zira_dashboard/odoo_client.py`, immediately after `fetch_departments` (~line 188), add:

```python
def _float_to_hhmm(f) -> str:
    """Odoo stores working-schedule hours as floats (5.75 == 05:45). Round
    to the nearest minute, carrying into the hour, clamped to [00:00, 23:59]."""
    total = int(round(float(f) * 60))          # minutes since midnight
    total = max(0, min(total, 23 * 60 + 59))
    return f"{total // 60:02d}:{total % 60:02d}"


def _calendar_hours_from_lines(rows) -> dict:
    """Reduce resource.calendar.attendance rows to per-weekday OUTER shift
    boundaries: {cal_id: {"0": ["05:45","14:30"], ...}} with weekday keys
    0=Mon..6=Sun (Odoo's dayofweek convention, same as Python weekday()).
    A lunch split (two lines on one day) collapses to min(hour_from) ..
    max(hour_to). Malformed rows are skipped."""
    acc: dict = {}   # {cal_id: {weekday:int -> [min_from:float, max_to:float]}}
    for r in rows:
        cal = r.get("calendar_id")
        cal_id = cal[0] if isinstance(cal, (list, tuple)) and cal else cal
        if not isinstance(cal_id, int):
            continue
        try:
            wd = int(r.get("dayofweek"))
        except (TypeError, ValueError):
            continue
        if not (0 <= wd <= 6):
            continue
        hf = float(r.get("hour_from") or 0.0)
        ht = float(r.get("hour_to") or 0.0)
        day = acc.setdefault(cal_id, {}).get(wd)
        if day is None:
            acc[cal_id][wd] = [hf, ht]
        else:
            day[0] = min(day[0], hf)
            day[1] = max(day[1], ht)
    out: dict = {}
    for cal_id, days in acc.items():
        out[cal_id] = {
            str(wd): [_float_to_hhmm(lo), _float_to_hhmm(hi)]
            for wd, (lo, hi) in days.items()
        }
    return out


def fetch_work_schedules() -> list[dict]:
    """Active working schedules (resource.calendar): [{id, name}, ...]."""
    return execute(
        "resource.calendar", "search_read",
        [("active", "=", True)],
        fields=["id", "name"],
    )


def fetch_calendar_hours(calendar_ids) -> dict:
    """Per-weekday shift boundaries for the given resource.calendar ids,
    derived from their attendance lines. Returns
    {cal_id: {"0": ["05:45","14:30"], ...}}; empty dict for no ids."""
    if not calendar_ids:
        return {}
    rows = execute(
        "resource.calendar.attendance", "search_read",
        [("calendar_id", "in", list(calendar_ids))],
        fields=["calendar_id", "dayofweek", "hour_from", "hour_to"],
    )
    return _calendar_hours_from_lines(rows)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_odoo_calendar_hours.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Local syntax gate**

Run: `python -m py_compile src/zira_dashboard/odoo_client.py`
Expected: no output, exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/odoo_client.py tests/test_odoo_calendar_hours.py
git commit -m "feat(odoo): read work schedules + per-weekday calendar hours"
```

---

## Task 4: Sync — write `people.resource_calendar_id` + refresh schedule hours

**Files:**
- Modify: `src/zira_dashboard/odoo_sync.py` (add `_m2o_id`; people upsert ~line 118; add `refresh_work_schedule_hours`; call from `sync()`)
- Test: `tests/test_odoo_sync_calendars.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_odoo_sync_calendars.py`:

```python
"""Tests for sync helpers: many2one id extraction (pure) + schedule-hours
refresh (Postgres-backed, Odoo monkeypatched)."""

import os

import pytest

from zira_dashboard import odoo_sync


def test_m2o_id_extracts_id():
    assert odoo_sync._m2o_id([7, "Drivers"]) == 7
    assert odoo_sync._m2o_id(False) is None
    assert odoo_sync._m2o_id(None) is None
    assert odoo_sync._m2o_id([]) is None


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs Postgres")
def test_refresh_only_touches_configured_overrides(monkeypatch):
    from zira_dashboard import db, work_schedule_store, odoo_client
    from zira_dashboard.rounding import RoundingSettings

    cal_id = 990002
    db.execute("DELETE FROM work_schedules WHERE resource_calendar_id = %s", (cal_id,))
    work_schedule_store.reload()
    try:
        work_schedule_store.create(cal_id, "Drivers")
        work_schedule_store.save_rounding(cal_id, RoundingSettings(20, 0, 0, 0))

        monkeypatch.setattr(odoo_client, "fetch_work_schedules",
                            lambda: [{"id": cal_id, "name": "Drivers 5:45"}])
        monkeypatch.setattr(odoo_client, "fetch_calendar_hours",
                            lambda ids: {cal_id: {"0": ["05:45", "14:30"]}})

        odoo_sync.refresh_work_schedule_hours()

        ws = work_schedule_store.get(cal_id)
        assert ws.name == "Drivers 5:45"
        assert ws.work_hours[0][0].hour == 5 and ws.work_hours[0][0].minute == 45
        # Rounding (app-owned) untouched by the sync.
        assert ws.rounding == RoundingSettings(20, 0, 0, 0)
    finally:
        db.execute("DELETE FROM work_schedules WHERE resource_calendar_id = %s", (cal_id,))
        work_schedule_store.reload()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_odoo_sync_calendars.py -v`
Expected: FAIL — `AttributeError: module 'zira_dashboard.odoo_sync' has no attribute '_m2o_id'`.

- [ ] **Step 3: Add `_m2o_id` and `refresh_work_schedule_hours`**

In `src/zira_dashboard/odoo_sync.py`, add at module level (e.g. just below the imports / before `sync`):

```python
def _m2o_id(val):
    """Odoo many2one fields come back as [id, name] or False. Return the
    id, or None."""
    if isinstance(val, (list, tuple)) and val:
        return val[0]
    return None


def refresh_work_schedule_hours(only_ids=None) -> None:
    """Refresh the Odoo-owned name + per-weekday hours for the configured
    work_schedules overrides. Leaves the app-owned rounding windows alone.
    Best-effort: callers wrap in try/except so an Odoo hiccup never breaks
    the rest of the sync."""
    from . import work_schedule_store, odoo_client
    ids = [o.resource_calendar_id for o in work_schedule_store.all_overrides()]
    if only_ids is not None:
        wanted = {int(i) for i in only_ids}
        ids = [i for i in ids if i in wanted]
    if not ids:
        return
    names = {c["id"]: c.get("name") or "" for c in odoo_client.fetch_work_schedules()}
    hours = odoo_client.fetch_calendar_hours(ids)
    for cid in ids:
        work_schedule_store.refresh_synced(cid, names.get(cid, ""), hours.get(cid, {}))
```

- [ ] **Step 4: Write `people.resource_calendar_id` in the employee upsert**

In `src/zira_dashboard/odoo_sync.py`, replace the people upsert (~lines 118-127) — currently:

```python
            cur.execute(
                "INSERT INTO people (odoo_id, name, active, wage_type, spanish_speaker, last_pulled_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (odoo_id) DO UPDATE SET name = EXCLUDED.name, "
                "active = EXCLUDED.active, wage_type = EXCLUDED.wage_type, "
                "spanish_speaker = EXCLUDED.spanish_speaker, "
                "last_pulled_at = EXCLUDED.last_pulled_at",
                (emp["id"], _short_name(emp["name"]), bool(emp.get("active", True)),
                 wage_type, spanish_speaker, pulled_at),
            )
```

with:

```python
            cur.execute(
                "INSERT INTO people (odoo_id, name, active, wage_type, spanish_speaker, "
                "resource_calendar_id, last_pulled_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (odoo_id) DO UPDATE SET name = EXCLUDED.name, "
                "active = EXCLUDED.active, wage_type = EXCLUDED.wage_type, "
                "spanish_speaker = EXCLUDED.spanish_speaker, "
                "resource_calendar_id = EXCLUDED.resource_calendar_id, "
                "last_pulled_at = EXCLUDED.last_pulled_at",
                (emp["id"], _short_name(emp["name"]), bool(emp.get("active", True)),
                 wage_type, spanish_speaker, _m2o_id(emp.get("resource_calendar_id")),
                 pulled_at),
            )
```

- [ ] **Step 5: Call the refresh from `sync()`**

`odoo_sync.py` has no module logger yet. Add one near the top of the file, just below the existing imports:

```python
import logging

log = logging.getLogger(__name__)
```

Then, in `sync()`, immediately after the cache-bust line `staffing._invalidate_roster_cache()` (~line 191) and immediately before the final `return SyncResult(ok=True, refreshed=True, ...)` (~line 193), add:

```python
    # Best-effort: refresh per-schedule rounding overrides' hours from Odoo.
    # A failure here must not fail the (already-committed) employee sync.
    try:
        refresh_work_schedule_hours()
    except Exception:
        log.exception("refresh_work_schedule_hours failed during sync")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_odoo_sync_calendars.py -v`
Expected: PASS (2 tests; the DB-backed one skips without `DATABASE_URL`).

- [ ] **Step 7: Local syntax gate**

Run: `python -m py_compile src/zira_dashboard/odoo_sync.py`
Expected: no output, exit 0.

- [ ] **Step 8: Commit**

```bash
git add src/zira_dashboard/odoo_sync.py tests/test_odoo_sync_calendars.py
git commit -m "feat(timeclock): sync resource_calendar_id + refresh schedule hours"
```

---

## Task 5: Punch-time resolver in `timeclock.py`

**Files:**
- Modify: `src/zira_dashboard/routes/timeclock.py` (add `_shift_for_punch`; use it in `_open_log_row` ~lines 333, 345-353)
- Test: `tests/test_shift_for_punch.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_shift_for_punch.py`:

```python
"""Resolution of (shift_start, shift_end, rounding) per employee schedule.
Postgres-backed (needs people + work_schedules)."""

import os
from datetime import date, time

import pytest

from zira_dashboard import db, work_schedule_store, shift_config
from zira_dashboard.rounding import RoundingSettings
from zira_dashboard.routes.timeclock import _shift_for_punch

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)

CAL_ID = 990003
ODOO_ID = 990103  # test employee odoo_id
MONDAY = date(2026, 6, 1)   # 2026-06-01 is a Monday (weekday 0)


@pytest.fixture(autouse=True)
def _seed():
    db.execute("DELETE FROM work_schedules WHERE resource_calendar_id = %s", (CAL_ID,))
    db.execute(
        "INSERT INTO people (odoo_id, name, active, resource_calendar_id) "
        "VALUES (%s, %s, TRUE, %s) "
        "ON CONFLICT (odoo_id) DO UPDATE SET resource_calendar_id = EXCLUDED.resource_calendar_id, "
        "active = TRUE",
        (ODOO_ID, "Test Driver", CAL_ID),
    )
    work_schedule_store.reload()
    yield
    db.execute("DELETE FROM people WHERE odoo_id = %s", (ODOO_ID,))
    db.execute("DELETE FROM work_schedules WHERE resource_calendar_id = %s", (CAL_ID,))
    work_schedule_store.reload()


def test_driver_resolves_to_override_hours_and_windows():
    work_schedule_store.create(CAL_ID, "Drivers")
    work_schedule_store.refresh_synced(CAL_ID, "Drivers", {"0": ["05:45", "14:30"]})
    work_schedule_store.save_rounding(CAL_ID, RoundingSettings(20, 0, 0, 0))

    start, end, windows = _shift_for_punch(ODOO_ID, MONDAY)
    assert start == time(5, 45)
    assert end == time(14, 30)
    assert windows == RoundingSettings(20, 0, 0, 0)


def test_weekday_without_hours_falls_back_to_plant_default():
    work_schedule_store.create(CAL_ID, "Drivers")
    # Only Monday configured; ask for a Saturday punch (weekday 5).
    work_schedule_store.refresh_synced(CAL_ID, "Drivers", {"0": ["05:45", "14:30"]})
    work_schedule_store.save_rounding(CAL_ID, RoundingSettings(20, 0, 0, 0))

    saturday = date(2026, 6, 6)
    start, end, windows = _shift_for_punch(ODOO_ID, saturday)
    assert start == shift_config.shift_start_for(saturday)
    assert end == shift_config.shift_end_for(saturday)


def test_employee_without_override_uses_plant_default():
    # No work_schedules row for CAL_ID -> plant default.
    start, end, windows = _shift_for_punch(ODOO_ID, MONDAY)
    assert start == shift_config.shift_start_for(MONDAY)
    assert end == shift_config.shift_end_for(MONDAY)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_shift_for_punch.py -v`
Expected: FAIL — `ImportError: cannot import name '_shift_for_punch'`.

- [ ] **Step 3: Add the resolver**

In `src/zira_dashboard/routes/timeclock.py`, add this function just above `_open_log_row` (~line 319):

```python
def _shift_for_punch(person_odoo_id: int, local_date):
    """Resolve (shift_start, shift_end, RoundingSettings) for a punch.

    An employee on an Odoo work schedule that has a configured override
    (with hours for this weekday) gets that schedule's boundaries + windows;
    everyone else — and any misconfiguration (no calendar, no override, or
    an override missing this weekday's hours) — falls back to the plant
    default. We never guess a boundary."""
    from .. import rounding_store, work_schedule_store
    rows = db.query(
        "SELECT resource_calendar_id FROM people WHERE odoo_id = %s",
        (person_odoo_id,),
    )
    cal_id = rows[0]["resource_calendar_id"] if rows else None
    if cal_id is not None:
        ws = work_schedule_store.get(cal_id)
        if ws is not None:
            hours = ws.work_hours.get(local_date.weekday())
            if hours is not None:
                return hours[0], hours[1], ws.rounding
            _log.warning(
                "Work schedule override %s has no hours for weekday %s "
                "(person %s); using plant default rounding",
                cal_id, local_date.weekday(), person_odoo_id,
            )
    return (
        shift_config.shift_start_for(local_date),
        shift_config.shift_end_for(local_date),
        rounding_store.current(),
    )
```

- [ ] **Step 4: Use the resolver in `_open_log_row`**

In `src/zira_dashboard/routes/timeclock.py`, change the import line at the top of `_open_log_row` (~line 333) from:

```python
    from .. import rounding, rounding_store
```

to:

```python
    from .. import rounding
```

Then replace the rounding call (~lines 345-353):

```python
    try:
        local_date = occurred_at.astimezone(shift_config.SITE_TZ).date()
        rounded = rounding.apply_rounding(
            action,
            occurred_at,
            shift_config.shift_start_for(local_date),
            shift_config.shift_end_for(local_date),
            rounding_store.current(),
        )
```

with:

```python
    try:
        local_date = occurred_at.astimezone(shift_config.SITE_TZ).date()
        shift_start, shift_end, windows = _shift_for_punch(person_odoo_id, local_date)
        rounded = rounding.apply_rounding(
            action, occurred_at, shift_start, shift_end, windows,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_shift_for_punch.py tests/test_rounding.py -v`
Expected: PASS (the new resolver tests + the unchanged pure rounding tests, confirming `apply_rounding` behavior — e.g. `test_custom_shift_times_round_to_those` — still holds for the driver case: 5:30 → 5:45, 5:52 unchanged with `RoundingSettings(20,0,0,0)`).

- [ ] **Step 6: Local syntax gate**

Run: `python -m py_compile src/zira_dashboard/routes/timeclock.py`
Expected: no output, exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/routes/timeclock.py tests/test_shift_for_punch.py
git commit -m "feat(timeclock): resolve per-schedule shift + rounding at punch time"
```

---

## Task 6: Settings UI — per-schedule rounding

**Files:**
- Modify: `src/zira_dashboard/routes/settings.py` (GET context ~line 272-316; new POST routes after `settings_save_rounding` ~line 396; `_hours_display` helper)
- Modify: `src/zira_dashboard/templates/settings.html` (after the rounding form `</form>` ~line 393)
- Test: `tests/test_settings_work_schedule_rounding.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_settings_work_schedule_rounding.py`:

```python
"""Settings routes for per-schedule rounding. Postgres-backed; Odoo not
required (the add route's hours-refresh is best-effort)."""

import os

import pytest
from fastapi.testclient import TestClient

from zira_dashboard.app import app
from zira_dashboard import db, work_schedule_store, odoo_client
from zira_dashboard.rounding import RoundingSettings

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)

client = TestClient(app)
CAL_ID = 990004


@pytest.fixture(autouse=True)
def _clean():
    db.execute("DELETE FROM work_schedules WHERE resource_calendar_id = %s", (CAL_ID,))
    work_schedule_store.reload()
    yield
    db.execute("DELETE FROM work_schedules WHERE resource_calendar_id = %s", (CAL_ID,))
    work_schedule_store.reload()


def test_save_clamps_and_persists():
    work_schedule_store.create(CAL_ID, "Drivers")
    r = client.post(
        "/settings/work_schedule_rounding",
        data={
            "resource_calendar_id": str(CAL_ID),
            "in_before_min": "20", "in_after_min": "0",
            "out_before_min": "0", "out_after_min": "999",  # clamps to 60
        },
        headers={"accept": "application/json"},
    )
    assert r.status_code == 200
    work_schedule_store.reload()
    assert work_schedule_store.get(CAL_ID).rounding == RoundingSettings(20, 0, 0, 60)


def test_add_creates_override(monkeypatch):
    monkeypatch.setattr(odoo_client, "fetch_work_schedules",
                        lambda: [{"id": CAL_ID, "name": "Drivers"}])
    monkeypatch.setattr(odoo_client, "fetch_calendar_hours",
                        lambda ids: {CAL_ID: {"0": ["05:45", "14:30"]}})
    r = client.post(
        "/settings/work_schedule_rounding/add",
        data={"resource_calendar_id": str(CAL_ID)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    work_schedule_store.reload()
    assert work_schedule_store.get(CAL_ID) is not None


def test_remove_deletes_override():
    work_schedule_store.create(CAL_ID, "Drivers")
    r = client.post(
        "/settings/work_schedule_rounding/remove",
        data={"resource_calendar_id": str(CAL_ID)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    work_schedule_store.reload()
    assert work_schedule_store.get(CAL_ID) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_settings_work_schedule_rounding.py -v`
Expected: FAIL — `404`/`405` from the missing routes (and the save assertion fails).

- [ ] **Step 3: Add the `_hours_display` helper + GET context**

In `src/zira_dashboard/routes/settings.py`, add this helper near `_parse_hhmm` (~line 33):

```python
def _hours_display(work_hours: dict) -> str:
    """Short, human label for a schedule's synced hours, e.g. '5:45 AM –
    2:30 PM'. Collapses to a single range when every configured weekday
    shares it; 'varies by day' otherwise."""
    if not work_hours:
        return "— not synced from Odoo yet —"

    def fmt(t) -> str:
        h = t.hour % 12 or 12
        ap = "AM" if t.hour < 12 else "PM"
        return f"{h}:{t.minute:02d} {ap}"

    ranges = {(s, e) for (s, e) in work_hours.values()}
    if len(ranges) == 1:
        s, e = next(iter(ranges))
        return f"{fmt(s)} – {fmt(e)}"
    return "varies by day"
```

**Important ordering:** the `if section == "timeclock":` block runs *before* the `rounding_ctx` block, so `available_schedules` must be initialized near the top and populated inside that block, while `work_schedules_ctx` is built later (after `rounding_ctx`). Do the four inserts below in code order.

**(a)** Initialize `available_schedules` with the other top-of-handler list initializers — immediately before the `if section == "timeclock":` line (~line 93, just after `timeclock_sync_status: dict | None = None`):

```python
    available_schedules: list[dict] = []
```

**(b)** Inside the existing `if section == "timeclock":` block, just after the `timeclock_sync_status = status_rows[0] if status_rows else None` line (~line 121), add (note the local import — `work_schedule_store` isn't imported yet at this point in the function):

```python
        from .. import odoo_client as _oc, work_schedule_store
        try:
            _configured = {o.resource_calendar_id for o in work_schedule_store.all_overrides()}
            available_schedules = [
                {"id": c["id"], "name": c.get("name") or f"Schedule {c['id']}"}
                for c in _oc.fetch_work_schedules()
                if c["id"] not in _configured
            ]
        except Exception:
            available_schedules = []
```

**(c)** Just after the `rounding_ctx` block (~line 279), build `work_schedules_ctx`. Do NOT re-initialize `available_schedules` here — that would clobber what the timeclock block set:

```python
    from .. import work_schedule_store
    work_schedules_ctx = [
        {
            "resource_calendar_id": o.resource_calendar_id,
            "name": o.name or f"Schedule {o.resource_calendar_id}",
            "hours_display": _hours_display(o.work_hours),
            "in_before_min": o.rounding.in_before_min,
            "in_after_min": o.rounding.in_after_min,
            "out_before_min": o.rounding.out_before_min,
            "out_after_min": o.rounding.out_after_min,
        }
        for o in work_schedule_store.all_overrides()
    ]
```

**(d)** Add both to the `TemplateResponse` context dict (alongside `"rounding": rounding_ctx,` ~line 307):

```python
            "work_schedules": work_schedules_ctx,
            "available_schedules": available_schedules,
```

- [ ] **Step 4: Add the three POST routes**

In `src/zira_dashboard/routes/settings.py`, immediately after `settings_save_rounding` (~line 396), add:

```python
@router.post("/settings/work_schedule_rounding")
async def settings_save_work_schedule_rounding(request: Request):
    """Save the four rounding windows for ONE Odoo work schedule (by
    resource_calendar_id). Same 0..60 clamp as /settings/rounding; leaves the
    schedule's synced hours untouched."""
    from .. import work_schedule_store
    from ..rounding import RoundingSettings
    form = await request.form()

    def _clamp(raw) -> int:
        try:
            v = int(raw)
        except (TypeError, ValueError):
            return 0
        return max(0, min(60, v))

    try:
        cal_id = int(form.get("resource_calendar_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad id"}, status_code=400)
    work_schedule_store.save_rounding(cal_id, RoundingSettings(
        in_before_min=_clamp(form.get("in_before_min")),
        in_after_min=_clamp(form.get("in_after_min")),
        out_before_min=_clamp(form.get("out_before_min")),
        out_after_min=_clamp(form.get("out_after_min")),
    ))
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/settings?saved=1&section=timeclock", status_code=303)


@router.post("/settings/work_schedule_rounding/add")
async def settings_add_work_schedule(request: Request):
    """Configure a new per-schedule override for an Odoo work schedule and
    immediately sync its hours (best-effort)."""
    from .. import work_schedule_store, odoo_sync
    form = await request.form()
    try:
        cal_id = int(form.get("resource_calendar_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad id"}, status_code=400)
    work_schedule_store.create(cal_id)
    try:
        odoo_sync.refresh_work_schedule_hours(only_ids=[cal_id])
    except Exception:
        pass  # row exists; hours fill in on the next periodic sync
    return RedirectResponse(url="/settings?saved=1&section=timeclock", status_code=303)


@router.post("/settings/work_schedule_rounding/remove")
async def settings_remove_work_schedule(request: Request):
    """Drop a per-schedule override. Its employees revert to plant default."""
    from .. import work_schedule_store
    form = await request.form()
    try:
        cal_id = int(form.get("resource_calendar_id"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad id"}, status_code=400)
    work_schedule_store.delete(cal_id)
    return RedirectResponse(url="/settings?saved=1&section=timeclock", status_code=303)
```

- [ ] **Step 5: Add the template block**

In `src/zira_dashboard/templates/settings.html`, change the global rounding subhead (~line 344) from:

```html
      <h4 class="rounding-subhead">Round To Schedule</h4>
```

to:

```html
      <h4 class="rounding-subhead">Round To Schedule (default)</h4>
```

Then, immediately after the rounding form's closing `</form>` (~line 393) and before `<h3 style="margin-top:1.6rem">Sync status (last 7 days)</h3>`, insert:

```html
    <div class="per-schedule-rounding" style="margin-top:1.6rem">
      <h4 class="rounding-subhead">Per-schedule rounding</h4>
      <p class="rounding-blurb">
        Give a specific Odoo work schedule its own rounding windows. The
        hours come from Odoo; the windows behave exactly like the default
        above. Anyone not on a listed schedule uses the default.
      </p>

      {% for ws in work_schedules %}
      <div class="ws-rounding-card"
           style="border:1px solid var(--border);border-radius:8px;padding:0.8rem;margin:0.6rem 0">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:0.5rem">
          <strong>{{ ws.name }}</strong>
          <span class="note">{{ ws.hours_display }} &middot; from Odoo</span>
        </div>
        <form method="post" action="/settings/work_schedule_rounding">
          <input type="hidden" name="resource_calendar_id" value="{{ ws.resource_calendar_id }}">
          <div class="rounding-grid">
            <div class="rounding-col">
              <h4>IN</h4>
              <label>Up to
                <input type="number" name="in_before_min" min="0" max="60" value="{{ ws.in_before_min }}">
                minute(s) before the schedule clock-in time.</label>
              <label>Up to
                <input type="number" name="in_after_min" min="0" max="60" value="{{ ws.in_after_min }}">
                minute(s) after the schedule clock-in time.</label>
            </div>
            <div class="rounding-col">
              <h4>OUT</h4>
              <label>Up to
                <input type="number" name="out_before_min" min="0" max="60" value="{{ ws.out_before_min }}">
                minute(s) before the schedule clock-out time.</label>
              <label>Up to
                <input type="number" name="out_after_min" min="0" max="60" value="{{ ws.out_after_min }}">
                minute(s) after the schedule clock-out time.</label>
            </div>
          </div>
          <button type="submit">Save</button>
        </form>
        <form method="post" action="/settings/work_schedule_rounding/remove" style="margin-top:0.4rem">
          <input type="hidden" name="resource_calendar_id" value="{{ ws.resource_calendar_id }}">
          <button type="submit"
                  onclick="return confirm('Remove this schedule override? Its employees revert to the default rounding.')">
            Remove
          </button>
        </form>
      </div>
      {% else %}
      <p class="note">No per-schedule overrides yet.</p>
      {% endfor %}

      {% if available_schedules %}
      <form method="post" action="/settings/work_schedule_rounding/add" style="margin-top:0.8rem">
        <label>Add a schedule:
          <select name="resource_calendar_id">
            {% for s in available_schedules %}
            <option value="{{ s.id }}">{{ s.name }}</option>
            {% endfor %}
          </select>
        </label>
        <button type="submit">Add</button>
      </form>
      {% endif %}
    </div>
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_settings_work_schedule_rounding.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Local syntax gate**

Run: `python -m py_compile src/zira_dashboard/routes/settings.py`
Expected: no output, exit 0. (Templates aren't byte-compiled; the route tests render `settings.html`, which exercises the Jinja.)

- [ ] **Step 8: Commit**

```bash
git add src/zira_dashboard/routes/settings.py src/zira_dashboard/templates/settings.html tests/test_settings_work_schedule_rounding.py
git commit -m "feat(settings): per-schedule rounding UI"
```

---

## Final verification

- [ ] **Run the full timeclock-relevant test set**

Run: `pytest tests/test_rounding.py tests/test_rounding_store.py tests/test_work_schedules_schema.py tests/test_work_schedule_store.py tests/test_odoo_calendar_hours.py tests/test_odoo_sync_calendars.py tests/test_shift_for_punch.py tests/test_settings_work_schedule_rounding.py -v`
Expected: all PASS (DB-backed ones skip only if `DATABASE_URL` is unset).

- [ ] **Compile-gate every touched module**

Run: `python -m py_compile src/zira_dashboard/db.py src/zira_dashboard/work_schedule_store.py src/zira_dashboard/odoo_client.py src/zira_dashboard/odoo_sync.py src/zira_dashboard/routes/timeclock.py src/zira_dashboard/routes/settings.py`
Expected: no output, exit 0.

- [ ] **Manual smoke (after deploy / in an env with Odoo):** In Odoo, confirm the Drivers working schedule (5:45–2:30) exists and is each driver's Working Hours. In the app: Settings → Timeclock → Per-schedule rounding → Add → pick Drivers → set `20 / 0 / 0 / 0` → Save. Confirm a driver clock-in at 5:30 records 5:45, 5:52 records 5:52, and a clock-out stands as-punched; confirm a plant employee is unaffected.
