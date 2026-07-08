# Machine Breakdown Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when a recycling machine stops producing, surface it in the Exception Inbox as a per-operator card, let the manager Transfer / Snooze / dismiss each idle operator, and automatically keep the dead-machine time off the operators' performance averages.

**Architecture:** A new `machine_breakdown` module owns detection (from Zira station data) and an incident store (`machine_breakdowns` + `breakdown_snoozes` tables). The dead-machine exclusion reuses the existing `wc_time_attributions` table with a new `source='breakdown'` — the mirror of the existing `'testing'` exclusion — and is subtracted from each operator's *expected* in the leaderboard/dashboard averages via a new `production_daily.excluded_minutes` column. The card is a bespoke breakdown row type wired into the standard inbox snapshot/reconcile/undo machinery.

**Tech Stack:** Python 3.12, FastAPI, psycopg2 + embedded Postgres for tests, Jinja2 templates, vanilla JS, pytest. Zira via `zira_probe.client`. Odoo via `odoo_client` (XML-RPC).

**Conventions (match the codebase):**
- Modules separate pure logic from I/O; DB access via `from . import db` (`db.query` / `db.execute` / `db.cursor` / `db.execute_values`), always lazily imported inside functions to avoid import cycles.
- Run the suite with: `ZIRA_API_KEY=test .venv/bin/python -m pytest <path> -q` (the embedded-Postgres fixtures make DB-gated tests run locally).
- Commit after each green step. Work happens on branch `machine-breakdown-inbox` (already created; the design spec is its first commit).

---

## File Structure

**Create:**
- `src/zira_dashboard/machine_breakdown.py` — detection (pure `detect` + I/O `current_rows`/`report_manual`/`run_detect_tick`), incident store, snooze, and the pure `excluded_minutes_for_windows` helper.
- `tests/test_machine_breakdown_detect.py` — pure detection + exclusion-math tests.
- `tests/test_machine_breakdown_store.py` — incident/snooze/exclusion DB tests.
- `tests/test_machine_breakdown_inbox.py` — snapshot section + reconcile + endpoint tests.

**Modify:**
- `src/zira_dashboard/_schema.py` — new tables + `production_daily.excluded_minutes` column.
- `src/zira_dashboard/wc_attributions.py` — `BREAKDOWN_SOURCE` + `add_breakdown`/`cap_breakdown`/`breakdown_windows_for_day`; exclude breakdown rows from crediting.
- `src/zira_dashboard/precompute.py` — carry `excluded_minutes` into `production_daily`.
- `src/zira_dashboard/production_history.py` — (via `precompute.daily_records_in_range`) surface `excluded_minutes`.
- `src/zira_dashboard/routes/leaderboards.py` — subtract `excluded_minutes` from expected in `averages_for_wc` / `averages_for_group`.
- `src/zira_dashboard/assignment_windows.py` — subtract breakdown windows from per-WC expected (recycling dashboard).
- `src/zira_dashboard/exception_inbox.py` — new `breakdown` snapshot section.
- `src/zira_dashboard/inbox_reconcile.py` — register the `breakdown` kind.
- `src/zira_dashboard/routes/exceptions.py` — breakdown endpoints + undo wiring.
- `src/zira_dashboard/templates/exceptions.html` — bespoke breakdown card render.
- `src/zira_dashboard/static/exceptions.js` — breakdown card handlers.
- `src/zira_dashboard/app.py` — register the detection + fold into the reconcile path.

---

## Phase 1 — Data model & exclusion primitives

### Task 1: Schema — incident tables + excluded_minutes column

**Files:**
- Modify: `src/zira_dashboard/_schema.py` (append after the `wc_time_attributions` block, ~line 196; add the `production_daily` column after ~line 432)
- Test: `tests/test_machine_breakdown_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_machine_breakdown_store.py
import pytest
from datetime import date, datetime, UTC

pytestmark = pytest.mark.usefixtures("db")  # embedded-postgres fixture (see conftest)


def test_schema_has_breakdown_tables_and_excluded_minutes():
    from zira_dashboard import db
    # machine_breakdowns + breakdown_snoozes exist and accept a row
    db.execute(
        "INSERT INTO machine_breakdowns (wc_name, day, detected_stop_utc, source) "
        "VALUES (%s, %s, %s, %s)",
        ("Dismantler 2", date(2026, 7, 7), datetime(2026, 7, 7, 18, 2, tzinfo=UTC), "auto"),
    )
    rows = db.query("SELECT wc_name, resolution, resolved_at FROM machine_breakdowns")
    assert rows[0]["wc_name"] == "Dismantler 2"
    assert rows[0]["resolution"] is None
    assert rows[0]["resolved_at"] is None
    # production_daily has excluded_minutes defaulting to 0
    cols = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'production_daily' AND column_name = 'excluded_minutes'"
    )
    assert cols, "production_daily.excluded_minutes missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_store.py::test_schema_has_breakdown_tables_and_excluded_minutes -q`
Expected: FAIL — `relation "machine_breakdowns" does not exist`.

- [ ] **Step 3: Add the schema**

Append to the CREATE TABLE block in `src/zira_dashboard/_schema.py` (right after the `wc_time_attributions` indexes/ALTER at line 196):

```sql
-- machine_breakdowns: one incident per broken recycling machine per day.
-- A row with resolved_at IS NULL is "open" (a live inbox card). resolution
-- records why it closed: 'recovered' (machine produced again), 'handled'
-- (all operators left the machine), or 'dismissed' ("Not a breakdown").
CREATE TABLE IF NOT EXISTS machine_breakdowns (
  id                BIGSERIAL PRIMARY KEY,
  wc_name           TEXT NOT NULL,
  day               DATE NOT NULL,
  detected_stop_utc TIMESTAMPTZ NOT NULL,
  source            TEXT NOT NULL DEFAULT 'auto',   -- 'auto' | 'manual'
  resolution        TEXT,                            -- 'recovered'|'handled'|'dismissed'|NULL
  resume_utc        TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS machine_breakdowns_day_idx ON machine_breakdowns(day);
-- At most one OPEN incident per machine per day.
CREATE UNIQUE INDEX IF NOT EXISTS machine_breakdowns_open_uniq
  ON machine_breakdowns(wc_name, day) WHERE resolved_at IS NULL;

-- breakdown_snoozes: per-operator "decide later" deferral on a breakdown card.
CREATE TABLE IF NOT EXISTS breakdown_snoozes (
  breakdown_id   BIGINT NOT NULL,
  person_name    TEXT NOT NULL,
  snooze_until   TIMESTAMPTZ NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (breakdown_id, person_name)
);
```

Add after the `production_daily` indexes (line 432):

```sql
-- excluded_minutes: per-(day, emp, wc) productive minutes to REMOVE from the
-- expected target — populated from source='breakdown' attributions so a dead
-- machine doesn't drag an operator's average down. Default 0 = no exclusion,
-- so every historical row is unaffected.
ALTER TABLE production_daily ADD COLUMN IF NOT EXISTS excluded_minutes NUMERIC NOT NULL DEFAULT 0;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/_schema.py tests/test_machine_breakdown_store.py
git commit -m "feat(breakdown): schema for machine_breakdowns, breakdown_snoozes, excluded_minutes"
```

---

### Task 2: `wc_attributions` breakdown helpers

**Files:**
- Modify: `src/zira_dashboard/wc_attributions.py`
- Test: `tests/test_machine_breakdown_store.py`

- [ ] **Step 1: Write the failing test** (append to the file)

```python
def test_breakdown_attribution_add_cap_and_windows():
    from zira_dashboard import wc_attributions as wa
    d = date(2026, 7, 7)
    stop = datetime(2026, 7, 7, 18, 2, tzinfo=UTC)
    rid = wa.add_breakdown(d, "Dismantler 2", "Juan Perez", stop)
    assert rid > 0
    # open (uncapped) window shows up
    wins = wa.breakdown_windows_for_day(d)
    assert wins["Dismantler 2"]["Juan Perez"] == [(stop, None)]
    # breakdown rows are NOT credited operators
    assert "Dismantler 2" not in wa.people_by_wc(d)
    # cap it
    cap = datetime(2026, 7, 7, 18, 30, tzinfo=UTC)
    wa.cap_breakdown(d, "Dismantler 2", "Juan Perez", cap)
    wins = wa.breakdown_windows_for_day(d)
    assert wins["Dismantler 2"]["Juan Perez"] == [(stop, cap)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_store.py::test_breakdown_attribution_add_cap_and_windows -q`
Expected: FAIL — `module 'zira_dashboard.wc_attributions' has no attribute 'add_breakdown'`.

- [ ] **Step 3: Implement**

In `src/zira_dashboard/wc_attributions.py`, add the constant next to `TESTING_SOURCE` (after line 23):

