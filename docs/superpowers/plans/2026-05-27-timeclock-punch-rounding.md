# Timeclock Punch Rounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable plant-wide rounding to timeclock punches so `clock_in` and `clock_out` values within a configured window of scheduled shift start/end are rounded to scheduled time before being written to Odoo `hr.attendance`. Raw and rounded times are both stored on `kiosk_punches_log` for audit.

**Architecture:** A pure function `rounding.apply_rounding(...)` computes the rounded timestamp from raw `occurred_at`, scheduled times, and a `RoundingSettings` dataclass. A `rounding_store` singleton (cached in-process like `schedule_store`) loads/saves the four config values. The kiosk write path computes the rounded value in the same transaction as the INSERT and stores it on a new `kiosk_punches_log.rounded_at` column. Every downstream read (Odoo sync, dashboard, etc.) uses `COALESCE(rounded_at, occurred_at)` so historical rows keep working.

**Tech Stack:** Python 3.11+, FastAPI, psycopg + Postgres, Jinja2 templates, pytest. Spec at `docs/superpowers/specs/2026-05-27-timeclock-punch-rounding-design.md`.

**Deferred from spec:** Spec section 4(d) — late_report rounding integration. The current `late_report.late_people_for_day` operates on a StratusTime-sourced attendance dict that does not know about kiosk punches. Plumbing rounded values through that path requires deciding whether to merge `kiosk_punches_log` into the attendance dict, which is a separate design conversation. The current rounding feature is observable end-to-end (kiosk display + Odoo) without this change. Flagged in the CHANGELOG entry.

---

## File Structure

**New files:**
- `src/zira_dashboard/rounding.py` — `RoundingSettings` frozen dataclass + `apply_rounding(action, occurred_at, shift_start, shift_end, settings) -> datetime` pure function. No DB, no imports from other project modules except `shift_config` for `SITE_TZ`. Easy to unit-test in isolation.
- `src/zira_dashboard/rounding_store.py` — singleton load/save with `_cache: RoundingSettings | None` + `RLock`. Mirrors the shape of `schedule_store.py`: `current()`, `save(settings)`, `reload()`, `DEFAULT_SETTINGS`. Cache invalidated on save.
- `tests/test_rounding.py` — pure-function tests; no DB needed.
- `tests/test_rounding_store.py` — DB-backed tests for the store; gated on `DATABASE_URL`.

**Modified files:**
- `src/zira_dashboard/db.py` — DDL block appended at the end of `BOOTSTRAP_SQL` for `rounding_settings` table and `kiosk_punches_log.rounded_at` ALTER.
- `src/zira_dashboard/routes/kiosk.py` — `_open_log_row` returns `(id, rounded)` and writes `rounded_at`; the three handlers (`kiosk_clock_in`, `kiosk_clock_out`, `kiosk_transfer`) pass the rounded value to `_fmt_time` for the success page; `_current_state` SELECT uses `COALESCE(rounded_at, occurred_at)`.
- `src/zira_dashboard/kiosk_sync.py` — `retry_unsynced_punches` and `sync_one_by_id` SELECTs use `COALESCE(rounded_at, occurred_at) AS occurred_at`.
- `src/zira_dashboard/routes/settings.py` — extend `settings_page` to load current rounding settings and pass to template; new `POST /settings/rounding` route that validates and saves; add `"rounding"` to the allowed `section` values.
- `src/zira_dashboard/templates/settings.html` — new Rounding section in the schedule area or as its own tab (follow existing tab pattern).
- `CHANGELOG.md` — entry under today's date.

---

## Task 1: Schema migration

**Files:**
- Modify: `src/zira_dashboard/db.py` (append to `BOOTSTRAP_SQL` constant before the closing `"""`)

- [ ] **Step 1: Add the new table and ALTER to BOOTSTRAP_SQL**

Locate the existing kiosk schema block in `src/zira_dashboard/db.py` (search for `CREATE TABLE IF NOT EXISTS kiosk_punches_log`). After the `kiosk_schedule_variances` index block (around line 678 — the line that reads `ON kiosk_schedule_variances (occurred_at);`), append this block **before** the closing `"""` of `BOOTSTRAP_SQL`:

```sql

-- Rounding settings (2026-05-27): plant-wide timeclock punch rounding,
-- modeled on StratusTime's "Round To Schedule" feature. Singleton row
-- (id=1) holds four integers — the four window edges. Zero on all four
-- = no rounding (ships disabled).
CREATE TABLE IF NOT EXISTS rounding_settings (
  id              INT PRIMARY KEY DEFAULT 1,
  in_before_min   INT NOT NULL DEFAULT 0,
  in_after_min    INT NOT NULL DEFAULT 0,
  out_before_min  INT NOT NULL DEFAULT 0,
  out_after_min   INT NOT NULL DEFAULT 0,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT rounding_settings_singleton CHECK (id = 1)
);
INSERT INTO rounding_settings (id) VALUES (1) ON CONFLICT DO NOTHING;

-- Store both raw and rounded timestamps so historical audit is preserved.
-- Columns added separately (not in the CREATE TABLE above) because
-- kiosk_punches_log already exists in production.
ALTER TABLE kiosk_punches_log
  ADD COLUMN IF NOT EXISTS rounded_at TIMESTAMPTZ;
```

