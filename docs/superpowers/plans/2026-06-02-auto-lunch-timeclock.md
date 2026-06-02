# Auto-Lunch for the Timeclock — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically sign employees out for lunch and back in (creating the unpaid lunch gap in Odoo `hr.attendance`), driven by the day's Lunch break for fixed-schedule staff and by elapsed-time-since-clock-in for Odoo-flexible staff, handling manual punches without breaking.

**Architecture:** A new ~60s asyncio worker (`auto_lunch.run_tick`) drives a per-person/per-day state machine persisted in `auto_lunch_runs`. It writes ordinary punch rows (tagged `source='auto_lunch'`) into `timeclock_punches_log` and lets the existing sync/retry path carry them to Odoo. Reads use the existing reconciliation (extracted into a shared `attendance_state` module). The kiosk treats an active lunch gap as "on shift" so a sign-out during the gap ends the day and cancels the auto sign-in.

**Tech Stack:** Python 3.11 (prod) / 3.9 (local), FastAPI, PostgreSQL (psycopg2), Odoo XML-RPC, asyncio background loops, pytest. Deploy: Railway (auto-deploys on push to `main`).

**Spec:** `docs/superpowers/specs/2026-06-02-auto-lunch-timeclock-design.md`

**Testing note (local constraint):** The full suite can't run locally (no FastAPI, Python 3.9). **Pure-logic test files that only import `zira_dashboard` service modules DO run locally** (no `DATABASE_URL`). DB/Odoo-backed tests guard with `pytest.mark.skipif(not os.environ.get("DATABASE_URL"))` and run in CI/Railway. Each task notes which kind it is.

---

## File Structure

**New files:**
- `src/zira_dashboard/attendance_state.py` — reconciled clocked-in/out state, shared by the kiosk route and the worker (extracted from `routes/timeclock.py`).
- `src/zira_dashboard/auto_lunch_settings.py` — singleton settings store (enable toggle, observe-only, flex rule), modeled on `schedule_store`.
- `src/zira_dashboard/auto_lunch.py` — the worker: pure decision core (`decide`, `lunch_window_for_day`, `flex_window`) + thin I/O (`run_tick`, run store, `active_lunch_run`, `note_employee_clock_out`).
- `tests/test_attendance_state.py`, `tests/test_auto_lunch_decide.py`, `tests/test_auto_lunch_flex_sync.py` — pure-logic (local).
- `tests/test_auto_lunch_settings.py`, `tests/test_auto_lunch_worker.py` — DB-backed (`skipif`).

**Modified files:**
- `src/zira_dashboard/db.py` — migrations (4 additive DDL statements + singleton seed) appended to `_SCHEMA_DDL`.
- `src/zira_dashboard/routes/timeclock.py` — delegate state to `attendance_state`; overlay active lunch on the dashboard; cancel auto-in on clock-out.
- `src/zira_dashboard/odoo_client.py` — `fetch_work_schedules()` also returns `is_flexible`.
- `src/zira_dashboard/odoo_sync.py` — populate `people.is_flexible` from the flex calendar set.
- `src/zira_dashboard/app.py` — register `_warm_auto_lunch_loop()`.

---

## Task 1: Schema migrations

**Files:**
- Modify: `src/zira_dashboard/db.py` (append to the `_SCHEMA_DDL` string, before its closing `"""`)
- Test: `tests/test_auto_lunch_schema.py` (DB-backed, `skipif`)

- [ ] **Step 1: Write the failing test**

`tests/test_auto_lunch_schema.py`:
```python
"""Auto-lunch schema migrations are present after bootstrap. Postgres-backed."""
import os
import pytest
from zira_dashboard import db

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres")


def _columns(table):
    return {r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s", (table,))}


def test_punch_log_has_source_column():
    db.bootstrap_schema()
    assert "source" in _columns("timeclock_punches_log")


def test_people_has_is_flexible_column():
    db.bootstrap_schema()
    assert "is_flexible" in _columns("people")


def test_auto_lunch_runs_and_settings_exist():
    db.bootstrap_schema()
    assert _columns("auto_lunch_runs") >= {
        "person_odoo_id", "day", "kind", "state", "target_out_at",
        "target_in_at", "wc_name", "out_punch_id", "in_punch_id"}
    assert _columns("auto_lunch_settings") >= {
        "enabled", "observe_only", "flex_after_hours", "flex_minutes"}


def test_settings_singleton_seeded():
    db.bootstrap_schema()
    rows = db.query("SELECT id FROM auto_lunch_settings WHERE id = 1")
    assert len(rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auto_lunch_schema.py -v`
Expected (with `DATABASE_URL` set): FAIL — `source`/`is_flexible`/tables missing. (Locally without `DATABASE_URL`: SKIPPED.)

- [ ] **Step 3: Add the migrations**

In `src/zira_dashboard/db.py`, append to the end of the `_SCHEMA_DDL` string (just before the closing `"""`):
```sql

-- 2026-06-02 auto-lunch: tag system-generated punches so the worker can
-- recognize its own actions and reports can filter them out.
ALTER TABLE timeclock_punches_log
  ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'employee';

-- Flex flag mirrored from each person's Odoo work schedule (Schedule Type =
-- flexible). Stored on people (always present) rather than work_schedules
-- (rows exist only for rounding overrides). Drives the elapsed-time lunch trigger.
ALTER TABLE people ADD COLUMN IF NOT EXISTS is_flexible BOOLEAN NOT NULL DEFAULT FALSE;

-- Per-person/per-day lunch state machine. UNIQUE(person, day) enforces one
-- lunch per day and survives restarts (no double-deduct after a redeploy).
CREATE TABLE IF NOT EXISTS auto_lunch_runs (
  id              BIGSERIAL PRIMARY KEY,
  person_odoo_id  INTEGER NOT NULL,
  day             DATE    NOT NULL,
  kind            TEXT    NOT NULL CHECK (kind IN ('scheduled','flex')),
  state           TEXT    NOT NULL CHECK (state IN
                    ('pending','auto_out','done','skipped','ended_by_employee')),
  target_out_at   TIMESTAMPTZ,
  target_in_at    TIMESTAMPTZ,
  wc_name         TEXT,
  out_punch_id    BIGINT,
  in_punch_id     BIGINT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (person_odoo_id, day)
);

-- Singleton settings row (id=1). Defaults: OFF, and the first enable runs
-- observe-only. flex rule defaults to 5h -> 30min.
CREATE TABLE IF NOT EXISTS auto_lunch_settings (
  id                INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  enabled           BOOLEAN NOT NULL DEFAULT FALSE,
  observe_only      BOOLEAN NOT NULL DEFAULT TRUE,
  flex_after_hours  NUMERIC NOT NULL DEFAULT 5.0,
  flex_minutes      INTEGER NOT NULL DEFAULT 30
);
INSERT INTO auto_lunch_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auto_lunch_schema.py -v` (with `DATABASE_URL`)
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/db.py tests/test_auto_lunch_schema.py
git commit -m "feat(auto-lunch): schema — source tag, is_flexible, runs + settings tables"
```

---

## Task 2: `auto_lunch_settings` store

**Files:**
- Create: `src/zira_dashboard/auto_lunch_settings.py`
- Test: `tests/test_auto_lunch_settings.py` (DB-backed, `skipif`)

- [ ] **Step 1: Write the failing test**

`tests/test_auto_lunch_settings.py`:
```python
"""auto_lunch_settings load/save/cache. Postgres-backed."""
import os
import pytest
from zira_dashboard import db, auto_lunch_settings as als

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres")