```python
BREAKDOWN_SOURCE = "breakdown"
"""``wc_time_attributions.source`` value marking a machine-breakdown exclusion
window [stop, cap] for one operator. Like ``testing`` it credits no one; unlike
testing it removes EXPECTED minutes (via production_daily.excluded_minutes)
rather than units."""

_NON_CREDIT_SOURCES = {TESTING_SOURCE, BREAKDOWN_SOURCE}
```

Change the filter in `people_by_wc` (line 69) and `creditable_for_day` (line 96) from the single-source check to `_NON_CREDIT_SOURCES`:

```python
# people_by_wc, line 69:
        if r.get("source") in _NON_CREDIT_SOURCES:
            continue
```
```python
# creditable_for_day, line 96:
    return [r for r in for_day(day) if r.get("source") not in _NON_CREDIT_SOURCES]
```

Add these functions after `delete` (line 101):

```python
def add_breakdown(day: date, wc_name: str, person_name: str,
                  start_utc: datetime) -> int:
    """Open a breakdown exclusion window (end_utc NULL = still down). Returns id."""
    return add(day, wc_name, person_name, start_utc, end_utc=None,
               source=BREAKDOWN_SOURCE)


def cap_breakdown(day: date, wc_name: str, person_name: str,
                  end_utc: datetime) -> None:
    """Close the OPEN breakdown window for this (day, wc, person) at end_utc.
    No-op if none open (idempotent — the operator already left)."""
    from . import db
    db.execute(
        "UPDATE wc_time_attributions SET end_utc = %s "
        "WHERE day = %s AND wc_name = %s AND person_name = %s "
        "AND source = %s AND end_utc IS NULL",
        (end_utc, day, wc_name, person_name, BREAKDOWN_SOURCE),
    )


def uncap_breakdown(day: date, wc_name: str, person_name: str) -> None:
    """Re-open the most recent capped breakdown window (undo of cap_breakdown)."""
    from . import db
    db.execute(
        "UPDATE wc_time_attributions SET end_utc = NULL "
        "WHERE id = (SELECT id FROM wc_time_attributions "
        "  WHERE day = %s AND wc_name = %s AND person_name = %s AND source = %s "
        "  ORDER BY end_utc DESC NULLS FIRST, id DESC LIMIT 1)",
        (day, wc_name, person_name, BREAKDOWN_SOURCE),
    )


def delete_breakdowns_for(day: date, wc_name: str) -> None:
    """Remove all breakdown windows for a machine/day (dismiss / undo detection)."""
    from . import db
    db.execute(
        "DELETE FROM wc_time_attributions "
        "WHERE day = %s AND wc_name = %s AND source = %s",
        (day, wc_name, BREAKDOWN_SOURCE),
    )


def breakdown_windows_for_day(day: date, rows: list[dict] | None = None):
    """``{wc_name: {person: [(start_utc, end_utc), ...]}}`` for breakdown rows.
    Swallows DB errors like ``people_by_wc``; ``end_utc`` may be None (open)."""
    if rows is None:
        try:
            rows = for_day(day)
        except Exception:
            return {}
    out: dict[str, dict[str, list[tuple]]] = {}
    for r in rows:
        if r.get("source") != BREAKDOWN_SOURCE:
            continue
        out.setdefault(r["wc_name"], {}).setdefault(
            r["person_name"], []).append((r["start_utc"], r["end_utc"]))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/wc_attributions.py tests/test_machine_breakdown_store.py
git commit -m "feat(breakdown): wc_time_attributions source='breakdown' helpers"
```

---

### Task 3: Pure `excluded_minutes_for_windows` helper

**Files:**
- Create: `src/zira_dashboard/machine_breakdown.py`
- Test: `tests/test_machine_breakdown_detect.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_machine_breakdown_detect.py
from datetime import date, datetime, UTC


def test_excluded_minutes_sums_productive_window_overlap():
    from zira_dashboard import machine_breakdown as mb
    d = date(2026, 7, 7)
    stop = datetime(2026, 7, 7, 18, 2, tzinfo=UTC)
    cap = datetime(2026, 7, 7, 18, 32, tzinfo=UTC)  # 30 min later
    now = datetime(2026, 7, 7, 19, 0, tzinfo=UTC)

    # Inject a fake "productive minutes in window" = whole span (no breaks).
    def fake_pmw(day, s, e):
        return int((e - s).total_seconds() // 60)

    # Capped window -> 30 min excluded.
    assert mb.excluded_minutes_for_windows(
        d, [(stop, cap)], now, productive_minutes_in_window=fake_pmw) == 30
    # Open window (None end) -> capped at `now` = 58 min.
    assert mb.excluded_minutes_for_windows(
        d, [(stop, None)], now, productive_minutes_in_window=fake_pmw) == 58
    # No windows -> 0.
    assert mb.excluded_minutes_for_windows(
        d, [], now, productive_minutes_in_window=fake_pmw) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_detect.py::test_excluded_minutes_sums_productive_window_overlap -q`
Expected: FAIL — `No module named 'zira_dashboard.machine_breakdown'`.

- [ ] **Step 3: Create the module with the pure helper**

```python
# src/zira_dashboard/machine_breakdown.py
"""Machine-breakdown detection + incident handling for the Exception Inbox.

A recycling machine that stops producing raises a per-operator inbox card. Each
operator's dead-machine time is excluded from their expected via source='breakdown'
attributions (see wc_attributions). Pure detection logic is separated from I/O so
it is unit-testable; the warmer owns the Zira/DB reads.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, UTC

_log = logging.getLogger(__name__)


def excluded_minutes_for_windows(
    day: date,
    windows: list[tuple],
    now: datetime,
    productive_minutes_in_window=None,
) -> int:
    """Sum productive minutes inside each breakdown window on ``day``.

    Each window is ``(start_utc, end_utc)``; an open window (``end_utc`` None)
    caps at ``now``. ``productive_minutes_in_window`` is injected for tests;
    defaults to ``shift_config.productive_minutes_in_window``.
    """
    if productive_minutes_in_window is None:
        from .shift_config import productive_minutes_in_window as pmw
        productive_minutes_in_window = pmw
    total = 0
    for start, end in windows:
        stop = end or now
        if stop <= start:
            continue
        total += int(productive_minutes_in_window(day, start, stop))
    return total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_detect.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/machine_breakdown.py tests/test_machine_breakdown_detect.py
git commit -m "feat(breakdown): pure excluded_minutes_for_windows helper"
```

---

## Phase 2 — Thread exclusion into averages

### Task 4: Carry `excluded_minutes` through precompute → production_daily

**Files:**
- Modify: `src/zira_dashboard/precompute.py` (`flatten_attribution` ~25-58, `upsert_production_daily` ~61-93, `precompute_day` ~96-106, `daily_records_in_range` ~161-193)
- Test: `tests/test_machine_breakdown_store.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_production_daily_carries_excluded_minutes():
    from zira_dashboard import precompute
    d = date(2026, 7, 7)
    rows = [{
        "day": d, "emp_id": "42", "name": "Juan Perez", "wc_name": "Dismantler 2",
        "units": 100.0, "downtime": 5.0, "hours": 8.0, "days_worked": 1.0,
        "excluded_minutes": 30.0,
    }]
    precompute.upsert_production_daily(rows)
    recs = precompute.daily_records_in_range(d, d)
    assert recs[0]["excluded_minutes"] == 30.0
    # flatten defaults excluded_minutes to 0 when not attached
    flat = precompute.flatten_attribution(
        d, {"Ann": {"Repair 1": {"units": 10, "downtime": 0, "hours": 8, "days_worked": 1}}},
        {"Ann": "7"})
    assert flat[0]["excluded_minutes"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_store.py::test_production_daily_carries_excluded_minutes -q`