- [ ] **Step 2: Verify the bootstrap runs cleanly**

If `DATABASE_URL` is set in your shell, run:

```bash
python -c "from zira_dashboard import db; db.init_pool(); db.bootstrap_schema(); print('ok')"
```

Expected: prints `ok`. If you don't have `DATABASE_URL`, skip this verification — Task 3's tests will exercise the schema.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/db.py
git commit -m "$(cat <<'EOF'
feat(timeclock): rounding_settings table + kiosk_punches_log.rounded_at

Schema for plant-wide punch rounding. Additive: CREATE TABLE IF NOT
EXISTS for settings singleton, ALTER TABLE ADD COLUMN IF NOT EXISTS
for the audit column. Safe to deploy without coordination.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `apply_rounding` pure function

**Files:**
- Create: `src/zira_dashboard/rounding.py`
- Test: `tests/test_rounding.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/test_rounding.py`:

```python
"""Tests for the pure rounding function. No DB needed — apply_rounding
takes the schedule times and settings as parameters."""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from zira_dashboard.rounding import RoundingSettings, apply_rounding

SITE_TZ = ZoneInfo("America/Chicago")


def _local(year, month, day, hour, minute):
    """Build a UTC datetime that represents the given site-local wall time."""
    return datetime(year, month, day, hour, minute, tzinfo=SITE_TZ).astimezone(timezone.utc)


SHIFT_START = time(7, 0)
SHIFT_END = time(15, 30)


def test_clock_in_within_before_window_rounds_to_start():
    """6:50 AM clock_in with in_before=20 rounds UP to 7:00 AM."""
    occurred = _local(2026, 5, 27, 6, 50)
    settings = RoundingSettings(in_before_min=20, in_after_min=0, out_before_min=0, out_after_min=0)
    rounded = apply_rounding("clock_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == _local(2026, 5, 27, 7, 0)


def test_clock_in_outside_before_window_unchanged():
    """6:38 AM (22 min before) with in_before=20 stays as 6:38."""
    occurred = _local(2026, 5, 27, 6, 38)
    settings = RoundingSettings(20, 0, 0, 0)
    rounded = apply_rounding("clock_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == occurred


def test_clock_in_within_after_window_rounds_to_start():
    """7:05 AM with in_after=10 rounds DOWN to 7:00."""
    occurred = _local(2026, 5, 27, 7, 5)
    settings = RoundingSettings(0, 10, 0, 0)
    rounded = apply_rounding("clock_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == _local(2026, 5, 27, 7, 0)


def test_clock_in_outside_after_window_unchanged():
    """9:00 AM (came in 2hr late) with in_after=10 stays as 9:00."""
    occurred = _local(2026, 5, 27, 9, 0)
    settings = RoundingSettings(0, 10, 0, 0)
    rounded = apply_rounding("clock_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == occurred


def test_clock_out_within_after_window_rounds_to_end():
    """3:35 PM clock_out with out_after=20 rounds DOWN to 3:30 PM."""
    occurred = _local(2026, 5, 27, 15, 35)
    settings = RoundingSettings(0, 0, 0, 20)
    rounded = apply_rounding("clock_out", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == _local(2026, 5, 27, 15, 30)


def test_clock_out_within_before_window_rounds_to_end():
    """3:25 PM clock_out with out_before=10 rounds UP to 3:30 PM."""
    occurred = _local(2026, 5, 27, 15, 25)
    settings = RoundingSettings(0, 0, 10, 0)
    rounded = apply_rounding("clock_out", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == _local(2026, 5, 27, 15, 30)


def test_clock_out_early_leave_unchanged():
    """1:00 PM clock_out, outside window, stays."""
    occurred = _local(2026, 5, 27, 13, 0)
    settings = RoundingSettings(0, 0, 60, 60)
    rounded = apply_rounding("clock_out", occurred, SHIFT_START, SHIFT_END, settings)
    # 13:00 is 2.5h before 15:30, way outside the 60-min before window
    assert rounded == occurred


def test_transfer_in_never_rounded():
    """transfer_in at 6:50 AM with in_before=20 stays at 6:50 (transfers are never rounded)."""
    occurred = _local(2026, 5, 27, 6, 50)
    settings = RoundingSettings(20, 20, 20, 20)
    rounded = apply_rounding("transfer_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == occurred


def test_transfer_out_never_rounded():
    """transfer_out at 3:35 PM with out_after=20 stays at 3:35."""
    occurred = _local(2026, 5, 27, 15, 35)
    settings = RoundingSettings(20, 20, 20, 20)
    rounded = apply_rounding("transfer_out", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == occurred


def test_zero_window_disables_rounding():
    """All settings = 0 → every clock_in/clock_out returns occurred_at unchanged."""
    settings = RoundingSettings(0, 0, 0, 0)
    for hh, mm in [(6, 50), (7, 0), (7, 5), (15, 25), (15, 30), (15, 35)]:
        occurred = _local(2026, 5, 27, hh, mm)
        for action in ("clock_in", "clock_out"):
            assert apply_rounding(action, occurred, SHIFT_START, SHIFT_END, settings) == occurred


def test_boundary_at_exact_window_edge_inclusive():
    """6:40 AM with in_before=20 (exactly 20 min before 7:00) rounds — bound is inclusive."""
    occurred = _local(2026, 5, 27, 6, 40)
    settings = RoundingSettings(20, 0, 0, 0)
    rounded = apply_rounding("clock_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == _local(2026, 5, 27, 7, 0)


def test_custom_shift_times_round_to_those():
    """Saturday OT with custom 8:00 AM start: 7:50 AM rounds to 8:00, not the default 7:00."""
    occurred = _local(2026, 5, 30, 7, 50)  # Saturday
    custom_start = time(8, 0)
    custom_end = time(12, 0)
    settings = RoundingSettings(20, 0, 0, 0)
    rounded = apply_rounding("clock_in", occurred, custom_start, custom_end, settings)
    assert rounded == _local(2026, 5, 30, 8, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_rounding.py -v
```