@pytest.fixture(autouse=True)
def _reset():
    db.bootstrap_schema()
    db.execute("UPDATE auto_lunch_settings SET enabled=FALSE, observe_only=TRUE, "
               "flex_after_hours=5.0, flex_minutes=30 WHERE id=1")
    als.reload()
    yield
    db.execute("UPDATE auto_lunch_settings SET enabled=FALSE, observe_only=TRUE, "
               "flex_after_hours=5.0, flex_minutes=30 WHERE id=1")
    als.reload()


def test_defaults_when_seeded():
    s = als.current()
    assert s.enabled is False and s.observe_only is True
    assert s.flex_after_hours == 5.0 and s.flex_minutes == 30


def test_save_round_trip_and_cache_invalidation():
    als.save(als.Settings(enabled=True, observe_only=False,
                          flex_after_hours=6.0, flex_minutes=45))
    s = als.current()
    assert s.enabled is True and s.observe_only is False
    assert s.flex_after_hours == 6.0 and s.flex_minutes == 45
    # A direct DB change is not seen until reload (proves caching).
    db.execute("UPDATE auto_lunch_settings SET flex_minutes=15 WHERE id=1")
    assert als.current().flex_minutes == 45
    assert als.reload().flex_minutes == 15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auto_lunch_settings.py -v` (with `DATABASE_URL`)
Expected: FAIL — `No module named 'zira_dashboard.auto_lunch_settings'`.

- [ ] **Step 3: Create the store**

`src/zira_dashboard/auto_lunch_settings.py`:
```python
"""Auto-lunch settings: master toggle, observe-only mode, and the global flex
rule. Singleton row (id=1), cached in process and invalidated on save() —
same pattern as schedule_store.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    observe_only: bool = True
    flex_after_hours: float = 5.0
    flex_minutes: int = 30


DEFAULT = Settings()

_lock = RLock()
_cache: Settings | None = None


def _row_to_settings(row: dict) -> Settings:
    return Settings(
        enabled=bool(row.get("enabled", False)),
        observe_only=bool(row.get("observe_only", True)),
        flex_after_hours=float(row.get("flex_after_hours") or 5.0),
        flex_minutes=int(row.get("flex_minutes") or 30),
    )


def _load_from_db() -> Settings:
    from . import db
    rows = db.query(
        "SELECT enabled, observe_only, flex_after_hours, flex_minutes "
        "FROM auto_lunch_settings WHERE id = 1"
    )
    return _row_to_settings(rows[0]) if rows else DEFAULT


def current() -> Settings:
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load_from_db()
        return _cache


def save(s: Settings) -> None:
    global _cache
    from . import db
    db.execute(
        "INSERT INTO auto_lunch_settings "
        "(id, enabled, observe_only, flex_after_hours, flex_minutes) "
        "VALUES (1, %s, %s, %s, %s) "
        "ON CONFLICT (id) DO UPDATE SET enabled = EXCLUDED.enabled, "
        "observe_only = EXCLUDED.observe_only, "
        "flex_after_hours = EXCLUDED.flex_after_hours, "
        "flex_minutes = EXCLUDED.flex_minutes",
        (s.enabled, s.observe_only, s.flex_after_hours, s.flex_minutes),
    )
    with _lock:
        _cache = s


def reload() -> Settings:
    global _cache
    with _lock:
        _cache = _load_from_db()
        return _cache
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auto_lunch_settings.py -v` (with `DATABASE_URL`)
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/auto_lunch_settings.py tests/test_auto_lunch_settings.py
git commit -m "feat(auto-lunch): settings store (toggle, observe-only, flex rule)"
```

---

## Task 3: Extract `attendance_state` (shared reconciliation)

Move the reconciliation out of `routes/timeclock.py` so the worker can reuse it without a routes↔service import cycle. Behavior is unchanged — same functions, new home.

**Files:**
- Create: `src/zira_dashboard/attendance_state.py`
- Modify: `src/zira_dashboard/routes/timeclock.py` (delete the 4 moved funcs; import + alias)
- Test: `tests/test_attendance_state.py` (pure-logic, local)

- [ ] **Step 1: Write the failing test**

`tests/test_attendance_state.py`:
```python
"""Pure-logic tests for the reconciliation helpers. No DB/Odoo — the two
sources (snapshot + latest punch) are passed in / monkeypatched."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from zira_dashboard import attendance_state as ast


def test_state_from_log_clocked_out_when_none_or_clockout():
    assert ast.state_from_log(None)["is_clocked_in"] is False
    out = {"action": "clock_out", "wc_name": None, "occurred_at": None,
           "odoo_attendance_id": None}
    assert ast.state_from_log(out)["is_clocked_in"] is False


def test_state_from_log_clocked_in_carries_wc():
    row = {"action": "clock_in", "wc_name": "Bay 3",
           "occurred_at": datetime(2026, 6, 2, 7, tzinfo=timezone.utc),
           "odoo_attendance_id": 55}
    s = ast.state_from_log(row)
    assert s["is_clocked_in"] is True and s["current_wc"] == "Bay 3"
    assert s["open_odoo_attendance_id"] == 55


def test_trust_local_unsynced_punch_wins():
    assert ast.trust_local({"synced_to_odoo": False}, datetime.now(timezone.utc)) is True


def test_trust_local_synced_before_refresh_yields_to_cache():
    synced = datetime(2026, 6, 2, 11, 0, tzinfo=timezone.utc)
    refreshed = synced + timedelta(seconds=30)
    latest = {"synced_to_odoo": True, "synced_at": synced}
    assert ast.trust_local(latest, refreshed) is False