Expected: FAIL — `KeyError: 'excluded_minutes'` (or the column isn't selected).

- [ ] **Step 3: Implement**

In `flatten_attribution` (precompute.py), add the field to each appended row (after `"days_worked"`, line 56):

```python
                "days_worked": float(totals.get("days_worked") or 0),
                "excluded_minutes": float(totals.get("excluded_minutes") or 0),
```

In `upsert_production_daily`, extend the INSERT columns, the `ON CONFLICT` set, the row tuple, and the `template`:

```python
    sql = """
        INSERT INTO production_daily (
            day, emp_id, name, wc_name,
            units, downtime, hours, days_worked, excluded_minutes, computed_at
        ) VALUES %s
        ON CONFLICT (day, emp_id, wc_name) DO UPDATE SET
            name             = EXCLUDED.name,
            units            = EXCLUDED.units,
            downtime         = EXCLUDED.downtime,
            hours            = EXCLUDED.hours,
            days_worked      = EXCLUDED.days_worked,
            excluded_minutes = EXCLUDED.excluded_minutes,
            computed_at      = now()
    """
    with db.cursor() as cur:
        db.execute_values(cur, sql, [
            (r["day"], r["emp_id"], r["name"], r["wc_name"],
             r["units"], r["downtime"], r["hours"], r["days_worked"],
             float(r.get("excluded_minutes") or 0))
            for r in rows
        ], template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, now())")
```

In `daily_records_in_range` (precompute.py ~161-193), add `excluded_minutes` to the SELECT column list and to the returned dict:

```python
        SELECT day, name, wc_name,
               units, downtime, hours, excluded_minutes
        FROM production_daily
```
```python
            "hours": float(r["hours"]),
            "excluded_minutes": float(r["excluded_minutes"]),
```

In `precompute_day`, attach exclusion minutes to the flattened rows before upsert:

```python
def precompute_day(day: date, client) -> dict:
    from . import production_history, attendance, wc_attributions, machine_breakdown
    attribution = production_history.attribution_for(day, client)
    name_to_emp_id = attendance.name_to_person_id()
    rows = flatten_attribution(day, attribution, name_to_emp_id)
    _attach_excluded_minutes(day, rows, wc_attributions, machine_breakdown)
    written = upsert_production_daily(rows)
    return {"day": day.isoformat(), "rows_written": written}


def _attach_excluded_minutes(day, rows, wc_attributions, machine_breakdown) -> None:
    """Set row['excluded_minutes'] from source='breakdown' windows for that
    (person, wc). Open windows cap at now (live-day partial excludes)."""
    from datetime import datetime, UTC
    bd = wc_attributions.breakdown_windows_for_day(day)
    if not bd:
        return
    now = datetime.now(UTC)
    for r in rows:
        wins = bd.get(r["wc_name"], {}).get(r["name"], [])
        if wins:
            r["excluded_minutes"] = machine_breakdown.excluded_minutes_for_windows(
                day, wins, now)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/precompute.py tests/test_machine_breakdown_store.py
git commit -m "feat(breakdown): carry excluded_minutes into production_daily"
```

---

### Task 5: Subtract `excluded_minutes` in leaderboard averages

**Files:**
- Modify: `src/zira_dashboard/routes/leaderboards.py` (`averages_for_wc` line 63-64, `averages_for_group` line 118-120)
- Test: `tests/test_machine_breakdown_detect.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_averages_subtract_excluded_minutes():
    from zira_dashboard.routes.leaderboards import averages_for_wc
    d = date(2026, 7, 7)
    # 480 productive min/day, target 12.5/hr -> full expected = 100 units.
    pmf = lambda day: 480
    base = {"day": d, "person": "Juan Perez", "wc": "Dismantler 2", "downtime": 0}

    # No exclusion: 60 units / 100 expected = 60%.
    no_excl = averages_for_wc(
        [{**base, "units": 60, "excluded_minutes": 0}], 12.5, pmf, "pct")
    assert round(no_excl[0]["avg_pct"], 4) == 0.6

    # 240 min excluded -> expected halves to 50 -> 60/50 = 120%.
    excl = averages_for_wc(
        [{**base, "units": 60, "excluded_minutes": 240}], 12.5, pmf, "pct")
    assert round(excl[0]["avg_pct"], 4) == 1.2

    # Missing key behaves like 0 (historical rows).
    legacy = averages_for_wc([{**base, "units": 60}], 12.5, pmf, "pct")
    assert round(legacy[0]["avg_pct"], 4) == 0.6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_detect.py::test_averages_subtract_excluded_minutes -q`
Expected: FAIL — excluded case returns 0.6, not 1.2.

- [ ] **Step 3: Implement**

In `averages_for_wc`, replace lines 63-64:

```python
            prod_min = productive_minutes_for(r["day"]) - float(r.get("excluded_minutes") or 0)
            prod_hr = max(0.0, prod_min) / 60.0
            expected = target_per_hour * prod_hr
```

In `averages_for_group`, replace lines 118-120:

```python
            prod_min = productive_minutes_for(r["day"]) - float(r.get("excluded_minutes") or 0)
            prod_hr = max(0.0, prod_min) / 60.0
            target = target_per_hour_by_wc.get(r["wc"], 0.0)
            expected = target * prod_hr
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_detect.py -q`
Expected: PASS.

- [ ] **Step 5: Run the existing leaderboard suite (regression guard)**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/ -q -k "leaderboard or averages"`
Expected: PASS — historical rows (no `excluded_minutes`) are unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/leaderboards.py tests/test_machine_breakdown_detect.py
git commit -m "feat(breakdown): subtract excluded_minutes from leaderboard expected"
```

---

### Task 6: Subtract breakdown windows from recycling per-WC expected

**Files:**
- Modify: `src/zira_dashboard/assignment_windows.py` (`expected_by_wc`, ~line 100-122)
- Test: `tests/test_machine_breakdown_detect.py`

- [ ] **Step 1: Read `expected_by_wc` and write a test matching its real signature**

First read `src/zira_dashboard/assignment_windows.py:100-132` to confirm the exact parameters (it takes resolved segments + a `productive_minutes_in_window`-style helper and returns `{wc: expected_units}`). Then write a test that passes one segment on "Dismantler 2" plus a breakdown window covering half of it and asserts the expected halves. Model the arithmetic on the leaderboard test above. Use the real parameter names from the file.

```python
def test_recycling_expected_honors_breakdown_window():
    # Fill in with expected_by_wc's real signature after reading the file.
    # Assert: given a full-shift segment on 'Dismantler 2' and a breakdown
    # window covering half the productive minutes, the WC's expected units
    # is halved vs. no breakdown window.
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_detect.py::test_recycling_expected_honors_breakdown_window -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `assignment_windows.expected_by_wc`, fetch `wc_attributions.breakdown_windows_for_day(day)` and, when computing each person's productive minutes on a WC, subtract the productive minutes that fall inside that person's breakdown windows (reuse `machine_breakdown.excluded_minutes_for_windows`). Keep the change additive: no breakdown windows → identical output. (Exact edit depends on the function body read in Step 1; mirror the leaderboard subtraction.)

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_detect.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/assignment_windows.py tests/test_machine_breakdown_detect.py
git commit -m "feat(breakdown): recycling per-WC expected honors breakdown windows"
```

---

## Phase 3 — Detection & incident store

### Task 7: Pure `detect`

**Files:**
- Modify: `src/zira_dashboard/machine_breakdown.py`
- Test: `tests/test_machine_breakdown_detect.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_detect_flags_no_output_with_operators():
    from zira_dashboard import machine_breakdown as mb
    from datetime import timedelta
    now = datetime(2026, 7, 7, 19, 0, tzinfo=UTC)
    shift = (datetime(2026, 7, 7, 13, 0, tzinfo=UTC),
             datetime(2026, 7, 8, 1, 30, tzinfo=UTC))  # 7:00a-7:30p CT-ish

    # Last unit 25 min ago -> no output >= 15 min threshold.
    down = mb.StationSignal(wc_name="Dismantler 2",
                            last_output_utc=now - timedelta(minutes=25),
                            producing=False)
    # Still producing recently -> not flagged.
    up = mb.StationSignal(wc_name="Repair 1",
                          last_output_utc=now - timedelta(minutes=2),
                          producing=True)

    incidents = mb.detect(
        signals=[down, up],
        operators_by_wc={"Dismantler 2": ["Juan Perez"], "Repair 1": ["Ana"]},
        now=now, shift_window=shift,
        open_wcs=set(), dismissed_wcs=set(), testing_wcs=set(),
        threshold_minutes=15,
    )
    assert [i.wc_name for i in incidents] == ["Dismantler 2"]
    assert incidents[0].detected_stop_utc == down.last_output_utc


def test_detect_skips_no_operators_open_dismissed_testing_and_off_shift():
    from zira_dashboard import machine_breakdown as mb
    from datetime import timedelta
    now = datetime(2026, 7, 7, 19, 0, tzinfo=UTC)
    shift = (datetime(2026, 7, 7, 13, 0, tzinfo=UTC),
             datetime(2026, 7, 8, 1, 30, tzinfo=UTC))
    sig = mb.StationSignal("Dismantler 2", now - timedelta(minutes=25), False)
    common = dict(signals=[sig], now=now, shift_window=shift, threshold_minutes=15)

    # No operators -> skipped.
    assert mb.detect(operators_by_wc={"Dismantler 2": []},
                     open_wcs=set(), dismissed_wcs=set(), testing_wcs=set(), **common) == []
    ops = {"Dismantler 2": ["Juan"]}
    # Already open -> skipped.
    assert mb.detect(operators_by_wc=ops, open_wcs={"Dismantler 2"},
                     dismissed_wcs=set(), testing_wcs=set(), **common) == []
    # Dismissed -> skipped.
    assert mb.detect(operators_by_wc=ops, open_wcs=set(),
                     dismissed_wcs={"Dismantler 2"}, testing_wcs=set(), **common) == []
    # In testing -> skipped.
    assert mb.detect(operators_by_wc=ops, open_wcs=set(),
                     dismissed_wcs=set(), testing_wcs={"Dismantler 2"}, **common) == []
    # Off shift -> skipped.
    off = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    assert mb.detect(operators_by_wc=ops, open_wcs=set(), dismissed_wcs=set(),
                     testing_wcs=set(), signals=[sig], now=off, shift_window=shift,
                     threshold_minutes=15) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_detect.py -q -k detect_`
Expected: FAIL — `StationSignal` / `detect` not defined.

- [ ] **Step 3: Implement** (add to `machine_breakdown.py`)

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class StationSignal:
    wc_name: str
    last_output_utc: datetime | None  # end of last active interval, or None
    producing: bool                   # produced within the threshold recently


@dataclass(frozen=True)
class BreakdownIncident:
    wc_name: str
    detected_stop_utc: datetime


def detect(
    *,
    signals: list["StationSignal"],
    operators_by_wc: dict[str, list[str]],
    now: datetime,
    shift_window: tuple[datetime, datetime],
    open_wcs: set[str],
    dismissed_wcs: set[str],
    testing_wcs: set[str],
    threshold_minutes: int,
) -> list["BreakdownIncident"]:
    """Pure: which stations look broken this tick.

    A station qualifies when: within shift hours; has ≥1 operator; not already
    open; not dismissed-and-not-recovered; not in a testing window; and its last
    output was ≥ ``threshold_minutes`` ago (or never produced this shift while
    someone's on it). Skips a station still producing.
    """
    from datetime import timedelta
    shift_start, shift_end = shift_window
    if not (shift_start <= now <= shift_end):
        return []
    cutoff = now - timedelta(minutes=threshold_minutes)
    out: list[BreakdownIncident] = []
    for s in signals:
        wc = s.wc_name
        if not operators_by_wc.get(wc):
            continue
        if wc in open_wcs or wc in dismissed_wcs or wc in testing_wcs:
            continue
        if s.producing:
            continue
        if s.last_output_utc is None or s.last_output_utc <= cutoff:
            stop = s.last_output_utc or shift_start
            out.append(BreakdownIncident(wc_name=wc, detected_stop_utc=stop))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_detect.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/machine_breakdown.py tests/test_machine_breakdown_detect.py
git commit -m "feat(breakdown): pure station-breakdown detection"
```

---

### Task 8: Incident store + snooze (I/O)

**Files:**
- Modify: `src/zira_dashboard/machine_breakdown.py`
- Test: `tests/test_machine_breakdown_store.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_incident_open_resolve_dismiss_and_snooze():
    from zira_dashboard import machine_breakdown as mb
    from zira_dashboard import wc_attributions as wa
    d = date(2026, 7, 7)
    stop = datetime(2026, 7, 7, 18, 2, tzinfo=UTC)

    # open_incident writes the row + a breakdown window per operator
    inc_id = mb.open_incident("Dismantler 2", d, stop, ["Juan Perez", "Ben Cruz"],
                              source="auto")
    assert inc_id > 0
    assert mb.open_wc_names(d) == {"Dismantler 2"}
    assert set(wa.breakdown_windows_for_day(d).get("Dismantler 2", {})) == {"Juan Perez", "Ben Cruz"}

    # snooze hides Juan
    until = datetime(2026, 7, 7, 18, 20, tzinfo=UTC)
    mb.snooze(inc_id, "Juan Perez", until)
    assert mb.snoozed_people(inc_id, as_of=datetime(2026, 7, 7, 18, 10, tzinfo=UTC)) == {"Juan Perez"}
    assert mb.snoozed_people(inc_id, as_of=datetime(2026, 7, 7, 18, 30, tzinfo=UTC)) == set()

    # dismiss resolves + deletes exclusion windows
    mb.dismiss_incident(inc_id)
    assert mb.open_wc_names(d) == set()
    assert wa.breakdown_windows_for_day(d) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_store.py::test_incident_open_resolve_dismiss_and_snooze -q`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement** (add to `machine_breakdown.py`)

```python
def open_incident(wc_name: str, day: date, detected_stop_utc: datetime,
                  operators: list[str], *, source: str = "auto") -> int:
    """Insert an open incident + one open breakdown window per operator.
    Idempotent per (wc, day) via the partial unique index; returns the
    existing open incident's id on conflict."""
    from . import db, wc_attributions
    rows = db.query(
        "INSERT INTO machine_breakdowns (wc_name, day, detected_stop_utc, source) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (wc_name, day) WHERE resolved_at IS NULL DO NOTHING "
        "RETURNING id",
        (wc_name, day, detected_stop_utc, source),
    )
    if rows:
        inc_id = rows[0]["id"]
    else:
        existing = db.query(
            "SELECT id FROM machine_breakdowns "
            "WHERE wc_name = %s AND day = %s AND resolved_at IS NULL",
            (wc_name, day),
        )
        inc_id = existing[0]["id"] if existing else 0
    for person in operators:
        wc_attributions.add_breakdown(day, wc_name, person, detected_stop_utc)
    return inc_id


def get_incident(inc_id: int) -> dict | None:
    from . import db
    rows = db.query(
        "SELECT id, wc_name, day, detected_stop_utc, source, resolution, "
        "resume_utc, resolved_at FROM machine_breakdowns WHERE id = %s",
        (inc_id,),
    )
    return rows[0] if rows else None


def open_incidents(day: date) -> list[dict]:
    from . import db
    return db.query(
        "SELECT id, wc_name, day, detected_stop_utc, source FROM machine_breakdowns "
        "WHERE day = %s AND resolved_at IS NULL ORDER BY detected_stop_utc",
        (day,),
    )


def open_wc_names(day: date) -> set[str]:
    return {r["wc_name"] for r in open_incidents(day)}


def dismissed_wc_names(day: date) -> set[str]:
    """Machines dismissed today (suppress re-detection until they produce again;
    the caller drops any that have produced since — see current_rows)."""
    from . import db
    return {r["wc_name"] for r in db.query(
        "SELECT DISTINCT wc_name FROM machine_breakdowns "
        "WHERE day = %s AND resolution = 'dismissed'", (day,))}


def resolve_incident(inc_id: int, resolution: str, resume_utc: datetime | None = None) -> None:
    """Close an incident. Caps each operator's open breakdown window at
    resume_utc (recovered) or leaves the cap the actions already applied."""
    from . import db
    db.execute(
        "UPDATE machine_breakdowns SET resolution = %s, resume_utc = %s, "
        "resolved_at = now() WHERE id = %s AND resolved_at IS NULL",
        (resolution, resume_utc, inc_id),
    )


def dismiss_incident(inc_id: int) -> None:
    """'Not a breakdown': resolve + delete this machine/day's exclusion windows."""
    from . import wc_attributions
    inc = get_incident(inc_id)
    if inc is None:
        return
    wc_attributions.delete_breakdowns_for(inc["day"], inc["wc_name"])
    resolve_incident(inc_id, "dismissed")


def reopen_incident(inc_id: int) -> None:
    """Undo of dismiss: clear resolution and re-create exclusion windows for the
    operators recorded when it was open (best-effort — re-detect fills gaps)."""
    from . import db
    db.execute(
        "UPDATE machine_breakdowns SET resolution = NULL, resolved_at = NULL, "
        "resume_utc = NULL WHERE id = %s",
        (inc_id,),
    )


def snooze(inc_id: int, person_name: str, until_utc: datetime) -> None:
    from . import db
    db.execute(
        "INSERT INTO breakdown_snoozes (breakdown_id, person_name, snooze_until) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (breakdown_id, person_name) DO UPDATE SET "
        "snooze_until = EXCLUDED.snooze_until, created_at = now()",
        (inc_id, person_name, until_utc),
    )


def snoozed_people(inc_id: int, as_of: datetime) -> set[str]:
    from . import db
    return {r["person_name"] for r in db.query(
        "SELECT person_name FROM breakdown_snoozes "
        "WHERE breakdown_id = %s AND snooze_until > %s", (inc_id, as_of))}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/machine_breakdown.py tests/test_machine_breakdown_store.py
git commit -m "feat(breakdown): incident store (open/resolve/dismiss/snooze)"
```

---

### Task 9: `current_rows` + `run_detect_tick` + `report_manual` (I/O glue)

**Files:**
- Modify: `src/zira_dashboard/machine_breakdown.py`
- Test: `tests/test_machine_breakdown_inbox.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_machine_breakdown_inbox.py
import pytest
from datetime import date, datetime, timedelta, UTC

pytestmark = pytest.mark.usefixtures("db")


def test_current_rows_shape_and_snooze_filter(monkeypatch):
    from zira_dashboard import machine_breakdown as mb
    d = date(2026, 7, 7)
    stop = datetime(2026, 7, 7, 18, 2, tzinfo=UTC)
    inc_id = mb.open_incident("Dismantler 2", d, stop, ["Juan Perez", "Ben Cruz"])

    # Freeze "who's on the machine now" so the test needs no Zira/Odoo.
    monkeypatch.setattr(mb, "_operators_on",
                        lambda wc, day, now: ["Juan Perez", "Ben Cruz"])
    monkeypatch.setattr(mb, "plant_today", lambda: d)

    rows = mb.current_rows()
    assert rows[0]["wc_name"] == "Dismantler 2"
    assert rows[0]["breakdown_id"] == inc_id
    assert {op["name"] for op in rows[0]["operators"]} == {"Juan Perez", "Ben Cruz"}

    # Snooze Ben -> dropped from the operators list.
    mb.snooze(inc_id, "Ben Cruz", datetime.now(UTC) + timedelta(minutes=15))
    rows = mb.current_rows()
    assert {op["name"] for op in rows[0]["operators"]} == {"Juan Perez"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_inbox.py::test_current_rows_shape_and_snooze_filter -q`
Expected: FAIL — `current_rows` / `_operators_on` not defined.

- [ ] **Step 3: Implement** (add to `machine_breakdown.py`)

```python
from .plant_day import today as plant_today  # module-level so tests can monkeypatch

_ENABLED = os.getenv("MACHINE_BREAKDOWN_ENABLED", "1") not in ("0", "false", "False")
_THRESHOLD_MINUTES = int(os.getenv("BREAKDOWN_NO_OUTPUT_MINUTES", "15"))


def _operators_on(wc_name: str, day: date, now: datetime) -> list[str]:
    """Distinct people currently resolved onto ``wc_name`` and still on it.
    Mirrors routes/departments._recycling_day_data's resolution: build segments
    with assignment_windows.resolve_segments(...) and keep person_names whose
    open segment is on this WC. (See routes/departments.py for the full arg set.)"""
    from . import assignment_windows
    try:
        segments = assignment_windows.resolve_segments(day)  # use the same args the dashboard uses
    except Exception:
        return []
    out: list[str] = []
    for seg in segments:
        if seg.wc_name != wc_name:
            continue
        if seg.end_utc is not None and seg.end_utc <= now:
            continue
        if seg.person_name not in out:
            out.append(seg.person_name)
    return out


def _station_signals(day: date, now: datetime) -> list["StationSignal"]:
    """Build a StationSignal per recycling station from the cached Zira day."""
    from . import leaderboard as _lb
    from .stations import recycling_stations
    from ._zira import client as _zira_client  # or the app's client accessor
    cutoff = now - __import__("datetime").timedelta(minutes=_THRESHOLD_MINUTES)
    results = _lb.cached_leaderboard(_zira_client(), recycling_stations(), day, now_utc=now)
    signals: list[StationSignal] = []
    for r in results:
        ais = r.active_intervals
        last = max((e for _, e in ais), default=None) if ais else None
        producing = last is not None and last > cutoff
        signals.append(StationSignal(r.station.name, last, producing))
    return signals


def run_detect_tick() -> None:
    """One detection pass (called from the warmer). No-op when disabled."""
    if not _ENABLED:
        return
    from datetime import datetime, UTC
    from . import shift_config, wc_attributions
    day = plant_today()
    now = datetime.now(UTC)
    shift_window = (shift_config.shift_start_utc(day), shift_config.shift_end_utc(day))  # confirm helper names
    signals = _station_signals(day, now)
    operators_by_wc = {s.wc_name: _operators_on(s.wc_name, day, now) for s in signals}

    # Dismissed machines that have produced again are eligible to re-detect.
    dismissed = dismissed_wc_names(day)
    still_dismissed = {s.wc_name for s in signals
                       if s.wc_name in dismissed and not s.producing}
    testing_wcs = set(wc_attributions.testing_windows_for_day(day).keys())

    incidents = detect(
        signals=signals, operators_by_wc=operators_by_wc, now=now,
        shift_window=shift_window, open_wcs=open_wc_names(day),
        dismissed_wcs=still_dismissed, testing_wcs=testing_wcs,
        threshold_minutes=_THRESHOLD_MINUTES,
    )
    for inc in incidents:
        open_incident(inc.wc_name, day, inc.detected_stop_utc,
                      operators_by_wc.get(inc.wc_name, []), source="auto")

    # Auto-resolve: machine producing again -> cap windows at resume + close.
    for row in open_incidents(day):
        sig = next((s for s in signals if s.wc_name == row["wc_name"]), None)
        if sig and sig.producing:
            resume = sig.last_output_utc or now
            for person in _breakdown_people(day, row["wc_name"]):
                wc_attributions.cap_breakdown(day, row["wc_name"], person, resume)
            resolve_incident(row["id"], "recovered", resume_utc=resume)


def _breakdown_people(day: date, wc_name: str) -> list[str]:
    from . import wc_attributions
    return list(wc_attributions.breakdown_windows_for_day(day).get(wc_name, {}).keys())


def current_rows() -> list[dict]:
    """Snapshot rows for the inbox: open incidents with their live operators
    (snoozed operators filtered out). All local reads."""
    from datetime import datetime, UTC
    day = plant_today()
    now = datetime.now(UTC)
    out: list[dict] = []
    for row in open_incidents(day):
        snoozed = snoozed_people(row["id"], now)
        ops = [p for p in _operators_on(row["wc_name"], day, now) if p not in snoozed]
        out.append({
            "breakdown_id": row["id"],
            "wc_name": row["wc_name"],
            "detected_stop_utc": row["detected_stop_utc"],
            "operators": [{"name": p} for p in ops],
        })
    return out


def report_manual(wc_name: str) -> int:
    """Manual '+ Report a breakdown': open an incident now (stop = last output
    or now) with whoever is currently on the machine."""
    from datetime import datetime, UTC
    day = plant_today()
    now = datetime.now(UTC)
    sig = next((s for s in _station_signals(day, now) if s.wc_name == wc_name), None)
    stop = (sig.last_output_utc if sig and sig.last_output_utc else now)
    return open_incident(wc_name, day, stop, _operators_on(wc_name, day, now),
                         source="manual")
```

> **Executor note:** confirm the exact names for the Zira client accessor (`_zira_client`), `shift_config.shift_start_utc/shift_end_utc` (or compose from `shift_start_for`/`shift_end_for`), and `assignment_windows.resolve_segments`'s argument list by reading those modules; adjust the three marked call sites. The pure `detect`, the store, and `current_rows`'s shape are the contract and are already tested.

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_inbox.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/machine_breakdown.py tests/test_machine_breakdown_inbox.py
git commit -m "feat(breakdown): current_rows, detect tick, manual report"
```

---

### Task 10: Register detection on the warmer

**Files:**
- Modify: `src/zira_dashboard/app.py` (warmer tick funcs ~74-303, `_WARMERS` list ~305)

- [ ] **Step 1: Add a tick coroutine** near `_tick_missing_wc` (app.py ~164):

```python
async def _tick_machine_breakdown():
    from . import machine_breakdown
    await asyncio.to_thread(machine_breakdown.run_detect_tick)
```

- [ ] **Step 2: Register it** in `_WARMERS` (app.py ~305), 45s to align with the production warmer:

```python
    ("Machine breakdown", _tick_machine_breakdown, 45),
```

- [ ] **Step 3: Verify the app imports/boots**

Run: `ZIRA_API_KEY=test .venv/bin/python -c "import zira_dashboard.app"`
Expected: no import error.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/app.py
git commit -m "feat(breakdown): run detection on the 45s warmer"
```

---

## Phase 4 — Inbox snapshot + reconcile

### Task 11: `inbox_keys` + snapshot section

**Files:**
- Modify: `src/zira_dashboard/inbox_keys.py`, `src/zira_dashboard/exception_inbox.py`
- Test: `tests/test_machine_breakdown_inbox.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_snapshot_has_breakdown_section(monkeypatch):
    from zira_dashboard import exception_inbox, machine_breakdown, inbox_keys
    monkeypatch.setattr(machine_breakdown, "current_rows", lambda: [{
        "breakdown_id": 7, "wc_name": "Dismantler 2",
        "detected_stop_utc": datetime(2026, 7, 7, 18, 2, tzinfo=UTC),
        "operators": [{"name": "Juan Perez"}, {"name": "Ben Cruz"}],
    }])
    snap = exception_inbox.build_snapshot()
    section = next(s for s in snap["sections"] if s["id"] == "breakdown")
    assert section["count"] == 1
    row = section["rows"][0]
    assert row["priority"] == "urgent"
    assert row["item_key"] == inbox_keys.breakdown("Dismantler 2", "2026-07-07T18:02:00+00:00")
    assert row["action"]["type"] == "breakdown"
    assert row["action"]["breakdown_id"] == 7
    assert len(row["action"]["operators"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_inbox.py::test_snapshot_has_breakdown_section -q`
Expected: FAIL — no `breakdown` section / `inbox_keys.breakdown` missing.

- [ ] **Step 3: Implement**

Add to `inbox_keys.py`:

```python
def breakdown(wc_name, stop_iso) -> str:
    return f"breakdown:{wc_name}:{stop_iso}"
```

In `exception_inbox.py`:

Add a helper near `_work_center_names` (line 42):

```python
def _breakdown_rows(rows: list[dict], today: date) -> list[dict]:
    out = []
    for r in rows:
        stop = r.get("detected_stop_utc")
        stop_iso = stop.isoformat() if hasattr(stop, "isoformat") else str(stop)
        ops = r.get("operators") or []
        out.append({
            "name": r.get("wc_name"),
            "label": "Stopped producing",
            "detail": _plural(len(ops), "operator") + " to reassign",
            "priority": "urgent",
            "badge": "Broke down",
            "row_key": _row_key("breakdown", r.get("wc_name"), stop_iso),
            "item_key": inbox_keys.breakdown(r.get("wc_name"), stop_iso),
            "action": {
                "type": "breakdown",
                "breakdown_id": r.get("breakdown_id"),
                "wc_name": r.get("wc_name"),
                "stop_iso": stop_iso,
                "operators": [op.get("name") for op in ops],
            },
        })
    return out
```

In `build_summary()`, capture the count (after the `missed_rows` capture, ~line 189):

```python
    breakdown_rows = _capture(source_errors, "Machine Breakdown", machine_breakdown.current_rows, [])
```
add `from . import machine_breakdown` to that function's imports (line 179), include `breakdown_count = len(breakdown_rows)` in `urgent_total`, `total`, and the `"sections"` dict (`"breakdown": breakdown_count`).

In `build_snapshot()`, capture rows (after `missed_rows`, ~line 245):

```python
    breakdown_rows = _capture(source_errors, "Machine Breakdown", machine_breakdown.current_rows, [])
```
add `from . import machine_breakdown` to that function's imports (line 235), and add a section to the `sections` list (place it FIRST so urgent breakdowns sort to the very top within the urgent tier):

```python
        {
            "id": "breakdown",
            "title": "Machine Breakdown",
            "count": len(breakdown_rows),
            "tone": "bad",
            "action_key": None,
            "action_label": None,
            "empty": "All clear",
            "context": {"work_centers": work_centers},
            "rows": _breakdown_rows(breakdown_rows, today),
        },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_inbox.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/inbox_keys.py src/zira_dashboard/exception_inbox.py tests/test_machine_breakdown_inbox.py
git commit -m "feat(breakdown): inbox snapshot section + item key"
```

---

### Task 12: Reconcile registration

**Files:**
- Modify: `src/zira_dashboard/inbox_reconcile.py` (`_SECTION_KIND` line 26, `_KIND_SOURCE` line 37)
- Test: `tests/test_machine_breakdown_inbox.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_reconcile_knows_breakdown_kind():
    from zira_dashboard import inbox_reconcile as ir
    assert ir._SECTION_KIND["breakdown"] == "breakdown"
    assert ir._KIND_SOURCE["breakdown"] == "Machine Breakdown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_inbox.py::test_reconcile_knows_breakdown_kind -q`
Expected: FAIL — KeyError.

- [ ] **Step 3: Implement** — add to both dicts:

```python
# _SECTION_KIND (line 26):
    "breakdown": "breakdown",
# _KIND_SOURCE (line 37):
    "breakdown": "Machine Breakdown",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_inbox.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/inbox_reconcile.py tests/test_machine_breakdown_inbox.py
git commit -m "feat(breakdown): register breakdown kind with the inbox reconciler"
```

---

## Phase 5 — Endpoints

### Task 13: Transfer, Snooze, Dismiss, Manual-report endpoints

**Files:**
- Modify: `src/zira_dashboard/routes/exceptions.py`
- Test: `tests/test_machine_breakdown_inbox.py`

- [ ] **Step 1: Write the failing test** (append — uses a FastAPI TestClient, mirroring existing route tests; stub Odoo/transfer)

```python
def test_breakdown_endpoints(monkeypatch):
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app
    from zira_dashboard import machine_breakdown as mb, staffing_transfer, wc_attributions
    d = date(2026, 7, 7)
    stop = datetime(2026, 7, 7, 18, 2, tzinfo=UTC)
    inc_id = mb.open_incident("Dismantler 2", d, stop, ["Juan Perez"])
    monkeypatch.setattr(mb, "plant_today", lambda: d)
    monkeypatch.setattr(staffing_transfer, "decide_and_apply",
                        lambda person, wc, ts: {"transfer": "moved", "person": person})

    client = TestClient(app)
    # transfer: caps Juan's window + returns event_id
    r = client.post("/api/exceptions/breakdown/transfer", json={
        "breakdown_id": inc_id, "wc_name": "Dismantler 2",
        "person_name": "Juan Perez", "to_wc": "Repair 3"})
    assert r.status_code == 200 and r.json()["ok"]
    caps = wc_attributions.breakdown_windows_for_day(d)["Dismantler 2"]["Juan Perez"]
    assert caps[0][1] is not None  # capped

    # snooze
    r = client.post("/api/exceptions/breakdown/snooze", json={
        "breakdown_id": inc_id, "person_name": "Juan Perez"})
    assert r.json()["ok"]
    assert mb.snoozed_people(inc_id, datetime.now(UTC)) == {"Juan Perez"}

    # dismiss: deletes windows + resolves
    r = client.post("/api/exceptions/breakdown/dismiss", json={"breakdown_id": inc_id})
    assert r.json()["ok"]
    assert mb.open_wc_names(d) == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_inbox.py::test_breakdown_endpoints -q`
Expected: FAIL — 404 (routes not defined).

- [ ] **Step 3: Implement** — add to `routes/exceptions.py` (import `machine_breakdown` where used):

```python
def _breakdown_transfer_sync(body, actor_upn=None, actor_name=None):
    from datetime import datetime, UTC
    from .. import inbox_keys, inbox_log, machine_breakdown, staffing_transfer, wc_attributions
    try:
        inc_id = int(body.get("breakdown_id"))
    except (TypeError, ValueError):
        return _json_error("bad breakdown_id", 400)
    wc_name = str(body.get("wc_name") or "").strip()
    person = str(body.get("person_name") or "").strip()
    to_wc = str(body.get("to_wc") or "").strip()
    if not (wc_name and person and to_wc):
        return _json_error("wc_name, person_name, to_wc required", 400)
    inc = machine_breakdown.get_incident(inc_id)
    if inc is None:
        return _json_error("breakdown not found", 404)
    now = datetime.now(UTC)
    wc_attributions.cap_breakdown(inc["day"], wc_name, person, now)
    try:
        result = staffing_transfer.decide_and_apply(person, to_wc, now)
    except Exception as e:
        wc_attributions.uncap_breakdown(inc["day"], wc_name, person)  # roll back the cap
        return _json_error(_friendly_odoo_error(e), 500)
    eid = inbox_log.log_event_safe(
        item_kind="breakdown",
        item_key=inbox_keys.breakdown(wc_name, _iso_day(inc["detected_stop_utc"]) or ""),
        person_name=person, category_label="Machine Breakdown",
        action="transfer", outcome=f"Moved to {to_wc}", after_value=to_wc,
        actor_upn=actor_upn, actor_name=actor_name, source="inbox", reversible=True,
        detail={"breakdown_id": inc_id, "wc_name": wc_name, "person": person,
                "transfer": result},
    )
    return JSONResponse({"ok": True, "event_id": eid, "transfer": result})


@router.post("/api/exceptions/breakdown/transfer")
async def breakdown_transfer(request: Request):
    body = await request.json()
    actor_upn, actor_name = _actor_from(request)
    return await asyncio.to_thread(_breakdown_transfer_sync, body, actor_upn, actor_name)


def _breakdown_snooze_sync(body, actor_upn=None, actor_name=None):
    from datetime import datetime, timedelta, UTC
    from .. import machine_breakdown
    try:
        inc_id = int(body.get("breakdown_id"))
    except (TypeError, ValueError):
        return _json_error("bad breakdown_id", 400)
    person = str(body.get("person_name") or "").strip()
    if not person:
        return _json_error("person_name required", 400)
    machine_breakdown.snooze(inc_id, person, datetime.now(UTC) + timedelta(minutes=15))
    return JSONResponse({"ok": True})


@router.post("/api/exceptions/breakdown/snooze")
async def breakdown_snooze(request: Request):
    body = await request.json()
    actor_upn, actor_name = _actor_from(request)
    return await asyncio.to_thread(_breakdown_snooze_sync, body, actor_upn, actor_name)


def _breakdown_dismiss_sync(body, actor_upn=None, actor_name=None):
    from .. import inbox_keys, inbox_log, machine_breakdown
    try:
        inc_id = int(body.get("breakdown_id"))
    except (TypeError, ValueError):
        return _json_error("bad breakdown_id", 400)
    inc = machine_breakdown.get_incident(inc_id)
    if inc is None:
        return _json_error("breakdown not found", 404)
    machine_breakdown.dismiss_incident(inc_id)
    eid = inbox_log.log_event_safe(
        item_kind="breakdown",
        item_key=inbox_keys.breakdown(inc["wc_name"], _iso_day(inc["detected_stop_utc"]) or ""),
        person_name=None, category_label="Machine Breakdown",
        action="dismiss", outcome="Not a breakdown",
        actor_upn=actor_upn, actor_name=actor_name, source="inbox", reversible=True,
        detail={"breakdown_id": inc_id, "wc_name": inc["wc_name"]},
    )
    return JSONResponse({"ok": True, "event_id": eid})


@router.post("/api/exceptions/breakdown/dismiss")
async def breakdown_dismiss(request: Request):
    body = await request.json()
    actor_upn, actor_name = _actor_from(request)
    return await asyncio.to_thread(_breakdown_dismiss_sync, body, actor_upn, actor_name)


def _breakdown_report_sync(body):
    from .. import machine_breakdown, staffing
    wc_name = str(body.get("wc_name") or "").strip()
    metered = {s.name for s in __import__("zira_dashboard.stations", fromlist=["recycling_stations"]).recycling_stations()}
    if wc_name not in metered:
        return _json_error("unknown machine", 400)
    inc_id = machine_breakdown.report_manual(wc_name)
    return JSONResponse({"ok": True, "breakdown_id": inc_id})


@router.post("/api/exceptions/breakdown/report")
async def breakdown_report(request: Request):
    body = await request.json()
    return await asyncio.to_thread(_breakdown_report_sync, body)
```

> **Executor note:** `log_event_safe`'s `detail=` param stores JSONB (used by undo below to recover `breakdown_id`/`wc_name`/`person`). Confirm `inbox_log.log_event_safe` accepts `detail`; the schema has the column (`inbox_events.detail JSONB`). If the helper doesn't forward it yet, add the pass-through.

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_inbox.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/exceptions.py tests/test_machine_breakdown_inbox.py
git commit -m "feat(breakdown): transfer/snooze/dismiss/report endpoints"
```

---

### Task 14: Undo wiring

**Files:**
- Modify: `src/zira_dashboard/routes/exceptions.py` (`_UNDOABLE` line 60, `_reverse_event` line 655)
- Test: `tests/test_machine_breakdown_inbox.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_undo_breakdown_transfer_and_dismiss(monkeypatch):
    from zira_dashboard.routes import exceptions as ex
    from zira_dashboard import machine_breakdown as mb, wc_attributions, odoo_client
    d = date(2026, 7, 7)
    inc_id = mb.open_incident("Dismantler 2", d, datetime(2026,7,7,18,2,tzinfo=UTC), ["Juan Perez"])
    monkeypatch.setattr(odoo_client, "undo_transfer", lambda closed, new: None)

    # transfer undo re-opens Juan's exclusion window
    ev = {"item_kind": "breakdown", "action": "transfer",
          "item_key": "breakdown:Dismantler 2:x",
          "detail": {"breakdown_id": inc_id, "wc_name": "Dismantler 2",
                     "person": "Juan Perez", "transfer": {"closed_id": 1, "new_id": 2}}}
    wc_attributions.cap_breakdown(d, "Dismantler 2", "Juan Perez", datetime(2026,7,7,18,30,tzinfo=UTC))
    ex._reverse_event(ev)
    assert wc_attributions.breakdown_windows_for_day(d)["Dismantler 2"]["Juan Perez"][0][1] is None

    # dismiss undo re-opens the incident
    mb.dismiss_incident(inc_id)
    ev2 = {"item_kind": "breakdown", "action": "dismiss",
           "item_key": "breakdown:Dismantler 2:x",
           "detail": {"breakdown_id": inc_id, "wc_name": "Dismantler 2"}}
    ex._reverse_event(ev2)
    assert mb.get_incident(inc_id)["resolved_at"] is None

    assert ("breakdown", "transfer") in ex._UNDOABLE
    assert ("breakdown", "dismiss") in ex._UNDOABLE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_inbox.py::test_undo_breakdown_transfer_and_dismiss -q`
Expected: FAIL — pair not in `_UNDOABLE` / no reverse branch.

- [ ] **Step 3: Implement**

Add to `_UNDOABLE` (line 60):

```python
    ("breakdown", "transfer"),
    ("breakdown", "dismiss"),
```

Add branches in `_reverse_event` (after the `late` branch, ~line 673); import `machine_breakdown`, `wc_attributions`:

```python
    elif kind == "breakdown":
        from .. import machine_breakdown, wc_attributions
        detail = ev.get("detail") or {}
        day = machine_breakdown.plant_today()
        wc = detail.get("wc_name")
        if action == "transfer":
            tr = detail.get("transfer") or {}
            odoo_client.undo_transfer(tr.get("closed_id"), tr.get("new_id"))
            wc_attributions.uncap_breakdown(day, wc, detail.get("person"))
        elif action == "dismiss":
            machine_breakdown.reopen_incident(detail.get("breakdown_id"))
            for person in machine_breakdown._operators_on(wc, day, plant_day.now()):
                wc_attributions.add_breakdown(day, wc, person, plant_day.now())
```

> **Executor note:** `_reverse_event` currently derives ids from `key.split(":")`. The breakdown branch reads from `ev["detail"]` instead (the key's `wc_name` can contain colons — e.g. none today, but detail is the robust source). Confirm `inbox_log.get_event` returns `detail` (it selects the column per the schema).

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_machine_breakdown_inbox.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/exceptions.py tests/test_machine_breakdown_inbox.py
git commit -m "feat(breakdown): undo for transfer + dismiss"
```

---

## Phase 6 — UI (bespoke breakdown card)

### Task 15: Template — breakdown card render

**Files:**
- Modify: `src/zira_dashboard/templates/exceptions.html` (data-* block ~69-80; `.row-actions` branches ~93-141)

- [ ] **Step 1: Add the `data-*` branch** for breakdown (in the `{% elif %}` chain at line 78, before the closing `{% endif %}`):

```html
           {% elif action and action.type == 'breakdown' %}
             data-breakdown-id="{{ action.breakdown_id or '' }}"
             data-wc-name="{{ action.wc_name or '' }}"
```

- [ ] **Step 2: Add the card body branch** in `.row-actions` (add a new `{% elif %}` before `{% elif row.get('href') %}` at line 139). This renders one sub-row per operator with a WC `<select>`, Transfer, Snooze, plus a card-level "Not a breakdown":

```html
          {% elif action and action.type == 'breakdown' %}
            <div class="breakdown-ops">
              {% for op in action.operators %}
                <div class="breakdown-op" data-op-name="{{ op }}">
                  <span class="breakdown-op-name">{{ op }}</span>
                  <select class="inline-select js-breakdown-wc" aria-label="Transfer {{ op }} to">
                    <option value="">Transfer to…</option>
                    {% for wc in work_centers %}<option value="{{ wc }}">{{ wc }}</option>{% endfor %}
                  </select>
                  <button type="button" class="row-btn primary js-breakdown-transfer">Transfer</button>
                  <button type="button" class="row-btn js-breakdown-snooze">Snooze 15m</button>
                </div>
              {% endfor %}
              <button type="button" class="row-btn subtle js-breakdown-dismiss">Not a breakdown</button>
            </div>
```

- [ ] **Step 3: Verify the page renders** (server-side) — covered by Task 16's JS test loading the page; for now confirm Jinja parses:

Run: `ZIRA_API_KEY=test .venv/bin/python -c "from zira_dashboard.deps import templates; templates.get_template('exceptions.html')"`
Expected: no `TemplateSyntaxError`.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/templates/exceptions.html
git commit -m "feat(breakdown): inbox card template (per-operator sub-rows)"
```

---

### Task 16: JS — breakdown card handlers

**Files:**
- Modify: `src/zira_dashboard/static/exceptions.js` (delegated click listener ~527-712; a per-op resolve that removes just the sub-row and only clears the card when empty)

- [ ] **Step 1: Add per-operator + card handlers** inside the delegated listener (before the `js-time-off-approve` block, after `personName`/`attendanceId` are read at line 533). These use `row.dataset.breakdownId` and the sub-row's `.js-breakdown-wc`. Because a breakdown card holds multiple operators, transfer/snooze remove only that operator's sub-row; the whole card is finalized only when no operators remain:

```javascript
    function breakdownOpRow(el) { return el.closest('.breakdown-op'); }

    function finalizeBreakdownOp(row, opRow) {
      if (opRow) opRow.remove();
      // No operators left -> the card is done; clear it like any resolved row.
      if (!row.querySelector('.breakdown-op')) {
        finalizeResolved(row);
      }
    }

    if (rowBtn.classList.contains('js-breakdown-transfer')) {
      var opRow = breakdownOpRow(rowBtn);
      var toWc = opRow.querySelector('.js-breakdown-wc').value;
      var opName = opRow.dataset.opName;
      if (!toWc) { failRow(row, 'Pick a work center.'); return; }
      setBusy(row, true); rowStatus(row, 'Moving ' + opName + '…', false);
      postJson('/api/exceptions/breakdown/transfer', {
        breakdown_id: row.dataset.breakdownId, wc_name: row.dataset.wcName,
        person_name: opName, to_wc: toWc,
      }).then(function (resp) {
        setBusy(row, false);
        if (resp && resp.ok) { rowStatus(row, opName + ' → ' + toWc, false); finalizeBreakdownOp(row, opRow); }
        else failRow(row, (resp && resp.error) || 'Transfer failed.');
      }).catch(function () { failRow(row, 'Network error.'); });
      return;
    }

    if (rowBtn.classList.contains('js-breakdown-snooze')) {
      var opRow2 = breakdownOpRow(rowBtn);
      var opName2 = opRow2.dataset.opName;
      setBusy(row, true); rowStatus(row, 'Snoozing ' + opName2 + '…', false);
      postJson('/api/exceptions/breakdown/snooze', {
        breakdown_id: row.dataset.breakdownId, person_name: opName2,
      }).then(function (resp) {
        setBusy(row, false);
        if (resp && resp.ok) { rowStatus(row, opName2 + ' snoozed 15m', false); finalizeBreakdownOp(row, opRow2); }
        else failRow(row, (resp && resp.error) || 'Snooze failed.');
      }).catch(function () { failRow(row, 'Network error.'); });
      return;
    }

    if (rowBtn.classList.contains('js-breakdown-dismiss')) {
      setBusy(row, true); rowStatus(row, 'Dismissing…', false);
      postJson('/api/exceptions/breakdown/dismiss', {
        breakdown_id: row.dataset.breakdownId,
      }).then(function (resp) {
        if (resp && resp.ok) resolveRow(row, 'Not a breakdown', resp.event_id);
        else failRow(row, (resp && resp.error) || 'Dismiss failed.');
      }).catch(function () { failRow(row, 'Network error.'); });
      return;
    }
```

- [ ] **Step 2: Verify with the preview server**

Start the app (preview tooling), open `/exceptions` with an open breakdown incident seeded, and confirm: the card shows one row per operator; Transfer with a WC selected removes that operator's sub-row and shows the toast; Snooze removes the sub-row; "Not a breakdown" clears the card with the 5s Undo. Check `preview_console_logs` for JS errors.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/static/exceptions.js
git commit -m "feat(breakdown): inbox card JS handlers (transfer/snooze/dismiss)"
```

---

### Task 17: "+ Report a breakdown" button

**Files:**
- Modify: `src/zira_dashboard/templates/exceptions.html` (header, near the `.inbox-title` refresh button ~27), `src/zira_dashboard/static/exceptions.js`

- [ ] **Step 1: Add the button + a minimal machine picker** to the header. Use a `<details>`/`<select>` of the metered recycling stations (passed from the route as `work_centers` is the full list — instead pass a dedicated `breakdown_machines` list). Add to the exceptions route context (`routes/exceptions.py:exceptions_page`):

```python
    from ..stations import recycling_stations
    ...
    "breakdown_machines": [s.name for s in recycling_stations()],
```

Template (header):

```html
    <details class="report-breakdown">
      <summary class="refresh-btn">＋ Report a breakdown</summary>
      <div class="report-breakdown-body">
        <select class="inline-select js-report-machine" aria-label="Machine">
          <option value="">Machine…</option>
          {% for m in breakdown_machines %}<option value="{{ m }}">{{ m }}</option>{% endfor %}
        </select>
        <button type="button" class="row-btn primary js-report-breakdown">Report</button>
      </div>
    </details>
```

- [ ] **Step 2: Add the JS handler** (in the delegated listener, near the top with the other non-row buttons ~520):

```javascript
    var reportBtn = event.target.closest('.js-report-breakdown');
    if (reportBtn) {
      var sel = document.querySelector('.js-report-machine');
      var machine = sel ? sel.value : '';
      if (!machine) { return; }
      reportBtn.disabled = true;
      postJson('/api/exceptions/breakdown/report', {wc_name: machine})
        .then(function (resp) {
          if (resp && resp.ok) window.location.reload();
          else { reportBtn.disabled = false; }
        }).catch(function () { reportBtn.disabled = false; });
      return;
    }
```

- [ ] **Step 3: Verify with the preview server** — click "Report a breakdown", pick "Dismantler 2", confirm a card appears on reload.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/templates/exceptions.html src/zira_dashboard/static/exceptions.js src/zira_dashboard/routes/exceptions.py
git commit -m "feat(breakdown): manual '+ Report a breakdown' entry point"
```

---

## Phase 7 — Final verification

### Task 18: Full suite + end-to-end sanity

- [ ] **Step 1: Run the whole suite**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 2: CSS** — add minimal styling for `.breakdown-ops`, `.breakdown-op`, `.report-breakdown` to `src/zira_dashboard/static/exceptions.css`, following the existing `.row-actions` / `.inline-select` styles (stacked sub-rows, subtle dismiss). Verify with the preview server in light + dark.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/static/exceptions.css
git commit -m "style(breakdown): inbox card layout"
```

- [ ] **Step 4: Manual smoke (documented, not automated)** — with a live-ish DB: seed an open incident for "Dismantler 2", load `/exceptions`, transfer one operator, snooze another, then dismiss; confirm the leaderboard for that person shows the dead window removed from expected (their % not dragged down).

---

## Self-Review

**Spec coverage:**
- Auto-detect trigger → Task 7 (`detect`) + Task 9 (`run_detect_tick`) + Task 10 (warmer). ✓
- Manual "Report a breakdown" → Task 9 (`report_manual`) + Task 13 (endpoint) + Task 17 (UI). ✓
- Auto-detected stop time → `detect` sets `detected_stop_utc` from last active interval. ✓
- Per-operator Transfer → Task 13 + Task 16. ✓
- Snooze 15m (per operator) → Task 8 + Task 13 + Task 16. ✓
- "Not a breakdown" dismiss (card-level, deletes exclusions, suppresses re-detect) → Task 8 (`dismiss_incident`, `dismissed_wc_names`), Task 7 (`dismissed_wcs` skip), Task 13, Task 16. ✓
- Automatic exclusion (capped on transfer / punch-out / resume) → Task 2 (`add/cap`), Task 9 (`run_detect_tick` caps on resume; `current_rows`/precompute cap-on-departure via segment end), Task 4 (excluded_minutes), Task 5 + 6 (averages honor it). ✓ — **Note:** capping on *punch-out* relies on the operator dropping out of `_operators_on` (segment ends) and `_attach_excluded_minutes` using the open window to `now`; the window is only hard-capped on transfer or machine resume. This keeps live averages correct; verify the punch-out path in Task 18 Step 4.
- Historical numbers untouched → Task 5 regression guard (Step 5). ✓
- Undo (transfer + dismiss) → Task 14. ✓
- Auto-resolve on recovery / all-handled → Task 9 (`recovered`) + reconcile Task 12 (all-handled: card empties → item leaves queue → reconciler logs auto_resolved). ✓
- Kill-switch `MACHINE_BREAKDOWN_ENABLED` + `BREAKDOWN_NO_OUTPUT_MINUTES` → Task 9. ✓

**Placeholder scan:** Three call sites in Task 9 are explicitly flagged as "confirm exact helper names by reading the module" (Zira client accessor, `shift_config` shift-window helpers, `resolve_segments` args) — these are real, named functions whose signatures the executor must read before wiring; the tested contracts (`detect`, store, `current_rows` shape) are complete. Task 6's test/impl is deferred to an in-task read of `expected_by_wc`. These are the only non-verbatim spots and each names exactly what to confirm.

**Type consistency:** `excluded_minutes` is the column/row-key throughout (schema, flatten, upsert, daily_records, averages). `breakdown_id`/`wc_name`/`person`/`transfer` are the `detail` JSON keys shared by Task 13 (write) and Task 14 (undo read). `StationSignal`/`BreakdownIncident` dataclasses are defined in Task 7 before use in Task 9. `item_kind='breakdown'`, `action` values `transfer`/`dismiss`/`snooze` are consistent across endpoints, `_UNDOABLE`, and reconcile.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-07-machine-breakdown-inbox.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review.

**Which approach?**