Expected: collection error or all 12 tests fail with `ModuleNotFoundError: No module named 'zira_dashboard.rounding'`.

- [ ] **Step 3: Implement `rounding.py`**

Create `src/zira_dashboard/rounding.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_rounding.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/rounding.py tests/test_rounding.py
git commit -m "$(cat <<'EOF'
feat(timeclock): pure rounding function + RoundingSettings dataclass

apply_rounding(action, occurred_at, shift_start, shift_end, settings)
returns the rounded UTC timestamp, or occurred_at unchanged if outside
the configured window. Transfers (transfer_in / transfer_out) always
pass through. 12 unit tests cover before/after windows, transfers,
zero-window disabling, custom shift times, boundary edges.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `rounding_store` with module cache

**Files:**
- Create: `src/zira_dashboard/rounding_store.py`
- Test: `tests/test_rounding_store.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/test_rounding_store.py`:

```python
"""Tests for rounding_store load/save/cache behavior. Postgres-backed."""

import os

import pytest

from zira_dashboard import db, rounding_store
from zira_dashboard.rounding import RoundingSettings


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)


@pytest.fixture(autouse=True)
def _reset_settings():
    """Reset to ship-disabled defaults around each test, AND clear the cache."""
    db.execute(
        "UPDATE rounding_settings SET in_before_min = 0, in_after_min = 0, "
        "out_before_min = 0, out_after_min = 0 WHERE id = 1"
    )
    rounding_store.reload()
    yield
    db.execute(
        "UPDATE rounding_settings SET in_before_min = 0, in_after_min = 0, "
        "out_before_min = 0, out_after_min = 0 WHERE id = 1"
    )
    rounding_store.reload()


def test_default_settings_all_zero():
    s = rounding_store.current()
    assert s == RoundingSettings(0, 0, 0, 0)


def test_save_persists_and_invalidates_cache():
    rounding_store.save(RoundingSettings(20, 0, 0, 20))
    # Reload from DB to confirm persistence.
    rounding_store.reload()
    s = rounding_store.current()
    assert s == RoundingSettings(20, 0, 0, 20)


def test_save_returns_settings_via_current_immediately():
    """After save(), current() should reflect the new values without
    requiring an explicit reload."""
    rounding_store.save(RoundingSettings(15, 5, 5, 15))
    assert rounding_store.current() == RoundingSettings(15, 5, 5, 15)


def test_current_is_cached():
    """Mutating the DB directly without calling save() should NOT show up
    in current() until reload() is called — proves the cache works."""
    rounding_store.current()  # prime the cache
    db.execute("UPDATE rounding_settings SET in_before_min = 99 WHERE id = 1")
    assert rounding_store.current().in_before_min == 0  # stale cache
    rounding_store.reload()
    assert rounding_store.current().in_before_min == 99
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_rounding_store.py -v
```

Expected: `ModuleNotFoundError: No module named 'zira_dashboard.rounding_store'`.

- [ ] **Step 3: Implement `rounding_store.py`**

Create `src/zira_dashboard/rounding_store.py`:

```python
"""Singleton rounding settings, cached in-process.

Mirrors schedule_store: settings get read on every kiosk punch, so an
in-process cache + RLock avoids hammering the DB. save() invalidates
the cache so the next current() reflects the new values.
"""

from __future__ import annotations

from threading import RLock

from .rounding import RoundingSettings

DEFAULT_SETTINGS = RoundingSettings(0, 0, 0, 0)

_lock = RLock()
_cache: RoundingSettings | None = None