def test_current_state_unsynced_autoout_reads_clocked_out(monkeypatch):
    # The race-guard the worker depends on: a just-written, still-unsynced
    # auto clock_out makes current_state report clocked-out even though the
    # cache still shows the morning attendance open.
    monkeypatch.setattr(ast.live_cache, "read_open_attendance",
                        lambda: ({"5": {"att_id": 1, "check_in": None, "wc_name": "Bay 3"}},
                                 datetime.now(timezone.utc)))
    monkeypatch.setattr(ast.live_cache, "is_stale", lambda _r: False)
    monkeypatch.setattr(ast, "latest_punch",
                        lambda pid: {"action": "clock_out", "wc_name": None,
                                     "occurred_at": None, "odoo_attendance_id": None,
                                     "synced_to_odoo": False, "synced_at": None})
    assert ast.current_state(5)["is_clocked_in"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_attendance_state.py -v`
Expected: FAIL — `No module named 'zira_dashboard.attendance_state'`.

- [ ] **Step 3: Create `attendance_state.py`** (copy the four functions verbatim from `routes/timeclock.py`)

`src/zira_dashboard/attendance_state.py`:
```python
"""Reconciled timeclock attendance state, shared by the kiosk route and the
auto-lunch worker.

Extracted from routes/timeclock.py so the background worker reasons about the
same Odoo-reconciled clocked-in/out state the kiosk uses, without a
routes<-service import cycle. state_from_log/trust_local are pure (unit-testable
with no DB/Odoo); current_state wires the two local reads around them.

See docs/superpowers/specs/2026-06-01-timeclock-odoo-state-reconciliation-design.md.
"""
from __future__ import annotations

from datetime import datetime

from . import db, live_cache


def latest_punch(person_odoo_id: int) -> dict | None:
    rows = db.query(
        "SELECT action, wc_name, "
        "COALESCE(rounded_at, occurred_at) AS occurred_at, "
        "odoo_attendance_id, synced_to_odoo, synced_at "
        "FROM timeclock_punches_log WHERE person_odoo_id = %s "
        "ORDER BY occurred_at DESC, id DESC LIMIT 1",
        (person_odoo_id,),
    )
    return rows[0] if rows else None


def state_from_log(latest: dict | None) -> dict:
    if latest is None or latest["action"] in ("clock_out", "transfer_out"):
        return {"is_clocked_in": False, "current_wc": None,
                "check_in_ts": None, "open_odoo_attendance_id": None}
    return {"is_clocked_in": True, "current_wc": latest["wc_name"],
            "check_in_ts": latest["occurred_at"],
            "open_odoo_attendance_id": latest["odoo_attendance_id"]}


def trust_local(latest: dict | None, refreshed_at) -> bool:
    if latest is None:
        return False
    if not latest.get("synced_to_odoo"):
        return True
    synced_at = latest.get("synced_at")
    if synced_at is None:
        return True
    return refreshed_at <= synced_at


def current_state(person_odoo_id: int) -> dict:
    snapshot, refreshed_at = live_cache.read_open_attendance()
    latest = latest_punch(person_odoo_id)
    if snapshot is None or live_cache.is_stale(refreshed_at):
        return state_from_log(latest)
    if trust_local(latest, refreshed_at):
        return state_from_log(latest)
    entry = snapshot.get(str(person_odoo_id))
    if not entry:
        return {"is_clocked_in": False, "current_wc": None,
                "check_in_ts": None, "open_odoo_attendance_id": None}
    check_in = entry.get("check_in")
    return {"is_clocked_in": True, "current_wc": entry.get("wc_name"),
            "check_in_ts": datetime.fromisoformat(check_in) if check_in else None,
            "open_odoo_attendance_id": entry.get("att_id")}
```

- [ ] **Step 4: Update `routes/timeclock.py` to delegate**

Delete the four function definitions `_trust_local`, `_state_from_log`, `_current_state`, and `_latest_punch` (lines ~166–246). Add `attendance_state` to the existing import on line 56:
```python
from .. import db, timeclock_sync, shift_config, staffing, live_cache, attendance_state
```
Then add module-level aliases where those functions used to be (so every existing call site keeps working):
```python
# Reconciliation moved to attendance_state.py (shared with the auto-lunch
# worker). Aliased here so existing call sites are unchanged.
_latest_punch = attendance_state.latest_punch
_current_state = attendance_state.current_state
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_attendance_state.py -v`
Expected: PASS (5 tests).
Run (CI, if a route test exists): `pytest tests/ -k timeclock -v` — Expected: still PASS (behavior unchanged).

- [ ] **Step 6: Verify import wiring locally**

Run: `python -c "import ast,sys; ast.parse(open('src/zira_dashboard/attendance_state.py').read()); print('ok')"`
Expected: `ok`. (Local Python can't import FastAPI route module, but `py_compile` of `routes/timeclock.py` is checked in CI.)

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/attendance_state.py src/zira_dashboard/routes/timeclock.py tests/test_attendance_state.py
git commit -m "refactor(timeclock): extract reconciliation into attendance_state"
```

---

## Task 4: Auto-lunch pure decision core

The heart of the feature: window computation + the state machine, with zero I/O.

**Files:**
- Create: `src/zira_dashboard/auto_lunch.py` (pure parts only this task)
- Test: `tests/test_auto_lunch_decide.py` (pure-logic, local)

- [ ] **Step 1: Write the failing test**

`tests/test_auto_lunch_decide.py`:
```python
"""Pure state-machine + window tests for auto_lunch. No DB/Odoo."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from zira_dashboard import auto_lunch as al
from zira_dashboard.schedule_store import Break
from zira_dashboard.shift_config import SITE_TZ
from datetime import time


def _dt(h, m=0):
    return datetime(2026, 6, 2, h, m, tzinfo=SITE_TZ)


def test_lunch_window_picks_the_lunch_break():
    breaks = (Break(time(9, 0), time(9, 15), "Morning break"),
              Break(time(11, 0), time(11, 30), "Lunch"))
    w = al.lunch_window_for_day(breaks, date(2026, 6, 2))
    assert w.out_at == _dt(11, 0) and w.in_at == _dt(11, 30)


def test_lunch_window_none_when_no_lunch():
    breaks = (Break(time(9, 0), time(9, 15), "Morning break"),)
    assert al.lunch_window_for_day(breaks, date(2026, 6, 2)) is None


def test_flex_window_from_first_clock_in():
    w = al.flex_window(_dt(6, 0), 5.0, 30)
    assert w.out_at == _dt(11, 0) and w.in_at == _dt(11, 30)


def test_pending_clocked_in_at_lunch_triggers_auto_out():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    t = al.decide("pending", True, w, _dt(11, 0))
    assert t.new_state == "auto_out" and t.action == "clock_out" and t.at == _dt(11, 0)


def test_pending_clocked_out_at_lunch_is_skipped():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    t = al.decide("pending", False, w, _dt(11, 0))
    assert t.new_state == "skipped" and t.action is None


def test_pending_before_lunch_does_nothing():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    t = al.decide("pending", True, w, _dt(10, 30))
    assert t.new_state == "pending" and t.action is None


def test_auto_out_returns_clock_in_at_lunch_end_when_out():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    t = al.decide("auto_out", False, w, _dt(11, 30))
    assert t.new_state == "done" and t.action == "clock_in" and t.at == _dt(11, 30)


def test_auto_out_already_in_at_end_no_double_punch():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    t = al.decide("auto_out", True, w, _dt(11, 30))
    assert t.new_state == "done" and t.action is None


def test_auto_out_mid_gap_waits():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    t = al.decide("auto_out", False, w, _dt(11, 15))
    assert t.new_state == "auto_out" and t.action is None


def test_terminal_states_are_inert():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    for st in ("done", "skipped", "ended_by_employee"):
        t = al.decide(st, True, w, _dt(12, 0))
        assert t.new_state == st and t.action is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auto_lunch_decide.py -v`
Expected: FAIL — `No module named 'zira_dashboard.auto_lunch'`.

- [ ] **Step 3: Create the pure core**

`src/zira_dashboard/auto_lunch.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auto_lunch_decide.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/auto_lunch.py tests/test_auto_lunch_decide.py
git commit -m "feat(auto-lunch): pure decision core (windows + state machine)"
```

---

## Task 5: Worker I/O — run store, punch writer, `run_tick`, overlay & cancel helpers

**Files:**
- Modify: `src/zira_dashboard/auto_lunch.py` (add I/O below the pure core)
- Test: `tests/test_auto_lunch_worker.py` (DB-backed, `skipif`)

- [ ] **Step 1: Write the failing test**

`tests/test_auto_lunch_worker.py`:
```python
"""Worker integration: run_tick drives the state machine end-to-end against
Postgres, with the open-attendance cache and Odoo sync stubbed. skipif Postgres."""
import os
from datetime import datetime, time, timedelta, timezone

import pytest

from zira_dashboard import (db, auto_lunch as al, auto_lunch_settings as als,
                            live_cache, shift_config)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres")

PID = 990777  # test person odoo_id unlikely to collide


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    db.bootstrap_schema()
    db.execute("DELETE FROM auto_lunch_runs WHERE person_odoo_id = %s", (PID,))
    db.execute("DELETE FROM timeclock_punches_log WHERE person_odoo_id = %s", (PID,))
    als.save(als.Settings(enabled=True, observe_only=False,
                          flex_after_hours=5.0, flex_minutes=30))
    # Stub the Odoo sync so no XML-RPC happens; the punch row is enough.
    monkeypatch.setattr("zira_dashboard.timeclock_sync.sync_one_by_id", lambda _id: None)
    yield
    db.execute("DELETE FROM auto_lunch_runs WHERE person_odoo_id = %s", (PID,))
    db.execute("DELETE FROM timeclock_punches_log WHERE person_odoo_id = %s", (PID,))
    als.save(als.Settings())  # back to defaults (off)


def test_scheduled_auto_out_then_auto_in(monkeypatch):
    day = datetime.now(shift_config.SITE_TZ).date()
    lunch_out = datetime.combine(day, time(11, 0), tzinfo=shift_config.SITE_TZ)
    lunch_in = lunch_out + timedelta(minutes=30)
    now_ref = datetime.now(timezone.utc)
    # Force a fixed lunch window for `day`.
    from zira_dashboard.schedule_store import Break
    monkeypatch.setattr(shift_config, "is_workday", lambda d: True)
    monkeypatch.setattr(shift_config, "breaks_for",
                        lambda d: (Break(time(11, 0), time(11, 30), "Lunch"),))
    # Cache says clocked in, fresh.
    monkeypatch.setattr(live_cache, "read_open_attendance",
                        lambda: ({str(PID): {"att_id": 1, "check_in": None,
                                             "wc_name": "Bay 3"}}, now_ref))
    monkeypatch.setattr(live_cache, "is_stale", lambda _r: False)

    # Tick at lunch start -> auto clock_out written, run = auto_out.
    al.run_tick(now=lunch_out)
    outs = db.query("SELECT action, wc_name, source, "
                    "COALESCE(rounded_at, occurred_at) AS at "
                    "FROM timeclock_punches_log WHERE person_odoo_id=%s", (PID,))
    assert [(r["action"], r["source"]) for r in outs] == [("clock_out", "auto_lunch")]
    run = db.query("SELECT state, wc_name FROM auto_lunch_runs WHERE person_odoo_id=%s",
                   (PID,))[0]
    assert run["state"] == "auto_out" and run["wc_name"] == "Bay 3"

    # Now they're clocked OUT (cache empty) and it's lunch end -> auto clock_in.
    monkeypatch.setattr(live_cache, "read_open_attendance", lambda: ({}, now_ref))
    al.run_tick(now=lunch_in)
    acts = [r["action"] for r in db.query(
        "SELECT action, COALESCE(rounded_at,occurred_at) AS at FROM "
        "timeclock_punches_log WHERE person_odoo_id=%s ORDER BY at", (PID,))]
    assert acts == ["clock_out", "clock_in"]
    assert db.query("SELECT state FROM auto_lunch_runs WHERE person_odoo_id=%s",
                    (PID,))[0]["state"] == "done"


def test_clocked_out_at_lunch_is_skipped(monkeypatch):
    day = datetime.now(shift_config.SITE_TZ).date()
    lunch_out = datetime.combine(day, time(11, 0), tzinfo=shift_config.SITE_TZ)
    from zira_dashboard.schedule_store import Break
    monkeypatch.setattr(shift_config, "is_workday", lambda d: True)
    monkeypatch.setattr(shift_config, "breaks_for",
                        lambda d: (Break(time(11, 0), time(11, 30), "Lunch"),))
    monkeypatch.setattr(live_cache, "read_open_attendance",
                        lambda: ({}, datetime.now(timezone.utc)))  # nobody in
    monkeypatch.setattr(live_cache, "is_stale", lambda _r: False)
    # Seed a run row so PID is a candidate even though the cache is empty.
    db.execute("INSERT INTO auto_lunch_runs (person_odoo_id, day, kind, state) "
               "VALUES (%s,%s,'scheduled','pending')", (PID, day))
    al.run_tick(now=lunch_out)
    assert db.query("SELECT state FROM auto_lunch_runs WHERE person_odoo_id=%s",
                    (PID,))[0]["state"] == "skipped"
    assert db.query("SELECT COUNT(*) n FROM timeclock_punches_log "
                    "WHERE person_odoo_id=%s", (PID,))[0]["n"] == 0


def test_disabled_does_nothing(monkeypatch):
    als.save(als.Settings(enabled=False))
    al.run_tick(now=datetime.now(shift_config.SITE_TZ))
    assert db.query("SELECT COUNT(*) n FROM auto_lunch_runs "
                    "WHERE person_odoo_id=%s", (PID,))[0]["n"] == 0


def test_note_employee_clock_out_cancels_auto_in(monkeypatch):
    day = datetime.now(shift_config.SITE_TZ).date()
    out_at = datetime.now(shift_config.SITE_TZ).replace(microsecond=0)
    in_at = out_at + timedelta(minutes=30)
    db.execute("INSERT INTO auto_lunch_runs (person_odoo_id, day, kind, state, "
               "target_out_at, target_in_at) VALUES (%s,%s,'scheduled','auto_out',%s,%s)",
               (PID, day, out_at, in_at))
    ended = al.note_employee_clock_out(PID, now=out_at + timedelta(minutes=5))
    assert ended is True
    assert db.query("SELECT state FROM auto_lunch_runs WHERE person_odoo_id=%s",
                    (PID,))[0]["state"] == "ended_by_employee"


def test_active_lunch_run_only_inside_window():
    day = datetime.now(shift_config.SITE_TZ).date()
    out_at = datetime.now(shift_config.SITE_TZ).replace(microsecond=0)
    in_at = out_at + timedelta(minutes=30)
    db.execute("INSERT INTO auto_lunch_runs (person_odoo_id, day, kind, state, "
               "target_out_at, target_in_at) VALUES (%s,%s,'scheduled','auto_out',%s,%s)",
               (PID, day, out_at, in_at))
    assert al.active_lunch_run(PID, out_at + timedelta(minutes=5)) is not None
    assert al.active_lunch_run(PID, in_at + timedelta(minutes=1)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auto_lunch_worker.py -v` (with `DATABASE_URL`)
Expected: FAIL — `module 'zira_dashboard.auto_lunch' has no attribute 'run_tick'`.

- [ ] **Step 3: Append the I/O to `auto_lunch.py`**

Add to the top imports of `src/zira_dashboard/auto_lunch.py`:
```python
from datetime import date, datetime, time, timedelta
from . import shift_config, db, live_cache, attendance_state, auto_lunch_settings, timeclock_sync
```
(Replace the existing `from . import shift_config` and the `date, datetime, timedelta` import line with the above.)

Append below the pure core:
```python
# ---------- I/O ----------

def _flex_person_ids() -> set[int]:
    rows = db.query(
        "SELECT odoo_id FROM people "
        "WHERE is_flexible = TRUE AND active = TRUE AND odoo_id IS NOT NULL"
    )
    return {int(r["odoo_id"]) for r in rows}


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=shift_config.SITE_TZ)
    return start, start + timedelta(days=1)


def _first_clock_in(person_odoo_id: int, day: date) -> datetime | None:
    """The person's earliest clock_in on `day` (their morning punch). Used as
    the flex elapsed-time anchor."""
    start, end = _day_bounds(day)
    rows = db.query(
        "SELECT MIN(COALESCE(rounded_at, occurred_at)) AS first_in "
        "FROM timeclock_punches_log WHERE person_odoo_id = %s "
        "AND action = 'clock_in' "
        "AND COALESCE(rounded_at, occurred_at) >= %s "
        "AND COALESCE(rounded_at, occurred_at) < %s",
        (person_odoo_id, start, end),
    )
    return rows[0]["first_in"] if rows and rows[0]["first_in"] else None


def _get_run(person_odoo_id: int, day: date) -> dict | None:
    rows = db.query(
        "SELECT person_odoo_id, day, kind, state, target_out_at, target_in_at, "
        "wc_name, out_punch_id, in_punch_id FROM auto_lunch_runs "
        "WHERE person_odoo_id = %s AND day = %s",
        (person_odoo_id, day),
    )
    return rows[0] if rows else None


def _upsert_run(person_odoo_id, day, kind, state, *, target_out_at=None,
                target_in_at=None, wc_name=None, out_punch_id=None,
                in_punch_id=None) -> None:
    db.execute(
        "INSERT INTO auto_lunch_runs (person_odoo_id, day, kind, state, "
        "target_out_at, target_in_at, wc_name, out_punch_id, in_punch_id, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
        "ON CONFLICT (person_odoo_id, day) DO UPDATE SET "
        "kind = EXCLUDED.kind, state = EXCLUDED.state, "
        "target_out_at = COALESCE(EXCLUDED.target_out_at, auto_lunch_runs.target_out_at), "
        "target_in_at  = COALESCE(EXCLUDED.target_in_at,  auto_lunch_runs.target_in_at), "
        "wc_name       = COALESCE(EXCLUDED.wc_name,       auto_lunch_runs.wc_name), "
        "out_punch_id  = COALESCE(EXCLUDED.out_punch_id,  auto_lunch_runs.out_punch_id), "
        "in_punch_id   = COALESCE(EXCLUDED.in_punch_id,   auto_lunch_runs.in_punch_id), "
        "updated_at = now()",
        (person_odoo_id, day, kind, state, target_out_at, target_in_at,
         wc_name, out_punch_id, in_punch_id),
    )


def _write_auto_punch(person_odoo_id, action, wc_name, occurred_at) -> int:
    """Insert an auto-lunch punch stamped at the scheduled boundary time.
    source='auto_lunch'; rounded_at = occurred_at (it IS the schedule, no
    rounding). Returns the new log id; caller triggers the Odoo sync."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO timeclock_punches_log "
            "(person_odoo_id, action, wc_name, occurred_at, rounded_at, source) "
            "VALUES (%s, %s, %s, %s, %s, 'auto_lunch') RETURNING id",
            (person_odoo_id, action, wc_name, occurred_at, occurred_at),
        )
        return cur.fetchone()["id"]


def _window_for(person_odoo_id, kind, today, fixed_window, settings) -> Window | None:
    if kind == "scheduled":
        return fixed_window
    first_in = _first_clock_in(person_odoo_id, today)
    if first_in is None:
        return None
    return flex_window(first_in, settings.flex_after_hours, settings.flex_minutes)


def _apply(person_odoo_id, today, kind, run, t, state, window, settings) -> None:
    if t.action == "clock_out":
        wc_name = state["current_wc"]
        out_id = None
        if not settings.observe_only:
            out_id = _write_auto_punch(person_odoo_id, "clock_out", None, t.at)
            timeclock_sync.sync_one_by_id(out_id)
        _log.info("auto-lunch %s: person %s clock_out @ %s (wc=%s)",
                  "OBSERVE" if settings.observe_only else "LIVE",
                  person_odoo_id, t.at, wc_name)
        _upsert_run(person_odoo_id, today, kind, "auto_out",
                    target_out_at=window.out_at, target_in_at=window.in_at,
                    wc_name=wc_name, out_punch_id=out_id)
    elif t.action == "clock_in":
        wc_name = run["wc_name"] if run else None
        in_id = None
        if not settings.observe_only:
            in_id = _write_auto_punch(person_odoo_id, "clock_in", wc_name, t.at)
            timeclock_sync.sync_one_by_id(in_id)
        _log.info("auto-lunch %s: person %s clock_in @ %s (wc=%s)",
                  "OBSERVE" if settings.observe_only else "LIVE",
                  person_odoo_id, t.at, wc_name)
        _upsert_run(person_odoo_id, today, kind, "done", in_punch_id=in_id)
    else:
        _upsert_run(person_odoo_id, today, kind, t.new_state,
                    target_out_at=window.out_at, target_in_at=window.in_at)


def _advance_person(person_odoo_id, today, now, fixed_window, flex_ids, settings) -> None:
    kind = "flex" if person_odoo_id in flex_ids else "scheduled"
    window = _window_for(person_odoo_id, kind, today, fixed_window, settings)
    if window is None:
        return
    run = _get_run(person_odoo_id, today)
    run_state = run["state"] if run else "pending"
    if run_state in TERMINAL:
        return
    state = attendance_state.current_state(person_odoo_id)
    is_in = state["is_clocked_in"]
    # Observe-only simulation: we never actually clocked them out, so the real
    # state still reads clocked-in. Pretend clocked-out after an observed
    # auto_out so the auto sign-in is previewed too.
    if settings.observe_only and run_state == "auto_out":
        is_in = False
    t = decide(run_state, is_in, window, now)
    if t.new_state == run_state and t.action is None:
        return
    _apply(person_odoo_id, today, kind, run, t, state, window, settings)


def run_tick(now: datetime | None = None) -> None:
    """One worker sweep. Safe to call every ~60s. No-op when disabled or when
    the open-attendance cache is missing/stale (never act on unknown state)."""
    settings = auto_lunch_settings.current()
    if not settings.enabled:
        return
    now = (now or datetime.now(shift_config.SITE_TZ)).astimezone(shift_config.SITE_TZ)
    today = now.date()

    fixed_window = None
    if shift_config.is_workday(today):
        fixed_window = lunch_window_for_day(shift_config.breaks_for(today), today)

    snapshot, refreshed_at = live_cache.read_open_attendance()
    if snapshot is None or live_cache.is_stale(refreshed_at):
        _log.info("auto-lunch: open-attendance cache missing/stale; skipping tick")
        return

    flex_ids = _flex_person_ids()
    clocked_in = {int(k) for k in snapshot.keys()}
    open_runs = {int(r["person_odoo_id"]) for r in db.query(
        "SELECT person_odoo_id FROM auto_lunch_runs WHERE day = %s "
        "AND state NOT IN ('done','skipped','ended_by_employee')", (today,))}
    for pid in clocked_in | open_runs:
        try:
            _advance_person(pid, today, now, fixed_window, flex_ids, settings)
        except Exception as e:  # noqa: BLE001 — one person never kills the tick
            _log.warning("auto-lunch: failed for person %s: %s", pid, e)


def active_lunch_run(person_odoo_id: int, now: datetime) -> dict | None:
    """The in-progress lunch gap for this person right now (state 'auto_out'
    and now within [target_out_at, target_in_at)), or None. The kiosk uses it
    to keep showing the on-shift 'sign out' action during the gap."""
    now = now.astimezone(shift_config.SITE_TZ)
    run = _get_run(person_odoo_id, now.date())
    if not run or run["state"] != "auto_out":
        return None
    out_at, in_at = run["target_out_at"], run["target_in_at"]
    if out_at is None or in_at is None:
        return None
    return run if out_at <= now < in_at else None


def note_employee_clock_out(person_odoo_id: int, now: datetime | None = None) -> bool:
    """Called when an employee signs out. If they're mid auto-lunch-gap, end
    their day here: cancel the pending auto sign-in. Returns True if a run was
    ended. Idempotent."""
    now = (now or datetime.now(shift_config.SITE_TZ)).astimezone(shift_config.SITE_TZ)
    if active_lunch_run(person_odoo_id, now) is None:
        return False
    db.execute(
        "UPDATE auto_lunch_runs SET state = 'ended_by_employee', updated_at = now() "
        "WHERE person_odoo_id = %s AND day = %s AND state = 'auto_out'",
        (person_odoo_id, now.date()),
    )
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auto_lunch_worker.py -v` (with `DATABASE_URL`)
Expected: PASS (5 tests). Locally (no `DATABASE_URL`): SKIPPED, but `pytest tests/test_auto_lunch_decide.py -v` still PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/auto_lunch.py tests/test_auto_lunch_worker.py
git commit -m "feat(auto-lunch): worker run_tick, run store, overlay + cancel helpers"
```

---

## Task 6: Flex detection sync (Odoo → `people.is_flexible`)

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py` (`fetch_work_schedules`)
- Modify: `src/zira_dashboard/odoo_sync.py` (`sync`)
- Test: `tests/test_auto_lunch_flex_sync.py` (pure-logic, local)

- [ ] **Step 1: Confirm the Odoo field name (verification, not code)**

The flex signal is the resource.calendar "Schedule Type" field (value *flexible* vs fully-fixed). Confirm its technical name + flexible value against the live Odoo before trusting the default below. Run from a Railway shell (or any env with Odoo creds):
```bash
python -c "from zira_dashboard import odoo_client as o; import json; \
print(json.dumps(o.execute('resource.calendar','search_read',[('active','=',True)], \
fields=['id','name','flexible_hours']), default=str))"
```
- If `flexible_hours` (boolean) exists → keep `SCHEDULE_TYPE_FIELD = "flexible_hours"` below.
- If it errors / the field is a selection (e.g. `schedule_type` with `'flexible'`) → set `SCHEDULE_TYPE_FIELD` to that name. `_is_flexible()` already handles both bool and the string `'flexible'`.

- [ ] **Step 2: Write the failing test**

`tests/test_auto_lunch_flex_sync.py`:
```python
"""Pure-logic tests for flex detection mapping in odoo_client. Stubs execute."""
from __future__ import annotations

from unittest.mock import MagicMock

from zira_dashboard import odoo_client


def test_is_flexible_handles_bool_and_selection():
    assert odoo_client._is_flexible(True) is True
    assert odoo_client._is_flexible(False) is False
    assert odoo_client._is_flexible("flexible") is True
    assert odoo_client._is_flexible("fully_fixed") is False
    assert odoo_client._is_flexible(None) is False


def test_fetch_work_schedules_maps_is_flexible(monkeypatch):
    fake = MagicMock(return_value=[
        {"id": 1, "name": "Standard", odoo_client.SCHEDULE_TYPE_FIELD: False},
        {"id": 2, "name": "Flexible", odoo_client.SCHEDULE_TYPE_FIELD: True},
    ])
    monkeypatch.setattr(odoo_client, "execute", fake)

    out = odoo_client.fetch_work_schedules()

    # The schedule-type field is requested.
    _args, kwargs = fake.call_args
    assert odoo_client.SCHEDULE_TYPE_FIELD in kwargs["fields"]
    assert out == [
        {"id": 1, "name": "Standard", "is_flexible": False},
        {"id": 2, "name": "Flexible", "is_flexible": True},
    ]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_auto_lunch_flex_sync.py -v`
Expected: FAIL — `module 'zira_dashboard.odoo_client' has no attribute '_is_flexible'`.

- [ ] **Step 4: Update `odoo_client.fetch_work_schedules`**

Replace the existing `fetch_work_schedules` (lines 235–241) in `src/zira_dashboard/odoo_client.py`:
```python
# Odoo "Schedule Type" on resource.calendar. Confirmed against live Odoo
# (Task 6 Step 1). Odoo 18 exposes flexible scheduling as the boolean
# `flexible_hours`; if your instance uses a selection, change this name —
# _is_flexible() already accepts both a bool and the string 'flexible'.
SCHEDULE_TYPE_FIELD = "flexible_hours"


def _is_flexible(value) -> bool:
    """Interpret the resource.calendar Schedule Type value as a flex flag.
    Accepts a boolean (Odoo 18 `flexible_hours`) or a selection string."""
    if isinstance(value, str):
        return value.strip().lower() == "flexible"
    return bool(value)


def fetch_work_schedules() -> list[dict]:
    """Active working schedules (resource.calendar):
    [{id, name, is_flexible}, ...]. is_flexible drives the auto-lunch
    elapsed-time trigger for flexible-schedule employees."""
    rows = execute(
        "resource.calendar", "search_read",
        [("active", "=", True)],
        fields=["id", "name", SCHEDULE_TYPE_FIELD],
    )
    return [
        {"id": r["id"], "name": r.get("name") or "",
         "is_flexible": _is_flexible(r.get(SCHEDULE_TYPE_FIELD))}
        for r in rows
    ]
```
(Note: `refresh_work_schedule_hours` in `odoo_sync.py` reads only `c["id"]`/`c.get("name")` from this return value — still compatible.)

- [ ] **Step 5: Populate `people.is_flexible` in `odoo_sync.sync`**

In `src/zira_dashboard/odoo_sync.py`, inside `sync()`'s `try:` block, add a fetch alongside the others (after `departments = odoo_client.fetch_departments()`):
```python
        work_schedules_meta = odoo_client.fetch_work_schedules()
```
After `type_by_skill = {...}` / before the `with db.cursor() as cur:` block, build the flex set:
```python
    flex_cal_ids = {c["id"] for c in work_schedules_meta if c.get("is_flexible")}
```
In the employees upsert loop, compute the flag and add it to the INSERT. Replace the existing people upsert `cur.execute(...)` (lines 147–159) with:
```python
            is_flex = _m2o_id(emp.get("resource_calendar_id")) in flex_cal_ids
            cur.execute(
                "INSERT INTO people (odoo_id, name, active, wage_type, spanish_speaker, "
                "resource_calendar_id, is_flexible, last_pulled_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (odoo_id) DO UPDATE SET name = EXCLUDED.name, "
                "active = EXCLUDED.active, wage_type = EXCLUDED.wage_type, "
                "spanish_speaker = EXCLUDED.spanish_speaker, "
                "resource_calendar_id = EXCLUDED.resource_calendar_id, "
                "is_flexible = EXCLUDED.is_flexible, "
                "last_pulled_at = EXCLUDED.last_pulled_at",
                (emp["id"], _short_name(emp["name"]), bool(emp.get("active", True)),
                 wage_type, spanish_speaker, _m2o_id(emp.get("resource_calendar_id")),
                 is_flex, pulled_at),
            )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_auto_lunch_flex_sync.py -v`
Expected: PASS (2 tests).
Run (CI): `pytest tests/test_odoo_sync.py tests/test_odoo_sync_calendars.py -v` — Expected: still PASS (existing sync tests unaffected; if one asserts the exact people INSERT SQL, update it to include `is_flexible`).

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/odoo_client.py src/zira_dashboard/odoo_sync.py tests/test_auto_lunch_flex_sync.py
git commit -m "feat(auto-lunch): sync Odoo Schedule Type -> people.is_flexible"
```

---

## Task 7: Kiosk overlay — show "sign out" during the lunch gap

**Files:**
- Modify: `src/zira_dashboard/routes/timeclock.py` (`timeclock_dashboard`)
- Test: covered by `tests/test_auto_lunch_worker.py::test_active_lunch_run_only_inside_window` (the overlay's data source); the route wiring is verified by `py_compile` in CI.

- [ ] **Step 1: Apply the overlay in the dashboard route**

In `src/zira_dashboard/routes/timeclock.py`, in `timeclock_dashboard`, replace the state read (line 461) and add the overlay:
```python
    # Local-DB read — no Odoo XML-RPC on the hot path. See attendance_state.
    state = _current_state(p["odoo_id"]) if p.get("odoo_id") else _current_state(-1)

    # Auto-lunch overlay: during the lunch gap the employee is still "on shift"
    # from their point of view (the auto sign-out is invisible payroll), so keep
    # showing the sign-out action. A sign-out during the gap ends their day
    # (handled in kiosk_clock_out).
    on_lunch = False
    if p.get("odoo_id"):
        from .. import auto_lunch
        now_local = datetime.now(timezone.utc).astimezone(shift_config.SITE_TZ)
        lunch_run = auto_lunch.active_lunch_run(p["odoo_id"], now_local)
        if lunch_run is not None:
            state = {**state, "is_clocked_in": True,
                     "current_wc": lunch_run.get("wc_name")}
            on_lunch = True
```
Then add `"on_lunch": on_lunch,` to the template context dict passed to `timeclock_dashboard.html` (alongside `"is_clocked_in": state["is_clocked_in"],`).

- [ ] **Step 2: (Optional, minimal) surface the lunch banner**

In `templates/timeclock_dashboard.html`, in the clocked-in block, add a small note shown when `on_lunch`:
```html
{% if on_lunch %}
  <p class="lunch-note">On lunch — tap <strong>Sign Out</strong> only if you're leaving for the day.</p>
{% endif %}
```
(If the template structure differs, place it near the existing clocked-in status text. This is cosmetic; the functional requirement — the sign-out action appearing — is satisfied by `is_clocked_in=True` above.)

- [ ] **Step 3: Verify compile**

Run: `python -c "import ast; ast.parse(open('src/zira_dashboard/routes/timeclock.py').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/timeclock.py templates/timeclock_dashboard.html
git commit -m "feat(auto-lunch): kiosk shows sign-out during the lunch gap"
```

---

## Task 8: Cancel the auto sign-in when an employee signs out mid-gap

**Files:**
- Modify: `src/zira_dashboard/routes/timeclock.py` (`kiosk_clock_out`)
- Test: covered by `tests/test_auto_lunch_worker.py::test_note_employee_clock_out_cancels_auto_in`.

- [ ] **Step 1: Hook the cancel into clock-out**

In `src/zira_dashboard/routes/timeclock.py`, in `kiosk_clock_out`, after the log row is written (after line 586 `log_id, rounded_at = _open_log_row(odoo_id, "clock_out", None)`), add:
```python
    # If they're signing out mid auto-lunch, end the day here: cancel the
    # pending auto sign-in. The morning attendance is already closed at lunch
    # start, so the Odoo sync of this clock_out is a safe no-op.
    from .. import auto_lunch
    auto_lunch.note_employee_clock_out(odoo_id)
```
(Place it before `background_tasks.add_task(...)`. Order doesn't matter — `note_employee_clock_out` only flips the run row.)

- [ ] **Step 2: Verify compile**

Run: `python -c "import ast; ast.parse(open('src/zira_dashboard/routes/timeclock.py').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/routes/timeclock.py
git commit -m "feat(auto-lunch): sign-out during lunch gap ends day, cancels auto sign-in"
```

---

## Task 9: Register the background loop

**Files:**
- Modify: `src/zira_dashboard/app.py` (add the loop + lifespan wiring)
- Test: verified by `py_compile` (CI) + the manual Railway check in Task 10.

- [ ] **Step 1: Add the loop function**

In `src/zira_dashboard/app.py`, next to `_warm_odoo_attendance_loop` (after line ~103), add:
```python
async def _warm_auto_lunch_loop():
    """Drive the auto-lunch worker every 60s. Mirrors the other warmers:
    runs the sync worker off the event loop, swallows errors so the loop
    never dies, sleeps 60s between ticks."""
    from . import auto_lunch
    while True:
        try:
            await asyncio.to_thread(auto_lunch.run_tick)
        except Exception as e:  # noqa: BLE001
            _log.warning("auto-lunch tick failed: %s", e)
        await asyncio.sleep(60)
```

- [ ] **Step 2: Wire it into `lifespan`**

In `lifespan()`, alongside the other `asyncio.create_task(...)` calls (after line ~236):
```python
    auto_lunch_task = asyncio.create_task(_warm_auto_lunch_loop())
```
In the shutdown section (where the other tasks are cancelled after `yield` — mirror the exact pattern used there, e.g. a `.cancel()` plus the gathered `await` with `return_exceptions=True`), add `auto_lunch_task` to the cancelled/awaited set the same way the siblings are handled.

- [ ] **Step 3: Verify compile**

Run: `python -c "import ast; ast.parse(open('src/zira_dashboard/app.py').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/app.py
git commit -m "feat(auto-lunch): register _warm_auto_lunch_loop (60s)"
```

---

## Task 10: Rollout — ship dark, observe a day, then go live

No code; this is the controlled enable. The feature ships with `enabled=FALSE`, so merging/deploying changes nothing until flipped.

- [ ] **Step 1: Deploy & confirm dark**

Push to `main`; Railway deploys. Confirm bootstrap created the tables and the worker is idle:
```bash
# Railway shell:
python -c "from zira_dashboard import auto_lunch_settings as s; print(s.current())"
```
Expected: `Settings(enabled=False, observe_only=True, flex_after_hours=5.0, flex_minutes=30)`.

- [ ] **Step 2: Confirm the Odoo flex field (if not already done in Task 6 Step 1)**

Verify Juan & Benjamin resolve as flex after a sync:
```bash
python -c "from zira_dashboard import odoo_sync, db; odoo_sync.sync(force=True); \
print(db.query(\"SELECT name, is_flexible FROM people WHERE is_flexible = TRUE\"))"
```
Expected: a row for each flex employee (Juan, Benjamin). If empty, the `SCHEDULE_TYPE_FIELD` name/value is wrong — fix per Task 6 Step 1 and redeploy.

- [ ] **Step 3: Enable observe-only for one full workday**

```bash
python -c "from zira_dashboard import auto_lunch_settings as s; c=s.current(); \
s.save(s.Settings(enabled=True, observe_only=True, \
flex_after_hours=c.flex_after_hours, flex_minutes=c.flex_minutes))"
```
Let a full workday pass. Then review what it *would* have done — no punches were written:
```bash
python -c "from zira_dashboard import db; \
print(db.query(\"SELECT person_odoo_id, kind, state, target_out_at, target_in_at \"\
\"FROM auto_lunch_runs WHERE day = CURRENT_DATE ORDER BY person_odoo_id\"))"
```
Expected: a row per active employee — fixed staff with the day's lunch window, flex staff (Juan/Benjamin) with their first-in+5h window. Sanity-check the times against reality. Also grep the app logs for `auto-lunch OBSERVE:` lines.

- [ ] **Step 4: Go live (next day)**

Once the observe day looks right:
```bash
python -c "from zira_dashboard import auto_lunch_settings as s; c=s.current(); \
s.save(s.Settings(enabled=True, observe_only=False, \
flex_after_hours=c.flex_after_hours, flex_minutes=c.flex_minutes))"
```
Verify against Odoo after lunch: each clocked-in employee has a morning `hr.attendance` closed at lunch start and an afternoon one opened at lunch end (two records; worked hours exclude lunch).

- [ ] **Step 5: Spot-check the manual-punch paths in the real kiosk**

- Clocked-in employee, no action → auto out at lunch start, auto in at lunch end. ✅
- Employee taps **Sign Out** during the gap → day ends; no afternoon record appears at lunch end. ✅
- Employee already clocked out before lunch → no auto-in. ✅

---

## Self-Review

**1. Spec coverage** (each spec section → task):
- Fixed-schedule lunch from `breaks_for(day)` Lunch window → Tasks 4 (`lunch_window_for_day`), 5 (`run_tick`).
- Flex: Schedule Type detection → Task 6; elapsed-since-first-clock-in, one/day → Tasks 4 (`flex_window`, `decide`), 5 (`_first_clock_in`, `UNIQUE(person,day)` from Task 1).
- Architecture A (punch log + sync + `source` tag) → Tasks 1, 5 (`_write_auto_punch`, `sync_one_by_id`).
- Manual punches / shift-session sign-out → Tasks 7 (overlay), 8 (cancel), 5 (`active_lunch_run`, `note_employee_clock_out`).
- Always full lunch / no early-return → `decide` has no early-in path (Task 4); overlay shows only sign-out (Task 7).
- Two Odoo records/day → inherent in the morning-close + afternoon-open writes (Task 5) via existing sync.
- Reconciliation reuse + race-guard → Task 3 (`attendance_state`), exercised by `test_current_state_unsynced_autoout_reads_clocked_out`.
- Workday gating / no-lunch day → `run_tick` checks `is_workday`; `lunch_window_for_day` returns None (Tasks 4, 5).
- Rollout: master toggle + observe-only → Tasks 2, 5 (`_apply` observe branch), 10.
- Settings persistence/cache → Task 2.

**2. Placeholder scan:** No "TBD"/"implement later". The only deferred item — the exact Odoo Schedule Type field name — has a concrete default (`flexible_hours`), a `_is_flexible()` that accepts both bool and selection forms, and an explicit verification step (Task 6 Step 1, Task 10 Step 2). The settings UI panel is intentionally out of scope (toggled via the store; documented commands in Task 10) per the spec's "minor UI add" framing.

**3. Type consistency:** `Window(out_at, in_at)` and `Transition(new_state, action, at)` are used identically across Tasks 4–5. `decide(run_state, is_clocked_in, window, now)` signature matches all call sites. `current_state()` returns the same dict keys (`is_clocked_in`, `current_wc`, `check_in_ts`, `open_odoo_attendance_id`) in Task 3 and is consumed in Task 5 (`state["is_clocked_in"]`, `state["current_wc"]`) and Task 7. `fetch_work_schedules()` returns `{id, name, is_flexible}` in Task 6, consumed as `c["id"]`/`c.get("is_flexible")` in `odoo_sync`. Run states (`pending/auto_out/done/skipped/ended_by_employee`) match the CHECK constraint (Task 1), `TERMINAL`, `decide`, and the worker queries.

**Out of scope (future):** a settings panel in the sidebar UI; per-employee flex parameters; reconciling historical/manually-edited lunch gaps.