def _load_from_db() -> RoundingSettings:
    from . import db
    rows = db.query(
        "SELECT in_before_min, in_after_min, out_before_min, out_after_min "
        "FROM rounding_settings WHERE id = 1"
    )
    if not rows:
        return DEFAULT_SETTINGS
    r = rows[0]
    return RoundingSettings(
        in_before_min=int(r["in_before_min"]),
        in_after_min=int(r["in_after_min"]),
        out_before_min=int(r["out_before_min"]),
        out_after_min=int(r["out_after_min"]),
    )


def current() -> RoundingSettings:
    """Return the cached singleton. Loads from DB on first call; subsequent
    calls hit the cache until save() or reload()."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load_from_db()
        return _cache


def save(settings: RoundingSettings) -> None:
    """Persist + update the cache so the next current() returns the new values."""
    global _cache
    from . import db
    db.execute(
        "INSERT INTO rounding_settings "
        "(id, in_before_min, in_after_min, out_before_min, out_after_min, updated_at) "
        "VALUES (1, %s, %s, %s, %s, now()) "
        "ON CONFLICT (id) DO UPDATE SET "
        "in_before_min = EXCLUDED.in_before_min, "
        "in_after_min = EXCLUDED.in_after_min, "
        "out_before_min = EXCLUDED.out_before_min, "
        "out_after_min = EXCLUDED.out_after_min, "
        "updated_at = now()",
        (
            settings.in_before_min,
            settings.in_after_min,
            settings.out_before_min,
            settings.out_after_min,
        ),
    )
    with _lock:
        _cache = settings


def reload() -> RoundingSettings:
    """Force a fresh read from Postgres, bypassing the cache."""
    global _cache
    with _lock:
        _cache = _load_from_db()
        return _cache
```

- [ ] **Step 4: Run tests to verify they pass**

If `DATABASE_URL` is set:

```bash
pytest tests/test_rounding_store.py -v
```

Expected: 4 passed.

If `DATABASE_URL` is not set, the file collects but all tests skip — confirm no errors:

```bash
pytest tests/test_rounding_store.py -v
```

Expected: 4 skipped (no errors).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/rounding_store.py tests/test_rounding_store.py
git commit -m "$(cat <<'EOF'
feat(timeclock): rounding_store singleton with in-process cache

Module-level cache + RLock mirrors schedule_store. current() returns
the cached RoundingSettings; save() persists and updates the cache so
the next call sees the new values without a reload.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire rounding into `_open_log_row`

**Files:**
- Modify: `src/zira_dashboard/routes/kiosk.py` (`_open_log_row` function, lines ~197-208)

This is the central integration. After each INSERT, we compute the rounded value and UPDATE the row before returning. The function signature changes from `-> int` to `-> tuple[int, datetime]` — callers need to be updated in Task 5.

- [ ] **Step 1: Replace `_open_log_row` to write `rounded_at`**

Open `src/zira_dashboard/routes/kiosk.py`. Find `_open_log_row` (around line 197). Replace it with:

```python
def _open_log_row(
    person_odoo_id: int, action: str, wc_name: str | None
) -> tuple[int, datetime]:
    """Insert a kiosk_punches_log row (synced=FALSE), compute the rounded
    timestamp using current rounding settings, write it back to the row,
    and return (id, rounded_at). Both occurred_at (raw) and rounded_at
    are persisted; everything downstream reads COALESCE(rounded_at,
    occurred_at)."""
    from .. import rounding, rounding_store, shift_config
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO kiosk_punches_log "
            "(person_odoo_id, action, wc_name) VALUES (%s, %s, %s) "
            "RETURNING id, occurred_at",
            (person_odoo_id, action, wc_name),
        )
        row = cur.fetchone()
        local_date = row["occurred_at"].astimezone(shift_config.SITE_TZ).date()
        rounded = rounding.apply_rounding(
            action,
            row["occurred_at"],
            shift_config.shift_start_for(local_date),
            shift_config.shift_end_for(local_date),
            rounding_store.current(),
        )
        cur.execute(
            "UPDATE kiosk_punches_log SET rounded_at = %s WHERE id = %s",
            (rounded, row["id"]),
        )
        return row["id"], rounded
```

- [ ] **Step 2: Update the three call sites in the same file**

Search for `_open_log_row(` in `src/zira_dashboard/routes/kiosk.py`. There are three calls — one in `kiosk_clock_in`, one in `kiosk_clock_out`, two in `kiosk_transfer`. Update each to unpack the tuple. Below are the exact edits:

In `kiosk_clock_in` (around line 337), change:

```python
    log_id = _open_log_row(odoo_id, "clock_in", wc_name)
```

to:

```python
    log_id, rounded_at = _open_log_row(odoo_id, "clock_in", wc_name)
```

And change the success-page render at the bottom of that handler from:

```python
            "time": _fmt_time(now),
```

to:

```python
            "time": _fmt_time(rounded_at),
```

In `kiosk_clock_out` (around line 370), change:

```python
    log_id = _open_log_row(odoo_id, "clock_out", None)
```

to:

```python
    log_id, rounded_at = _open_log_row(odoo_id, "clock_out", None)
```

And change `"time": _fmt_time(now),` to `"time": _fmt_time(rounded_at),` in the success-page render.

In `kiosk_transfer` (around line 398-399), change:

```python
    out_log = _open_log_row(odoo_id, "transfer_out", None)
    in_log = _open_log_row(odoo_id, "transfer_in", new_wc_name)
```

to:

```python
    out_log, _ = _open_log_row(odoo_id, "transfer_out", None)
    in_log, in_rounded = _open_log_row(odoo_id, "transfer_in", new_wc_name)
```

(Transfers are never rounded, so `in_rounded == occurred_at` — but we still pull it out to display the recorded time consistently.) Then update the `_fmt_time(now)` in the transfer success render to `_fmt_time(in_rounded)`.

- [ ] **Step 3: Run the existing kiosk-related tests to confirm nothing regressed**

```bash
pytest tests/ -k "kiosk or rounding" -v
```

Expected: all passing tests still pass. The rounding tests from Task 2 still pass. If `DATABASE_URL` is set, kiosk DB tests also pass.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/kiosk.py
git commit -m "$(cat <<'EOF'
feat(timeclock): write rounded_at on every kiosk punch

_open_log_row now computes and persists the rounded timestamp in the
same transaction as the INSERT. Returns (id, rounded_at) so the three
handlers (clock_in, clock_out, transfer) can render the rounded value
on the success page. Transfers pass through apply_rounding unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `_current_state` reads rounded time

**Files:**
- Modify: `src/zira_dashboard/routes/kiosk.py` (`_current_state` function, lines ~107-142)

- [ ] **Step 1: Update the SELECT in `_current_state`**

Find `_current_state` (around line 107). Change the SELECT statement from:

```python
    rows = db.query(
        "SELECT action, wc_name, occurred_at, odoo_attendance_id "
        "FROM kiosk_punches_log WHERE person_odoo_id = %s "
        "ORDER BY occurred_at DESC, id DESC LIMIT 1",
        (person_odoo_id,),
    )
```

to:

```python
    rows = db.query(
        "SELECT action, wc_name, "
        "COALESCE(rounded_at, occurred_at) AS occurred_at, "
        "odoo_attendance_id "
        "FROM kiosk_punches_log WHERE person_odoo_id = %s "
        "ORDER BY occurred_at DESC, id DESC LIMIT 1",
        (person_odoo_id,),
    )
```

(Note: still ORDER BY raw `occurred_at` — the punch order is determined by when the punch actually happened, not by the rounded value. Two punches at 6:50 and 6:55 both round to 7:00, but the 6:55 one is the latest.)

- [ ] **Step 2: Run kiosk tests**

```bash
pytest tests/ -k "kiosk" -v
```

Expected: same pass/skip status as before (no kiosk DB tests should regress).

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/routes/kiosk.py
git commit -m "$(cat <<'EOF'
feat(timeclock): kiosk dashboard shows rounded clock-in time

_current_state uses COALESCE(rounded_at, occurred_at) so the
'Clocked in at HH:MM' line matches what gets written to Odoo. Old
rows (rounded_at IS NULL) fall back to occurred_at.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `kiosk_sync` passes rounded time to Odoo

**Files:**
- Modify: `src/zira_dashboard/kiosk_sync.py` (lines 40-47, 117-119)

- [ ] **Step 1: Update both SELECT statements in `kiosk_sync.py`**

In `retry_unsynced_punches` (around line 40), change:

```python
    rows = db.query(
        "SELECT id, person_odoo_id, action, wc_name, occurred_at "
        "FROM kiosk_punches_log "
        "WHERE synced_to_odoo = FALSE "
        "ORDER BY occurred_at ASC, id ASC "
        "LIMIT %s",
        (_BATCH_SIZE,),
    )
```

to:

```python
    rows = db.query(
        "SELECT id, person_odoo_id, action, wc_name, "
        "COALESCE(rounded_at, occurred_at) AS occurred_at "
        "FROM kiosk_punches_log "
        "WHERE synced_to_odoo = FALSE "
        "ORDER BY occurred_at ASC, id ASC "
        "LIMIT %s",
        (_BATCH_SIZE,),
    )
```

In `sync_one_by_id` (around line 117), change:

```python
    rows = db.query(
        "SELECT id, person_odoo_id, action, wc_name, occurred_at "
        "FROM kiosk_punches_log WHERE id = %s",
        (log_id,),
    )
```

to:

```python
    rows = db.query(
        "SELECT id, person_odoo_id, action, wc_name, "
        "COALESCE(rounded_at, occurred_at) AS occurred_at "
        "FROM kiosk_punches_log WHERE id = %s",
        (log_id,),
    )
```

Note: `_retry_one` reads `r["occurred_at"]` — since the column alias is preserved, no code changes are needed inside `_retry_one`. The value passed to `odoo_client.clock_in(...)` / `clock_out(...)` is now the rounded time.

- [ ] **Step 2: Run sync-related tests**

```bash
pytest tests/ -k "sync or kiosk" -v
```

Expected: no regressions.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/kiosk_sync.py
git commit -m "$(cat <<'EOF'
feat(timeclock): sync rounded timestamp to Odoo hr.attendance

Both retry_unsynced_punches and sync_one_by_id SELECT
COALESCE(rounded_at, occurred_at) as occurred_at. Odoo
hr.attendance.check_in / check_out now receive the rounded value;
payroll runs against rounded times. Historical rows (pre-feature)
still sync their raw occurred_at via the COALESCE fallback.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Settings route — GET seeds current rounding values

**Files:**
- Modify: `src/zira_dashboard/routes/settings.py`

- [ ] **Step 1: Allow `"rounding"` as a settings section**

Find the `settings_page` function (line ~44). On line ~50, change:

```python
    if section not in ("work_centers", "schedule", "integrations", "roster_filter", "tvs", "kiosk"):
```

to:

```python
    if section not in ("work_centers", "schedule", "integrations", "roster_filter", "tvs", "kiosk", "rounding"):
```

- [ ] **Step 2: Load rounding settings and pass to template**

Still in `settings_page`, find the existing `sched = schedule_store.current()` line (around line 175 — just above the `schedule_ctx = { ... }` block). Immediately AFTER the `schedule_ctx = { ... }` block (and BEFORE the `# Skill list comes directly from the skills table` comment), insert:

```python
    from .. import rounding_store
    rounding_settings = rounding_store.current()
    rounding_ctx = {
        "in_before_min": rounding_settings.in_before_min,
        "in_after_min": rounding_settings.in_after_min,
        "out_before_min": rounding_settings.out_before_min,
        "out_after_min": rounding_settings.out_after_min,
    }
```

(The `from .. import rounding_store` line: `routes/settings.py` lives at `src/zira_dashboard/routes/settings.py`, so `..` is `src/zira_dashboard` — same depth as the existing `from .. import schedule_store, ...` at line 17. The local import inside the function avoids a circular-import risk during module load.)

Then in the `templates.TemplateResponse` call at the bottom of `settings_page` (lines 200-224), add `"rounding": rounding_ctx,` to the context dict. The block looks like:

```python
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "wc_rows": wc_rows,
            ...
            "kiosk_sync_status": kiosk_sync_status,
            "rounding": rounding_ctx,
        },
    )
```

- [ ] **Step 3: Verify settings page still renders**

If you have a local dev server, start it and visit `/settings?section=rounding`. The page should render (even if the Rounding section content isn't there yet — that's Task 9). Expect no 500.

Without a local server, just run any settings tests:

```bash
pytest tests/ -k "settings" -v
```

Expected: no regressions.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/settings.py
git commit -m "$(cat <<'EOF'
feat(timeclock): /settings page loads rounding values

Adds 'rounding' to the allowed sections, loads current
rounding_settings via rounding_store, passes a rounding context dict
to the template. UI to render and edit it comes next.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Settings route — POST saves and validates

**Files:**
- Modify: `src/zira_dashboard/routes/settings.py`
- Test: `tests/test_rounding_store.py` (extend) — *or* add a separate route test, but the store tests already cover the round-trip; a route-level test is overkill here.

- [ ] **Step 1: Add a POST route**

In `src/zira_dashboard/routes/settings.py`, after the `settings_save_schedule` function (around line 270), add:

```python
@router.post("/settings/rounding")
async def settings_save_rounding(request: Request):
    """Save the four rounding-window values. Each must be 0 <= v <= 60.
    Out-of-range or unparseable values fall back to 0 (no rounding on
    that side) rather than rejecting the whole submission."""
    from .. import rounding_store
    from ..rounding import RoundingSettings
    form = await request.form()

    def _clamp(raw) -> int:
        try:
            v = int(raw)
        except (TypeError, ValueError):
            return 0
        if v < 0:
            return 0
        if v > 60:
            return 60
        return v

    settings = RoundingSettings(
        in_before_min=_clamp(form.get("in_before_min")),
        in_after_min=_clamp(form.get("in_after_min")),
        out_before_min=_clamp(form.get("out_before_min")),
        out_after_min=_clamp(form.get("out_after_min")),
    )
    rounding_store.save(settings)
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/settings?saved=1&section=rounding", status_code=303)
```

(`JSONResponse` and `RedirectResponse` are already imported at the top of the file — no new imports needed.)

- [ ] **Step 2: Run settings + rounding tests**

```bash
pytest tests/ -k "settings or rounding" -v
```

Expected: no regressions; rounding store tests still pass.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/routes/settings.py
git commit -m "$(cat <<'EOF'
feat(timeclock): POST /settings/rounding saves window config

Clamps each of the four values to [0, 60]. Empty / unparseable inputs
fall back to 0 (disable that side) rather than rejecting the form.
Redirects back to /settings?section=rounding&saved=1 on success.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Settings page UI — Rounding section

**Files:**
- Modify: `src/zira_dashboard/templates/settings.html`

This step adds the visible UI: tab/nav entry, the section block with four number inputs, the Save button, and the StratusTime-style explanatory text.

- [ ] **Step 1: Find the existing settings tab navigation**

Open `src/zira_dashboard/templates/settings.html`. Locate the tab/nav structure. Look for the existing "Schedule" tab link — it'll be near the top of the file, something like a `<nav>` or `<ul>` element with anchors per section.

Grep:

```bash
grep -n "section=schedule" src/zira_dashboard/templates/settings.html
```

The result tells you where the nav lives. Add a "Rounding" tab link right after the "Schedule" tab link, following the same anchor pattern. Example pattern (adapt to the file's actual markup):

```html
<a href="/settings?section=rounding"
   class="settings-tab {% if active_section == 'rounding' %}active{% endif %}">
  Rounding
</a>
```

- [ ] **Step 2: Find the existing settings section blocks**

Still in `settings.html`, find an existing section block, e.g.:

```html
{% if active_section == 'schedule' %}
  <section> ... </section>
{% endif %}
```

After that block, add a new section for rounding:

```html
{% if active_section == 'rounding' %}
<section class="settings-section">
  <h2>Rounding</h2>
  <p class="note">
    Note: Some jurisdictions may limit or prohibit rounding.
  </p>

  <h3>Round To Schedule</h3>
  <p>
    When clocking in or out, the employee's time can be rounded to the
    plant shift if the entry falls within an acceptable
    &ldquo;window&rdquo;. Enter the values for the window:
  </p>

  <form method="post" action="/settings/rounding" class="rounding-form">
    <div class="rounding-grid">
      <div class="rounding-col">
        <h4>IN</h4>
        <label>
          Up to
          <input type="number" name="in_before_min"
                 min="0" max="60" value="{{ rounding.in_before_min }}">
          minute(s) before the schedule clock-in time.
        </label>
        <label>
          Up to
          <input type="number" name="in_after_min"
                 min="0" max="60" value="{{ rounding.in_after_min }}">
          minute(s) after the schedule clock-in time.
        </label>
      </div>
      <div class="rounding-col">
        <h4>OUT</h4>
        <label>
          Up to
          <input type="number" name="out_before_min"
                 min="0" max="60" value="{{ rounding.out_before_min }}">
          minute(s) before the schedule clock-out time.
        </label>
        <label>
          Up to
          <input type="number" name="out_after_min"
                 min="0" max="60" value="{{ rounding.out_after_min }}">
          minute(s) after the schedule clock-out time.
        </label>
      </div>
    </div>
    <p class="effective-note">
      Effective immediately &mdash; punches from this point forward use
      these values. Historical punches are unchanged.
    </p>
    {% if saved and active_section == 'rounding' %}
      <p class="saved-flash">Saved.</p>
    {% endif %}
    <button type="submit">Save Rounding</button>
  </form>
</section>
{% endif %}
```

- [ ] **Step 3: Add minimal CSS for the grid (inline or in an existing CSS file)**

If the file uses inline `<style>` blocks at the top, append these rules to the existing block:

```css
.rounding-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2rem;
  margin: 1rem 0;
}
.rounding-grid label {
  display: block;
  margin: 0.5rem 0;
}
.rounding-grid input[type="number"] {
  width: 4rem;
}
.effective-note {
  font-size: 0.9em;
  color: #555;
  margin: 1rem 0;
}
.saved-flash {
  color: green;
  font-weight: bold;
}
```

If the project has a separate stylesheet (`static/settings.css` or similar), put them there instead — check `templates/settings.html` for the `<link rel="stylesheet">` reference.

- [ ] **Step 4: Manual smoke test**

Start the dev server (per the project's existing pattern — `run_dashboard.bat` on Windows, or `uvicorn`-based command if available). Visit `/settings?section=rounding`. You should see:

- A Rounding tab in the nav
- The Rounding section with four number inputs, defaulting to 0 (ship-disabled)
- A Save Rounding button

Change the values to 20/0/0/20, click Save. Expect a redirect to `/settings?saved=1&section=rounding`, the page re-renders with the saved values, and the "Saved." flash appears.

If the dev server isn't trivially launchable on this host, defer the smoke test until Task 11.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/settings.html
git commit -m "$(cat <<'EOF'
feat(timeclock): settings page UI for rounding windows

Four number inputs (in_before, in_after, out_before, out_after),
modeled on the StratusTime 'Round To Schedule' screen. New 'Rounding'
tab alongside Schedule. POSTs to /settings/rounding (added in prior
commit).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: End-to-end manual verification

**Prerequisite:** local dev server, a kiosk session for Dale, an actual Odoo connection (or a mocked one), and a clock you can manipulate. If those aren't available, this task downgrades to "describe what should happen" and we trust the unit + integration tests.

- [ ] **Step 1: Enable rounding via the UI**

Open `/settings?section=rounding`. Set `in_before_min=20`, `in_after_min=0`, `out_before_min=0`, `out_after_min=20`. Click Save Rounding. Expect "Saved." flash.

- [ ] **Step 2: Clock in inside the IN window**

Within the same plant day, before scheduled shift_start (e.g., if shift_start = 7:00 AM, do this between 6:40 and 7:00 AM — or manipulate test data to simulate this).

Tap "Clock In" at the kiosk. Pick a WC. Confirm:

- The success page shows the rounded time (the scheduled shift_start, not the raw tap time)
- The `kiosk_punches_log` row has `occurred_at` = raw tap time and `rounded_at` = shift_start

```bash
psql $DATABASE_URL -c "SELECT id, action, occurred_at, rounded_at FROM kiosk_punches_log ORDER BY id DESC LIMIT 3"
```

- [ ] **Step 3: Confirm Odoo received the rounded time**

After the background sync runs (it fires immediately, plus a 60s sweep retry), check `hr.attendance` in Odoo for the new record. `check_in` should equal the rounded time, not the raw `occurred_at`.

- [ ] **Step 4: Clock in outside the window**

Tap "Clock In" at 9:00 AM (way past shift_start, outside the 0-min after window).

- The success page shows 9:00 AM (raw — no rounding applied)
- `rounded_at` in the DB row equals `occurred_at`
- Odoo `check_in` = 9:00 AM

- [ ] **Step 5: Clock out inside the OUT window**

At 3:35 PM (5 min after scheduled shift_end of 3:30 PM, within the 20-min OUT-after window), tap Clock Out.

- Success page shows 3:30 PM
- `rounded_at` = 3:30 PM in the DB
- Odoo `check_out` = 3:30 PM

- [ ] **Step 6: Confirm a mid-shift transfer is NOT rounded**

At any time within the day, tap Transfer to a different WC at 3:35 PM (within the OUT-after window). Confirm:

- The two new log rows (`transfer_out` + `transfer_in`) have `rounded_at` = `occurred_at` (both equal to the raw tap time — transfers never round)
- Odoo received the raw timestamp for both halves of the transfer

- [ ] **Step 7: Disable rounding and confirm it deactivates**

Back to `/settings?section=rounding`. Set all four values to 0. Save. Punch in at 6:50 AM again. Confirm:

- Success page shows 6:50 AM
- `rounded_at` = 6:50 AM (== occurred_at)
- Odoo `check_in` = 6:50 AM

---

## Task 11: CHANGELOG entry + final commit

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Read the top of CHANGELOG.md**

Confirm the format (today's date heading, time-based subentries). Per Dale's memory, every deploy needs a new `### TIME` entry under today's date.

```bash
head -30 CHANGELOG.md
```

- [ ] **Step 2: Add a new entry**

Insert a new `### HH:MM` block under today's date (`## 2026-05-27`). If today doesn't have a heading yet, add it. Example:

```markdown
## 2026-05-27

### HH:MM (use actual current time)

- **Timeclock: configurable punch rounding.** New "Rounding" section
  on `/settings` lets Dale set four window values (IN-before, IN-after,
  OUT-before, OUT-after, 0-60 min each), modeled on StratusTime's
  "Round To Schedule" UI. Clock-in / clock-out punches inside the
  window round to scheduled shift_start / shift_end before being
  written to Odoo `hr.attendance`. Raw and rounded timestamps both
  stored on `kiosk_punches_log` for audit (`rounded_at` column added).
  Transfers are never rounded. Ships disabled (all four values = 0).
- **Deferred:** late-report integration with rounded time. Today's
  `late_report.late_people_for_day` operates on a StratusTime-sourced
  attendance dict that doesn't know about kiosk punches. Plumbing
  rounded values through that path requires deciding whether to merge
  `kiosk_punches_log` into the attendance dict — a separate design
  conversation, to revisit closer to the StratusTime cutover.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
chore: changelog for timeclock punch rounding

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push (if Dale confirms)**

Per memory: small/obvious commits can be pushed autonomously; pause on major changes. This is a feature deploy with schema changes — confirm with Dale before pushing. If confirmed:

```bash
git push origin main
```

---

## Self-review checklist

After implementation, before declaring done:

- [ ] `pytest tests/ -k "rounding"` — all rounding tests pass.
- [ ] `pytest tests/` — no other tests regressed.
- [ ] `/settings?section=rounding` renders, save works, settings persist across page reload.
- [ ] Kiosk dashboard shows rounded clock-in time when rounding is enabled.
- [ ] `kiosk_punches_log` rows have both `occurred_at` and `rounded_at` populated for new punches.
- [ ] Odoo `hr.attendance.check_in` / `check_out` reflect the rounded time for new punches.
- [ ] Transfer punches show `rounded_at == occurred_at`.
- [ ] With all four settings = 0, behavior is identical to pre-feature (no surprises).
