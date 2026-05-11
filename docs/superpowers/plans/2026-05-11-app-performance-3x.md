# App Performance: Precompute + Warm Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get the dashboard ~3x faster by eliminating Odoo/StratusTime calls from the request path: precompute per-day-per-person-per-WC into `production_daily` for history pages, and run a background warmer for today's data on the live pages.

**Architecture:** One fact table (`production_daily`) populated nightly + refreshed on the live warmer's tick; three small JSONB cache tables (`today_attendance_cache`, `today_timeoff_cache`, `today_production_cache`) refreshed every 45 s by an in-process asyncio task. Route hot paths swap from "call external API + compute" to "SELECT … FROM …". Existing pure-function cores (`rank_by_category`, `apply_overrides`, `_rank_single_day`, `attribute_for_day`) are untouched — only their data-fetch layer changes.

**Tech Stack:** Python 3.11+, FastAPI, psycopg2 + Postgres, pytest, Jinja templates.

**Spec:** `docs/superpowers/specs/2026-05-11-app-performance-3x-design.md`

---

## File Structure

**New files:**
- `src/zira_dashboard/precompute.py` — flatten attribution into `production_daily` rows; UPSERT + range query helpers
- `src/zira_dashboard/live_cache.py` — read/write helpers for the three `today_*_cache` tables + cold-start safety valve
- `tests/test_precompute.py` — unit + integration tests for the precompute module
- `tests/test_live_cache.py` — unit + integration tests for live_cache module
- `tests/test_admin_precompute.py` — endpoint tests

**Modified files:**
- `src/zira_dashboard/db.py` — append four `CREATE TABLE` statements to `_SCHEMA_DDL`
- `src/zira_dashboard/production_history.py` — `daily_records` reads from `production_daily`; `attribution_range` becomes a thin wrapper over `precompute.sum_by_range`
- `src/zira_dashboard/routes/admin.py` — add `precompute_run` endpoint (covers nightly + backfill)
- `src/zira_dashboard/app.py` — add third warmer task in `lifespan`
- `src/zira_dashboard/routes/staffing.py` — `/api/late-report` and `/staffing/{today}` read from `live_cache`
- `CHANGELOG.md` — entries for each deploy

**Responsibility split:** `precompute.py` owns the `production_daily` table — every write and read goes through it. `live_cache.py` owns the three `today_*_cache` tables and the warmer's refresh logic. The existing modules keep their pure-functional cores; only their data-fetch wrappers are rewritten.

---

## Conventions used in every task

- Tests that hit Postgres are gated with the existing pattern: `pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="...")`.
- Every Postgres test calls `db.init_pool()` and `db.bootstrap_schema()` first, and runs cleanup queries in a fixture so the test database stays clean.
- Commit messages follow the convention in `git log`: `feat:`, `fix:`, `test:`, `docs:`, `schema:` prefixes.
- CHANGELOG entries follow `### TIME` under today's date (memory rule — every deploy gets an entry).

---

## Task 1: Schema migrations — four new tables

**Files:**
- Modify: `src/zira_dashboard/db.py:136-` (append to `_SCHEMA_DDL`)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
def test_bootstrap_creates_precompute_tables():
    db.init_pool()
    db.bootstrap_schema()
    rows = db.query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name IN "
        "('production_daily','today_attendance_cache',"
        "'today_timeoff_cache','today_production_cache')"
    )
    names = {r["table_name"] for r in rows}
    assert names == {
        "production_daily",
        "today_attendance_cache",
        "today_timeoff_cache",
        "today_production_cache",
    }


def test_production_daily_pk_and_indexes():
    db.init_pool()
    db.bootstrap_schema()
    # PK columns
    pk_rows = db.query(
        "SELECT a.attname FROM pg_index i "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = 'production_daily'::regclass AND i.indisprimary "
        "ORDER BY a.attname"
    )
    assert {r["attname"] for r in pk_rows} == {"day", "emp_id", "wc_name"}
    # Both secondary indexes exist
    idx_rows = db.query(
        "SELECT indexname FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = 'production_daily'"
    )
    idx_names = {r["indexname"] for r in idx_rows}
    assert any("name" in n and "day" in n for n in idx_names)
    assert any("wc_name" in n and "day" in n for n in idx_names)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py::test_bootstrap_creates_precompute_tables tests/test_db.py::test_production_daily_pk_and_indexes -v`
Expected: FAIL — tables don't exist yet.

- [ ] **Step 3: Append DDL to `_SCHEMA_DDL` in `db.py`**

In `src/zira_dashboard/db.py`, find the end of `_SCHEMA_DDL` (just before the closing `"""`). Append:

```sql
-- Precompute fact table -------------------------------------------------
-- One row per (day, person, WC). Written nightly for past days, written
-- by the live warmer for today. Every leaderboard / player-card /
-- trophy / value-stream page reads from here.
CREATE TABLE IF NOT EXISTS production_daily (
  day         DATE   NOT NULL,
  emp_id      TEXT   NOT NULL,
  name        TEXT   NOT NULL,
  wc_name     TEXT   NOT NULL,
  units       NUMERIC NOT NULL DEFAULT 0,
  downtime    NUMERIC NOT NULL DEFAULT 0,
  hours       NUMERIC NOT NULL DEFAULT 0,
  days_worked NUMERIC NOT NULL DEFAULT 0,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (day, emp_id, wc_name)
);
CREATE INDEX IF NOT EXISTS idx_production_daily_name_day
  ON production_daily (name, day);
CREATE INDEX IF NOT EXISTS idx_production_daily_wc_day
  ON production_daily (wc_name, day);

-- Live cache tables ----------------------------------------------------
-- Single-row JSONB blobs keyed by today's date. The live warmer
-- overwrites them every 45 s. Routes read from here instead of calling
-- StratusTime / Odoo in the request path. `refreshed_at` lets routes
-- detect staleness for a cold-start safety valve.
CREATE TABLE IF NOT EXISTS today_attendance_cache (
  day          DATE PRIMARY KEY,
  payload      JSONB NOT NULL,
  refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS today_timeoff_cache (
  day          DATE PRIMARY KEY,
  payload      JSONB NOT NULL,
  refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS today_production_cache (
  day          DATE PRIMARY KEY,
  payload      JSONB NOT NULL,
  refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py::test_bootstrap_creates_precompute_tables tests/test_db.py::test_production_daily_pk_and_indexes -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/db.py tests/test_db.py
git commit -m "schema: precompute fact table + today live-cache tables"
```

---

## Task 2: Precompute module — flatten + upsert

**Files:**
- Create: `src/zira_dashboard/precompute.py`
- Test: `tests/test_precompute.py`

- [ ] **Step 1: Write failing tests for `flatten_attribution`**

Create `tests/test_precompute.py`:

```python
import os
from datetime import date

import pytest


def test_flatten_attribution_empty():
    from zira_dashboard.precompute import flatten_attribution
    out = flatten_attribution(date(2026, 5, 1), {}, name_to_emp_id={})
    assert out == []


def test_flatten_attribution_solo_operator():
    from zira_dashboard.precompute import flatten_attribution
    attribution = {
        "Christian": {
            "Repair 1": {
                "units": 80.0, "downtime": 12.0, "hours": 8.0, "days_worked": 1,
            }
        }
    }
    out = flatten_attribution(
        date(2026, 5, 1), attribution, name_to_emp_id={"Christian": "E123"}
    )
    assert out == [{
        "day": date(2026, 5, 1),
        "emp_id": "E123",
        "name": "Christian",
        "wc_name": "Repair 1",
        "units": 80.0,
        "downtime": 12.0,
        "hours": 8.0,
        "days_worked": 1.0,
    }]


def test_flatten_skips_zero_units():
    from zira_dashboard.precompute import flatten_attribution
    attribution = {"Bob": {"Repair 1": {"units": 0.0, "downtime": 0.0, "hours": 0.0, "days_worked": 0}}}
    out = flatten_attribution(date(2026, 5, 1), attribution, name_to_emp_id={"Bob": "E1"})
    assert out == []


def test_flatten_skips_unknown_name():
    from zira_dashboard.precompute import flatten_attribution
    attribution = {"Ghost": {"Repair 1": {"units": 50.0, "downtime": 0.0, "hours": 4.0, "days_worked": 1}}}
    out = flatten_attribution(date(2026, 5, 1), attribution, name_to_emp_id={})
    # Ghost has no emp_id → skipped, but logged elsewhere; for now just verify skip
    assert out == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_precompute.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create `src/zira_dashboard/precompute.py` with `flatten_attribution`**

```python
"""Precompute layer for daily-OK pages.

Owns the `production_daily` table: every write (nightly + live-warmer)
and every read (leaderboards, player cards, trophies, value streams)
goes through this module.

The two halves:

  - Write path:  precompute_day(day, client) — calls
    production_history.attribution_for(), flattens the nested dict into
    per-(day, emp_id, wc) rows, UPSERTs them.
  - Read path:   sum_by_range, sum_by_name, daily_records — replace the
    on-demand attribution loops that used to run inside each request.

Cores that operate on lists/dicts of rows (rank_by_category,
apply_overrides, _rank_single_day) live in their own modules and are
unaffected.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable


def flatten_attribution(
    day: date,
    attribution: dict[str, dict[str, dict[str, float]]],
    name_to_emp_id: dict[str, str],
) -> list[dict]:
    """Turn {person: {wc: {units, downtime, hours, days_worked}}} into
    a flat list of rows ready for UPSERT into production_daily.

    Rows where units == 0 are dropped (the attribution dict can carry
    zero-unit rows for multi-person WCs with no production; they add
    no value in the table).

    Rows where the operator has no emp_id (not in the StratusTime
    directory) are dropped silently — the caller is expected to log
    and the next Odoo sync will pull the person in.
    """
    rows: list[dict] = []
    for person, wc_map in attribution.items():
        emp_id = name_to_emp_id.get(person)
        if not emp_id:
            continue
        for wc_name, totals in wc_map.items():
            units = float(totals.get("units") or 0)
            if units <= 0:
                continue
            rows.append({
                "day": day,
                "emp_id": str(emp_id),
                "name": person,
                "wc_name": wc_name,
                "units": units,
                "downtime": float(totals.get("downtime") or 0),
                "hours": float(totals.get("hours") or 0),
                "days_worked": float(totals.get("days_worked") or 0),
            })
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_precompute.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Add failing tests for `upsert_production_daily` (Postgres-gated)**

Append to `tests/test_precompute.py`:

```python
pytestmark_pg = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="Postgres tests need a live DATABASE_URL",
)


@pytestmark_pg
def test_upsert_inserts_rows():
    from zira_dashboard import db
    from zira_dashboard.precompute import upsert_production_daily
    db.init_pool(); db.bootstrap_schema()
    db.execute("DELETE FROM production_daily WHERE day = %s", (date(2099, 1, 1),))

    rows = [
        {"day": date(2099, 1, 1), "emp_id": "E1", "name": "A", "wc_name": "WC1",
         "units": 10.0, "downtime": 1.0, "hours": 4.0, "days_worked": 1.0},
    ]
    upsert_production_daily(rows)

    got = db.query(
        "SELECT emp_id, name, wc_name, units, hours FROM production_daily "
        "WHERE day = %s ORDER BY emp_id, wc_name",
        (date(2099, 1, 1),),
    )
    assert len(got) == 1
    assert got[0]["emp_id"] == "E1"
    assert float(got[0]["units"]) == 10.0

    db.execute("DELETE FROM production_daily WHERE day = %s", (date(2099, 1, 1),))


@pytestmark_pg
def test_upsert_overwrites_on_pk_conflict():
    from zira_dashboard import db
    from zira_dashboard.precompute import upsert_production_daily
    db.init_pool(); db.bootstrap_schema()
    db.execute("DELETE FROM production_daily WHERE day = %s", (date(2099, 1, 2),))

    upsert_production_daily([{
        "day": date(2099, 1, 2), "emp_id": "E1", "name": "A", "wc_name": "WC1",
        "units": 10.0, "downtime": 1.0, "hours": 4.0, "days_worked": 1.0,
    }])
    upsert_production_daily([{
        "day": date(2099, 1, 2), "emp_id": "E1", "name": "A", "wc_name": "WC1",
        "units": 99.0, "downtime": 9.0, "hours": 9.0, "days_worked": 1.0,
    }])

    got = db.query(
        "SELECT units FROM production_daily WHERE day = %s",
        (date(2099, 1, 2),),
    )
    assert len(got) == 1
    assert float(got[0]["units"]) == 99.0

    db.execute("DELETE FROM production_daily WHERE day = %s", (date(2099, 1, 2),))
```

- [ ] **Step 6: Run them to verify they fail**

Run: `pytest tests/test_precompute.py -v`
Expected: 2 new FAILs (upsert_production_daily not defined).

- [ ] **Step 7: Implement `upsert_production_daily` in `precompute.py`**

Append to `src/zira_dashboard/precompute.py`:

```python
def upsert_production_daily(rows: Iterable[dict]) -> int:
    """UPSERT a batch of rows into production_daily. Returns count written.

    Idempotent: PK conflict triggers an UPDATE of every non-PK column
    and bumps `computed_at`. Re-running the same day overwrites cleanly.
    """
    from . import db
    rows = list(rows)
    if not rows:
        return 0
    sql = """
        INSERT INTO production_daily (
            day, emp_id, name, wc_name,
            units, downtime, hours, days_worked, computed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (day, emp_id, wc_name) DO UPDATE SET
            name        = EXCLUDED.name,
            units       = EXCLUDED.units,
            downtime    = EXCLUDED.downtime,
            hours       = EXCLUDED.hours,
            days_worked = EXCLUDED.days_worked,
            computed_at = now()
    """
    db.execute_many(sql, [
        (r["day"], r["emp_id"], r["name"], r["wc_name"],
         r["units"], r["downtime"], r["hours"], r["days_worked"])
        for r in rows
    ])
    return len(rows)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_precompute.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add src/zira_dashboard/precompute.py tests/test_precompute.py
git commit -m "feat(precompute): flatten_attribution + upsert_production_daily"
```

---

## Task 3: Precompute module — orchestrator `precompute_day`

**Files:**
- Modify: `src/zira_dashboard/precompute.py`
- Test: `tests/test_precompute.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_precompute.py`:

```python
def test_precompute_day_flattens_and_upserts(monkeypatch):
    from zira_dashboard import precompute
    calls = {"attribution": 0, "upsert": []}

    def fake_attribution(d, client):
        calls["attribution"] += 1
        return {
            "Alice": {"WC1": {"units": 50.0, "downtime": 2.0, "hours": 4.0, "days_worked": 1}},
            "Bob":   {"WC1": {"units": 50.0, "downtime": 2.0, "hours": 4.0, "days_worked": 1}},
        }

    def fake_name_map():
        return {"Alice": "E1", "Bob": "E2"}

    def fake_upsert(rows):
        calls["upsert"].extend(rows)
        return len(rows)

    monkeypatch.setattr(
        "zira_dashboard.production_history.attribution_for", fake_attribution
    )
    monkeypatch.setattr(
        "zira_dashboard.stratustime_client.name_to_emp_id_map", fake_name_map
    )
    monkeypatch.setattr(precompute, "upsert_production_daily", fake_upsert)

    result = precompute.precompute_day(date(2026, 5, 1), client=None)

    assert result == {"day": "2026-05-01", "rows_written": 2}
    assert calls["attribution"] == 1
    assert {r["name"] for r in calls["upsert"]} == {"Alice", "Bob"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_precompute.py::test_precompute_day_flattens_and_upserts -v`
Expected: FAIL — `precompute_day` not defined.

- [ ] **Step 3: Implement `precompute_day` in `precompute.py`**

Append to `src/zira_dashboard/precompute.py`:

```python
def precompute_day(day: date, client) -> dict:
    """Compute attribution for one day and UPSERT into production_daily.

    Returns {"day": iso, "rows_written": int}. Idempotent; safe to re-run.
    """
    from . import production_history, stratustime_client
    attribution = production_history.attribution_for(day, client)
    name_to_emp_id = stratustime_client.name_to_emp_id_map()
    rows = flatten_attribution(day, attribution, name_to_emp_id)
    written = upsert_production_daily(rows)
    return {"day": day.isoformat(), "rows_written": written}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_precompute.py::test_precompute_day_flattens_and_upserts -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/precompute.py tests/test_precompute.py
git commit -m "feat(precompute): precompute_day orchestrator"
```

---

## Task 4: Precompute module — range query helpers

These are the SELECTs that every cut-over route will call.

**Files:**
- Modify: `src/zira_dashboard/precompute.py`
- Test: `tests/test_precompute.py`

- [ ] **Step 1: Write failing tests (Postgres-gated)**

Append to `tests/test_precompute.py`:

```python
def _seed(rows):
    from zira_dashboard import db
    db.execute("DELETE FROM production_daily WHERE day BETWEEN %s AND %s",
               (date(2099, 6, 1), date(2099, 6, 30)))
    from zira_dashboard.precompute import upsert_production_daily
    upsert_production_daily(rows)


@pytestmark_pg
def test_sum_by_range_groups_by_name():
    from zira_dashboard import db
    from zira_dashboard.precompute import sum_by_range
    db.init_pool(); db.bootstrap_schema()
    _seed([
        {"day": date(2099, 6, 1), "emp_id": "E1", "name": "Alice", "wc_name": "WC1",
         "units": 10.0, "downtime": 1.0, "hours": 4.0, "days_worked": 1.0},
        {"day": date(2099, 6, 2), "emp_id": "E1", "name": "Alice", "wc_name": "WC1",
         "units": 20.0, "downtime": 2.0, "hours": 4.0, "days_worked": 1.0},
        {"day": date(2099, 6, 1), "emp_id": "E2", "name": "Bob", "wc_name": "WC1",
         "units": 30.0, "downtime": 0.0, "hours": 8.0, "days_worked": 1.0},
    ])

    out = sum_by_range(
        start=date(2099, 6, 1), end=date(2099, 6, 30),
        wc_names=["WC1"], group_by="name",
    )
    # Returned shape: list of dicts, one per name
    by_name = {r["name"]: r for r in out}
    assert float(by_name["Alice"]["units"]) == 30.0
    assert float(by_name["Alice"]["days_worked"]) == 2.0
    assert float(by_name["Bob"]["units"]) == 30.0


@pytestmark_pg
def test_sum_by_name_returns_per_wc_breakdown():
    from zira_dashboard import db
    from zira_dashboard.precompute import sum_by_name
    db.init_pool(); db.bootstrap_schema()
    _seed([
        {"day": date(2099, 6, 1), "emp_id": "E1", "name": "Alice", "wc_name": "WC1",
         "units": 10.0, "downtime": 1.0, "hours": 4.0, "days_worked": 1.0},
        {"day": date(2099, 6, 1), "emp_id": "E1", "name": "Alice", "wc_name": "WC2",
         "units": 5.0,  "downtime": 0.5, "hours": 2.0, "days_worked": 1.0},
    ])

    out = sum_by_name("Alice", start=date(2099, 6, 1), end=date(2099, 6, 30))
    by_wc = {r["wc_name"]: r for r in out}
    assert float(by_wc["WC1"]["units"]) == 10.0
    assert float(by_wc["WC2"]["units"]) == 5.0


@pytestmark_pg
def test_daily_records_in_range_returns_per_row():
    from zira_dashboard import db
    from zira_dashboard.precompute import daily_records_in_range
    db.init_pool(); db.bootstrap_schema()
    _seed([
        {"day": date(2099, 6, 1), "emp_id": "E1", "name": "Alice", "wc_name": "WC1",
         "units": 10.0, "downtime": 1.0, "hours": 4.0, "days_worked": 1.0},
        {"day": date(2099, 6, 2), "emp_id": "E1", "name": "Alice", "wc_name": "WC1",
         "units": 20.0, "downtime": 2.0, "hours": 4.0, "days_worked": 1.0},
    ])

    out = daily_records_in_range(date(2099, 6, 1), date(2099, 6, 30))
    assert len(out) == 2
    out_sorted = sorted(out, key=lambda r: r["day"])
    assert out_sorted[0]["units"] == 10.0
    assert out_sorted[1]["units"] == 20.0
    assert out_sorted[0]["person"] == "Alice"  # match old daily_records key name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_precompute.py -k "sum_by_range or sum_by_name or daily_records_in_range" -v`
Expected: FAILs — functions not defined.

- [ ] **Step 3: Implement the helpers in `precompute.py`**

Append to `src/zira_dashboard/precompute.py`:

```python
def sum_by_range(
    start: date,
    end: date,
    wc_names: list[str] | None = None,
    group_by: str = "name",
) -> list[dict]:
    """Sum units / downtime / hours / days_worked over [start, end]
    grouped by `group_by` (currently only "name").

    `wc_names` filters which WCs to include. None = all WCs.
    """
    from . import db
    if group_by != "name":
        raise ValueError(f"group_by must be 'name'; got {group_by!r}")
    params: list = [start, end]
    sql = """
        SELECT name,
               SUM(units)       AS units,
               SUM(downtime)    AS downtime,
               SUM(hours)       AS hours,
               SUM(days_worked) AS days_worked
        FROM production_daily
        WHERE day BETWEEN %s AND %s
    """
    if wc_names:
        sql += " AND wc_name = ANY(%s)"
        params.append(list(wc_names))
    sql += " GROUP BY name"
    return db.query(sql, params)


def sum_by_name(name: str, start: date, end: date) -> list[dict]:
    """Per-WC totals for one person across [start, end].

    Return rows: {wc_name, units, downtime, hours, days_worked}.
    """
    from . import db
    return db.query(
        """
        SELECT wc_name,
               SUM(units)       AS units,
               SUM(downtime)    AS downtime,
               SUM(hours)       AS hours,
               SUM(days_worked) AS days_worked
        FROM production_daily
        WHERE name = %s AND day BETWEEN %s AND %s
        GROUP BY wc_name
        """,
        (name, start, end),
    )


def daily_records_in_range(start: date, end: date) -> list[dict]:
    """One row per (day, person, wc) in [start, end], matching the shape
    of the existing `production_history.daily_records` so awards/trophy
    code can swap over with no behavior change.

    Each row: {day, person, wc, units, downtime, hours}.
    """
    from . import db
    rows = db.query(
        """
        SELECT day, name AS person, wc_name AS wc,
               units, downtime, hours
        FROM production_daily
        WHERE day BETWEEN %s AND %s AND units > 0
        """,
        (start, end),
    )
    return [
        {
            "day": r["day"],
            "person": r["person"],
            "wc": r["wc"],
            "units": float(r["units"]),
            "downtime": float(r["downtime"]),
            "hours": float(r["hours"]),
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_precompute.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/precompute.py tests/test_precompute.py
git commit -m "feat(precompute): sum_by_range, sum_by_name, daily_records_in_range"
```

---

## Task 5: Admin endpoint — `/admin/precompute-run`

Covers both nightly (default = yesterday) and backfill (with `from`/`to` query params). Auth via `X-Admin-Secret` header matching the `ZIRA_ADMIN_SECRET` env var.

**Files:**
- Modify: `src/zira_dashboard/routes/admin.py`
- Test: `tests/test_admin_precompute.py`

- [ ] **Step 1: Write failing endpoint tests**

Create `tests/test_admin_precompute.py`:

```python
import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ZIRA_ADMIN_SECRET", "test-secret")
    from zira_dashboard.app import app
    return TestClient(app)


def test_precompute_run_rejects_missing_secret(client):
    r = client.post("/admin/precompute-run")
    assert r.status_code == 401


def test_precompute_run_rejects_wrong_secret(client):
    r = client.post("/admin/precompute-run", headers={"X-Admin-Secret": "nope"})
    assert r.status_code == 401


def test_precompute_run_default_does_yesterday(client, monkeypatch):
    calls = []

    def fake_precompute_day(day, client_):
        calls.append(day)
        return {"day": day.isoformat(), "rows_written": 5}

    monkeypatch.setattr(
        "zira_dashboard.precompute.precompute_day", fake_precompute_day
    )

    r = client.post(
        "/admin/precompute-run",
        headers={"X-Admin-Secret": "test-secret"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["days_processed"] == 1
    assert body["rows_written"] == 5
    yesterday = date.today() - timedelta(days=1)
    assert calls == [yesterday]


def test_precompute_run_with_range(client, monkeypatch):
    calls = []

    def fake_precompute_day(day, client_):
        calls.append(day)
        return {"day": day.isoformat(), "rows_written": 3}

    monkeypatch.setattr(
        "zira_dashboard.precompute.precompute_day", fake_precompute_day
    )

    r = client.post(
        "/admin/precompute-run?from=2026-05-01&to=2026-05-03",
        headers={"X-Admin-Secret": "test-secret"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["days_processed"] == 3
    assert body["rows_written"] == 9
    assert calls == [date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)]


def test_precompute_run_continues_on_per_day_error(client, monkeypatch):
    def fake_precompute_day(day, client_):
        if day == date(2026, 5, 2):
            raise RuntimeError("boom")
        return {"day": day.isoformat(), "rows_written": 1}

    monkeypatch.setattr(
        "zira_dashboard.precompute.precompute_day", fake_precompute_day
    )

    r = client.post(
        "/admin/precompute-run?from=2026-05-01&to=2026-05-03",
        headers={"X-Admin-Secret": "test-secret"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["days_processed"] == 3
    assert body["rows_written"] == 2
    assert body["errors"] == [{"day": "2026-05-02", "error": "boom"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_admin_precompute.py -v`
Expected: FAILs — endpoint doesn't exist.

- [ ] **Step 3: Implement the endpoint**

Add to `src/zira_dashboard/routes/admin.py`:

```python
import os
from fastapi import Request


def _check_admin_secret(request: Request) -> bool:
    expected = os.environ.get("ZIRA_ADMIN_SECRET", "")
    if not expected:
        return False
    provided = request.headers.get("X-Admin-Secret", "")
    return provided == expected


@router.post("/admin/precompute-run")
def precompute_run(
    request: Request,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
):
    """Run the production_daily precompute for one or more days.

    Default behavior (no params): precompute yesterday.
    With `from` + `to`: precompute every day in that inclusive range.

    Auth: X-Admin-Secret header must match $ZIRA_ADMIN_SECRET.
    Idempotent — re-running a day overwrites cleanly.

    Designed to be hit by Windows Task Scheduler nightly and by
    operators manually for backfills.
    """
    import time
    from .. import precompute
    from ..deps import client

    if not _check_admin_secret(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    today = datetime.now(timezone.utc).date()
    if from_ or to:
        if not (from_ and to):
            return JSONResponse(
                {"error": "must supply both `from` and `to`, or neither"},
                status_code=400,
            )
        try:
            start_d = date.fromisoformat(from_)
            end_d = date.fromisoformat(to)
        except ValueError:
            return JSONResponse(
                {"error": "from/to must be YYYY-MM-DD"}, status_code=400
            )
    else:
        start_d = end_d = today - timedelta(days=1)

    if end_d < start_d:
        return JSONResponse({"error": "to must be >= from"}, status_code=400)

    started = time.time()
    days_processed = 0
    rows_written = 0
    errors: list[dict] = []

    cursor = start_d
    while cursor <= end_d:
        try:
            result = precompute.precompute_day(cursor, client)
            rows_written += int(result.get("rows_written", 0))
        except Exception as e:
            errors.append({"day": cursor.isoformat(), "error": str(e)[:200]})
        days_processed += 1
        cursor += timedelta(days=1)

    return JSONResponse({
        "from": start_d.isoformat(),
        "to": end_d.isoformat(),
        "days_processed": days_processed,
        "rows_written": rows_written,
        "duration_ms": int((time.time() - started) * 1000),
        "errors": errors,
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_admin_precompute.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/admin.py tests/test_admin_precompute.py
git commit -m "feat(admin): /admin/precompute-run endpoint (nightly + backfill)"
```

---

## Task 6: Cut over `production_history.daily_records` → `production_daily`

This is the highest-leverage cutover: `daily_records` is what awards/trophies and leaderboards call most often, and right now it runs `attribution_for(day, client)` for every day in the range on every request.

**Files:**
- Modify: `src/zira_dashboard/production_history.py:230-276`
- Test: `tests/test_production_history.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_production_history.py`:

```python
import os
from datetime import date as _date

import pytest


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="Postgres test needs DATABASE_URL",
)
def test_daily_records_reads_from_production_daily():
    """daily_records must return rows from production_daily without
    calling production_history.attribution_for at all."""
    from zira_dashboard import db, precompute, production_history

    db.init_pool(); db.bootstrap_schema()
    db.execute("DELETE FROM production_daily WHERE day BETWEEN %s AND %s",
               (_date(2099, 7, 1), _date(2099, 7, 31)))
    precompute.upsert_production_daily([
        {"day": _date(2099, 7, 1), "emp_id": "E1", "name": "Alice",
         "wc_name": "WC1", "units": 10.0, "downtime": 1.0, "hours": 4.0,
         "days_worked": 1.0},
        {"day": _date(2099, 7, 2), "emp_id": "E2", "name": "Bob",
         "wc_name": "WC2", "units": 20.0, "downtime": 2.0, "hours": 8.0,
         "days_worked": 1.0},
    ])

    # Replace attribution_for with a poison pill — if the cutover is
    # wrong and daily_records still calls it, the test blows up.
    def poison(*a, **k):
        raise AssertionError("attribution_for should not be called")

    saved = production_history.attribution_for
    production_history.attribution_for = poison
    try:
        out = production_history.daily_records(
            _date(2099, 7, 1), _date(2099, 7, 31), client=None
        )
    finally:
        production_history.attribution_for = saved

    by_day = {(r["day"], r["person"]): r for r in out}
    assert by_day[(_date(2099, 7, 1), "Alice")]["units"] == 10.0
    assert by_day[(_date(2099, 7, 2), "Bob")]["units"] == 20.0

    db.execute("DELETE FROM production_daily WHERE day BETWEEN %s AND %s",
               (_date(2099, 7, 1), _date(2099, 7, 31)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_production_history.py::test_daily_records_reads_from_production_daily -v`
Expected: FAIL — current impl calls attribution_for.

- [ ] **Step 3: Rewrite `daily_records`**

In `src/zira_dashboard/production_history.py`, replace the existing body of `daily_records` (lines 230-276) with a thin wrapper:

```python
def daily_records(
    start_d: date, end_d: date, client
) -> list[dict]:
    """Return one record per (day, person, wc) where attributed units > 0.

    Now reads from production_daily. The `client` argument is kept for
    signature compatibility with existing callers, but is unused —
    production_daily is the canonical source.
    """
    from . import precompute
    return precompute.daily_records_in_range(start_d, end_d)
```

The legacy implementation (thread-pool + `_records_for`) and its imports can be removed. Leave any other functions in `production_history.py` untouched.

- [ ] **Step 4: Run targeted tests**

Run: `pytest tests/test_production_history.py tests/test_awards.py tests/test_trophies_route.py tests/test_leaderboards_person_days.py -v`
Expected: all PASS. If anything else still calls `attribution_for` directly, those tests pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/production_history.py tests/test_production_history.py
git commit -m "perf(daily_records): read from production_daily instead of recomputing"
```

---

## Task 7: Cut over `production_history.attribution_range` → `production_daily`

`attribution_range` is what the player card and leaderboard routes call. It currently parallel-fetches per-day attribution. Replace it with a single SUM-by-name query and reshape into the legacy `{person: {wc: totals}}` envelope so callers don't change.

**Files:**
- Modify: `src/zira_dashboard/production_history.py:194-228`
- Test: `tests/test_production_history.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_production_history.py`:

```python
@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="Postgres test needs DATABASE_URL",
)
def test_attribution_range_reads_from_production_daily():
    from zira_dashboard import db, precompute, production_history

    db.init_pool(); db.bootstrap_schema()
    db.execute("DELETE FROM production_daily WHERE day BETWEEN %s AND %s",
               (_date(2099, 8, 1), _date(2099, 8, 31)))
    precompute.upsert_production_daily([
        {"day": _date(2099, 8, 1), "emp_id": "E1", "name": "Alice",
         "wc_name": "WC1", "units": 10.0, "downtime": 1.0, "hours": 4.0,
         "days_worked": 1.0},
        {"day": _date(2099, 8, 2), "emp_id": "E1", "name": "Alice",
         "wc_name": "WC1", "units": 5.0,  "downtime": 0.5, "hours": 2.0,
         "days_worked": 1.0},
    ])

    def poison(*a, **k):
        raise AssertionError("attribution_for should not be called")
    saved = production_history.attribution_for
    production_history.attribution_for = poison
    try:
        out = production_history.attribution_range(
            _date(2099, 8, 1), _date(2099, 8, 31), client=None
        )
    finally:
        production_history.attribution_for = saved

    assert out["Alice"]["WC1"]["units"] == 15.0
    assert out["Alice"]["WC1"]["hours"] == 6.0
    assert out["Alice"]["WC1"]["days_worked"] == 2.0

    db.execute("DELETE FROM production_daily WHERE day BETWEEN %s AND %s",
               (_date(2099, 8, 1), _date(2099, 8, 31)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_production_history.py::test_attribution_range_reads_from_production_daily -v`
Expected: FAIL.

- [ ] **Step 3: Rewrite `attribution_range`**

In `src/zira_dashboard/production_history.py`, replace the body of `attribution_range`:

```python
def attribution_range(
    start: date,
    end: date,
    client,
) -> dict[str, dict[str, dict[str, float]]]:
    """Sum attribution across [start, end] inclusive.

    Reads from production_daily and reshapes into the legacy
    {person: {wc: {units, downtime, hours, days_worked}}} envelope so
    that existing callers (player cards, leaderboards via rank_by_category)
    don't have to change.

    `client` is kept for signature compatibility but unused.
    """
    from . import db
    rows = db.query(
        """
        SELECT name,
               wc_name,
               SUM(units)       AS units,
               SUM(downtime)    AS downtime,
               SUM(hours)       AS hours,
               SUM(days_worked) AS days_worked
        FROM production_daily
        WHERE day BETWEEN %s AND %s
        GROUP BY name, wc_name
        """,
        (start, end),
    )
    out: dict[str, dict[str, dict[str, float]]] = {}
    for r in rows:
        out.setdefault(r["name"], {})[r["wc_name"]] = {
            "units":       float(r["units"]),
            "downtime":    float(r["downtime"]),
            "hours":       float(r["hours"]),
            "days_worked": float(r["days_worked"]),
        }
    return out
```

- [ ] **Step 4: Add a failing test for `attribution_per_day`**

`attribution_per_day` is used by `routes/leaderboards.py` (single-day top rows) and `routes/people.py` (per-day player-card breakdown). Same hot-path problem; same fix.

Append to `tests/test_production_history.py`:

```python
@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="Postgres test needs DATABASE_URL",
)
def test_attribution_per_day_reads_from_production_daily():
    from zira_dashboard import db, precompute, production_history

    db.init_pool(); db.bootstrap_schema()
    db.execute("DELETE FROM production_daily WHERE day BETWEEN %s AND %s",
               (_date(2099, 9, 1), _date(2099, 9, 30)))
    precompute.upsert_production_daily([
        {"day": _date(2099, 9, 1), "emp_id": "E1", "name": "Alice",
         "wc_name": "WC1", "units": 10.0, "downtime": 1.0, "hours": 4.0,
         "days_worked": 1.0},
        {"day": _date(2099, 9, 2), "emp_id": "E1", "name": "Alice",
         "wc_name": "WC1", "units": 5.0,  "downtime": 0.0, "hours": 2.0,
         "days_worked": 1.0},
        {"day": _date(2099, 9, 1), "emp_id": "E2", "name": "Bob",
         "wc_name": "WC2", "units": 20.0, "downtime": 0.0, "hours": 8.0,
         "days_worked": 1.0},
    ])

    def poison(*a, **k):
        raise AssertionError("attribution_for should not be called")
    saved = production_history.attribution_for
    production_history.attribution_for = poison
    try:
        out = production_history.attribution_per_day(
            _date(2099, 9, 1), _date(2099, 9, 30), client=None
        )
    finally:
        production_history.attribution_for = saved

    by_day = dict(out)
    assert by_day[_date(2099, 9, 1)]["Alice"]["WC1"]["units"] == 10.0
    assert by_day[_date(2099, 9, 1)]["Bob"]["WC2"]["units"] == 20.0
    assert by_day[_date(2099, 9, 2)]["Alice"]["WC1"]["units"] == 5.0
    # Every day in range present (even empty days), so callers can
    # distinguish "checked and empty" from "didn't check".
    assert len(out) == 30

    db.execute("DELETE FROM production_daily WHERE day BETWEEN %s AND %s",
               (_date(2099, 9, 1), _date(2099, 9, 30)))
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_production_history.py::test_attribution_per_day_reads_from_production_daily -v`
Expected: FAIL.

- [ ] **Step 6: Rewrite `attribution_per_day`**

In `src/zira_dashboard/production_history.py`, replace the body of `attribution_per_day`:

```python
def attribution_per_day(
    start: date,
    end: date,
    client,
) -> list[tuple[date, dict[str, dict[str, dict[str, float]]]]]:
    """Per-day attribution across [start, end] inclusive.

    Returns one (day, attribution_dict) tuple per day in the range,
    in date-ascending order. Empty days return ({}). `client` is kept
    for signature compatibility but unused — reads from production_daily.
    """
    from datetime import timedelta
    from . import db

    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    if not days:
        return []

    rows = db.query(
        """
        SELECT day, name, wc_name,
               units, downtime, hours, days_worked
        FROM production_daily
        WHERE day BETWEEN %s AND %s
        """,
        (start, end),
    )
    by_day: dict[date, dict[str, dict[str, dict[str, float]]]] = {d: {} for d in days}
    for r in rows:
        person_map = by_day[r["day"]].setdefault(r["name"], {})
        person_map[r["wc_name"]] = {
            "units":       float(r["units"]),
            "downtime":    float(r["downtime"]),
            "hours":       float(r["hours"]),
            "days_worked": float(r["days_worked"]),
        }
    return [(d, by_day[d]) for d in days]
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_production_history.py tests/test_player_card_stats.py tests/test_leaderboards_avg.py tests/test_leaderboards_person_days.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/zira_dashboard/production_history.py tests/test_production_history.py
git commit -m "perf(attribution_range,per_day): read from production_daily"
```

---

## Task 8: Verify leaderboards / awards / trophies / player-card / value-streams routes are fast

Tasks 6 + 7 made the slow paths fast because every dependent function (`rank_by_category`, `_rank_single_day`, `apply_overrides`, player-card breakdown SQL) builds on top of `daily_records` or `attribution_range`. This task is a smoke check + a CHANGELOG entry, not new code.

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: all PASS. Investigate and fix any regression before continuing — do not move on if anything fails.

- [ ] **Step 2: Smoke-test the affected pages manually**

Boot the app locally (`python -m zira_dashboard` or however it's normally run). With an empty `production_daily` table, the affected pages should render but show no data. Hit:

- `/leaderboards`
- `/staffing/people/<name>` (any roster name)
- `/trophies`
- `/recycling`
- `/new-vs`

Each should return 200 with empty/zero data. (Backfill comes in Task 13 — for now we just want to confirm no crashes when the table is empty.)

- [ ] **Step 3: Add CHANGELOG entry**

Insert a new `### TIME` block under today's date in `CHANGELOG.md`:

```markdown
### <HH:MM AM/PM>

- **Backend speedup — daily-OK pages now read from a precomputed fact table** — leaderboards, player cards, trophies/awards, and value-stream production views previously recomputed per-person attribution from raw Zira on every page hit. They now read from a new `production_daily` table populated by `/admin/precompute-run` (nightly + backfill). The user-visible win lands once the table is backfilled (Task 13). Surface APIs and templates unchanged.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): daily-OK page reads now go through production_daily"
```

---

## Task 9: Live cache module — read/write helpers

**Files:**
- Create: `src/zira_dashboard/live_cache.py`
- Test: `tests/test_live_cache.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_live_cache.py`:

```python
import os
from datetime import date, datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="Postgres tests need DATABASE_URL",
)


def _reset_caches():
    from zira_dashboard import db
    db.execute("DELETE FROM today_attendance_cache")
    db.execute("DELETE FROM today_timeoff_cache")
    db.execute("DELETE FROM today_production_cache")


def test_write_then_read_attendance():
    from zira_dashboard import db, live_cache
    db.init_pool(); db.bootstrap_schema(); _reset_caches()

    payload = {"some": "data", "list": [1, 2, 3]}
    live_cache.write_attendance(date(2099, 9, 1), payload)
    got, refreshed_at = live_cache.read_attendance(date(2099, 9, 1))
    assert got == payload
    assert refreshed_at is not None


def test_read_missing_returns_none():
    from zira_dashboard import db, live_cache
    db.init_pool(); db.bootstrap_schema(); _reset_caches()
    got, refreshed_at = live_cache.read_attendance(date(2099, 9, 2))
    assert got is None
    assert refreshed_at is None


def test_write_then_overwrite_attendance():
    from zira_dashboard import db, live_cache
    db.init_pool(); db.bootstrap_schema(); _reset_caches()

    live_cache.write_attendance(date(2099, 9, 3), {"v": 1})
    live_cache.write_attendance(date(2099, 9, 3), {"v": 2})
    got, _ = live_cache.read_attendance(date(2099, 9, 3))
    assert got == {"v": 2}


def test_is_stale_threshold():
    from zira_dashboard import live_cache
    fresh = datetime.now(timezone.utc) - timedelta(seconds=30)
    stale = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert live_cache.is_stale(fresh) is False
    assert live_cache.is_stale(stale) is True
    assert live_cache.is_stale(None) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_live_cache.py -v`
Expected: FAILs — module doesn't exist.

- [ ] **Step 3: Create `src/zira_dashboard/live_cache.py`**

```python
"""Live cache for today's StratusTime + Odoo data.

Owns three single-row JSONB tables (today_attendance_cache,
today_timeoff_cache, today_production_cache). The warmer (in app.py)
overwrites them every 45 s. Live routes read through this module
instead of calling the external APIs in the request path.

The `is_stale` helper supports the cold-start safety valve: if a route
reads a cache row whose refreshed_at is older than ~3 minutes, it can
trigger an inline refresh before returning.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

STALE_THRESHOLD = timedelta(minutes=3)


def _write(table: str, day: date, payload: Any) -> None:
    from . import db
    db.execute(
        f"""
        INSERT INTO {table} (day, payload, refreshed_at)
        VALUES (%s, %s::jsonb, now())
        ON CONFLICT (day) DO UPDATE SET
          payload = EXCLUDED.payload,
          refreshed_at = now()
        """,
        (day, json.dumps(payload, default=str)),
    )


def _read(table: str, day: date) -> tuple[Any | None, datetime | None]:
    from . import db
    rows = db.query(
        f"SELECT payload, refreshed_at FROM {table} WHERE day = %s",
        (day,),
    )
    if not rows:
        return (None, None)
    return (rows[0]["payload"], rows[0]["refreshed_at"])


def write_attendance(day: date, payload: Any) -> None:
    _write("today_attendance_cache", day, payload)


def read_attendance(day: date) -> tuple[Any | None, datetime | None]:
    return _read("today_attendance_cache", day)


def write_timeoff(day: date, payload: Any) -> None:
    _write("today_timeoff_cache", day, payload)


def read_timeoff(day: date) -> tuple[Any | None, datetime | None]:
    return _read("today_timeoff_cache", day)


def write_production(day: date, payload: Any) -> None:
    _write("today_production_cache", day, payload)


def read_production(day: date) -> tuple[Any | None, datetime | None]:
    return _read("today_production_cache", day)


def is_stale(refreshed_at: datetime | None) -> bool:
    """True if the row is missing or older than STALE_THRESHOLD."""
    if refreshed_at is None:
        return True
    return (datetime.now(timezone.utc) - refreshed_at) > STALE_THRESHOLD
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_live_cache.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/live_cache.py tests/test_live_cache.py
git commit -m "feat(live_cache): JSONB cache helpers for today's StratusTime/Odoo data"
```

---

## Task 10: Live cache refresh functions

The functions that the warmer (and the cold-start path) actually call. Each pulls from the real source and writes to its cache table.

**Files:**
- Modify: `src/zira_dashboard/live_cache.py`
- Test: `tests/test_live_cache.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_live_cache.py`:

```python
def test_refresh_attendance_calls_stratustime_and_writes_cache(monkeypatch):
    from zira_dashboard import db, live_cache
    db.init_pool(); db.bootstrap_schema(); _reset_caches()

    called = {}

    def fake_attendance(day):
        called["day"] = day
        return {"E1": {"status": "no_punch"}}

    monkeypatch.setattr(
        "zira_dashboard.stratustime_client.attendance_for_day", fake_attendance
    )

    live_cache.refresh_attendance(date(2099, 9, 4))
    got, _ = live_cache.read_attendance(date(2099, 9, 4))
    assert called["day"] == date(2099, 9, 4)
    assert got == {"E1": {"status": "no_punch"}}


def test_refresh_attendance_swallows_errors(monkeypatch):
    from zira_dashboard import db, live_cache
    db.init_pool(); db.bootstrap_schema(); _reset_caches()

    def boom(day):
        raise RuntimeError("stratustime down")

    monkeypatch.setattr(
        "zira_dashboard.stratustime_client.attendance_for_day", boom
    )

    # Must not raise — warmer relies on this.
    live_cache.refresh_attendance(date(2099, 9, 5))
    # No row was written (the failure happened before write).
    got, _ = live_cache.read_attendance(date(2099, 9, 5))
    assert got is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_live_cache.py::test_refresh_attendance_calls_stratustime_and_writes_cache tests/test_live_cache.py::test_refresh_attendance_swallows_errors -v`
Expected: FAIL.

- [ ] **Step 3: Implement refresh functions in `live_cache.py`**

Append to `src/zira_dashboard/live_cache.py`:

```python
import logging

_log = logging.getLogger(__name__)


def refresh_attendance(day: date) -> None:
    """Pull today's StratusTime attendance, write to cache.

    Errors are logged and swallowed — the warmer keeps running and the
    previous good payload (if any) remains in the cache table."""
    try:
        from . import stratustime_client
        payload = stratustime_client.attendance_for_day(day)
        write_attendance(day, payload)
    except Exception as e:
        _log.warning("refresh_attendance(%s) failed: %s", day, e)


def refresh_timeoff(day: date) -> None:
    """Pull today's StratusTime time-off entries, write to cache."""
    try:
        from . import stratustime_client
        payload = stratustime_client.time_off_entries_for_day(day)
        write_timeoff(day, payload)
    except Exception as e:
        _log.warning("refresh_timeoff(%s) failed: %s", day, e)


def refresh_production(day: date, client) -> None:
    """Refresh today's Zira production AND today's production_daily rows.

    The cache table holds the raw payload (used by the recycling/new-vs
    pages); production_daily rows are written so MTD / today leaderboards
    see today's partial-day data without a separate query path.
    """
    try:
        from . import precompute
        # Side effect: also UPSERTs today's production_daily rows because
        # precompute_day calls attribution_for(day) + flatten + upsert.
        result = precompute.precompute_day(day, client)
        write_production(day, result)
    except Exception as e:
        _log.warning("refresh_production(%s) failed: %s", day, e)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_live_cache.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/live_cache.py tests/test_live_cache.py
git commit -m "feat(live_cache): refresh_attendance, refresh_timeoff, refresh_production"
```

---

## Task 11: Wire the warmer into the FastAPI lifespan

**Files:**
- Modify: `src/zira_dashboard/app.py:44-95, 132-156`

- [ ] **Step 1: Add the new warmer loop in `app.py`**

After `_warm_zira_cache_loop` (around line 94), add a new warmer loop:

```python
async def _warm_live_cache_loop():
    """Refresh today's attendance, time-off, and production into the
    live_cache tables every 45 s. Each source is wrapped independently so
    one outage doesn't block the others. The loop itself never raises —
    a hard failure logs and the next tick tries again."""
    from . import live_cache
    while True:
        try:
            today = datetime.now(timezone.utc).date()
            await asyncio.to_thread(live_cache.refresh_attendance, today)
            await asyncio.to_thread(live_cache.refresh_timeoff, today)
            await asyncio.to_thread(
                live_cache.refresh_production, today, _zira_client()
            )
        except Exception as e:  # noqa: BLE001 — warmer must never die
            _log.warning("live_cache warmer tick failed: %s", e)
        await asyncio.sleep(45)
```

In the `lifespan` function (around line 132), add the new task to startup and shutdown:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_pool()
    db.bootstrap_schema()
    _prewarm_stratustime()
    warmer_task = asyncio.create_task(_warm_zira_cache_loop())
    st_warmer_task = asyncio.create_task(_warm_stratustime_loop())
    live_cache_task = asyncio.create_task(_warm_live_cache_loop())
    try:
        yield
    finally:
        for t in (warmer_task, st_warmer_task, live_cache_task):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        db.shutdown_pool()
```

- [ ] **Step 2: Boot the app locally and verify the warmer runs**

Run the app for ~90 s. Check the logs — no warmer crash, and at least one `live_cache` refresh tick should have completed silently (success) or logged a swallowed failure (no DB / no creds).

- [ ] **Step 3: Verify cache rows appear**

After ~60 s of the app running with a live DATABASE_URL and live StratusTime creds:

```bash
psql "$DATABASE_URL" -c "SELECT day, refreshed_at FROM today_attendance_cache;"
```

Should show one row with `refreshed_at` within the last minute.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/app.py
git commit -m "feat(app): wire live_cache warmer into FastAPI lifespan"
```

---

## Task 12: Cut over `/api/late-report` to read from `live_cache`

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py` — find the `late_report_json` handler (line ~875, the `/api/late-report` GET handler)

- [ ] **Step 1: Read the current implementation**

Open `src/zira_dashboard/routes/staffing.py` around line 875 (`late_report_json` and `_safe_attendance` helper). Identify every place it calls `stratustime_client.attendance_for_day` or `time_off_entries_for_day` directly.

- [ ] **Step 2: Add a thin live-cache helper inside the staffing module**

Near the top of `src/zira_dashboard/routes/staffing.py` (after the imports), add:

```python
def _attendance_with_fallback(day):
    """Return today's attendance from the live cache. If the warmer
    hasn't refreshed in >3 min, do an inline refresh and re-read.

    Routes call this instead of stratustime_client.attendance_for_day."""
    from .. import live_cache, stratustime_client
    payload, refreshed_at = live_cache.read_attendance(day)
    if payload is None or live_cache.is_stale(refreshed_at):
        try:
            live_cache.refresh_attendance(day)
            payload, _ = live_cache.read_attendance(day)
        except Exception:
            pass
        if payload is None:
            # Final fallback: call the source directly.
            return stratustime_client.attendance_for_day(day)
    return payload


def _timeoff_with_fallback(day):
    from .. import live_cache, stratustime_client
    payload, refreshed_at = live_cache.read_timeoff(day)
    if payload is None or live_cache.is_stale(refreshed_at):
        try:
            live_cache.refresh_timeoff(day)
            payload, _ = live_cache.read_timeoff(day)
        except Exception:
            pass
        if payload is None:
            return stratustime_client.time_off_entries_for_day(day)
    return payload
```

- [ ] **Step 3: Swap calls in `late_report_json` and `_safe_attendance`**

In every place inside `late_report_json` (and any helpers it calls *for today's day only*) that reads `stratustime_client.attendance_for_day(today)`, replace with `_attendance_with_fallback(today)`. Same for `time_off_entries_for_day(today)` → `_timeoff_with_fallback(today)`. Leave any historical-day calls alone (they aren't in the live cache).

- [ ] **Step 4: Smoke test**

Boot the app, open `/api/late-report` in a browser. Should return JSON with the same structure as before. The first call may be slow (cold-start safety valve does inline refresh); subsequent calls should return in well under 50 ms.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py
git commit -m "perf(late-report): read today's attendance + time-off from live cache"
```

---

## Task 13: Cut over `/staffing/{today}` to read from `live_cache`

This is the user-facing scheduler page. Same shape as Task 12 but applied to the staffing day-view handler.

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py` — the `/staffing/{day}` GET handler

- [ ] **Step 1: Locate the day-view handler**

In `src/zira_dashboard/routes/staffing.py`, find the `@router.get("/staffing/{day}")` (or equivalent root staffing handler). It calls `stratustime_client.attendance_for_day`, `time_off_entries_for_day`, and possibly Zira-today functions directly when `day == today`.

- [ ] **Step 2: Add today-vs-history branching**

Inside the handler, before any external-API calls, compute `today = datetime.now(timezone.utc).date()` and `is_today = (day == today)`. Replace each external-API call with:

```python
if is_today:
    attendance = _attendance_with_fallback(day)
    time_off = _timeoff_with_fallback(day)
else:
    attendance = stratustime_client.attendance_for_day(day)
    time_off = stratustime_client.time_off_entries_for_day(day)
```

(Past days aren't covered by the live cache — they're a different problem. This plan leaves them on the existing slow path.)

- [ ] **Step 3: Run staffing tests**

Run: `pytest tests/test_late_report.py tests/test_staffing_custom_hours.py -v`
Expected: all PASS.

- [ ] **Step 4: Smoke-test the page**

Boot the app, open `/staffing/<today>`. Should render correctly. Refreshing repeatedly should feel near-instant after the first hit.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py
git commit -m "perf(staffing): today's view reads attendance + time-off from live cache"
```

---

## Task 14: Backfill production_daily for the last 12 months

**Files:**
- None (data-only step)

- [ ] **Step 1: Confirm `ZIRA_ADMIN_SECRET` is set in the runtime environment**

If not, set it before deploying — pick a long random value and stash it in the production env. The endpoint returns 401 if the env var is unset.

- [ ] **Step 2: Deploy the code from Tasks 1-13**

Push to main; the production deploy runs `bootstrap_schema()` at startup, creating all four new tables.

- [ ] **Step 3: Run the backfill in 90-day chunks**

Today's date is `2026-05-11`. The trophy system uses up to ~12 months of data, so backfill from `2025-05-11` to today. The endpoint has no internal day cap (unlike `/admin/zira-backfill`), but chunks let you watch progress and re-run any failed chunk easily.

```bash
curl -X POST -H "X-Admin-Secret: $ZIRA_ADMIN_SECRET" \
  "$APP_URL/admin/precompute-run?from=2025-05-11&to=2025-08-08"
curl -X POST -H "X-Admin-Secret: $ZIRA_ADMIN_SECRET" \
  "$APP_URL/admin/precompute-run?from=2025-08-09&to=2025-11-06"
curl -X POST -H "X-Admin-Secret: $ZIRA_ADMIN_SECRET" \
  "$APP_URL/admin/precompute-run?from=2025-11-07&to=2026-02-04"
curl -X POST -H "X-Admin-Secret: $ZIRA_ADMIN_SECRET" \
  "$APP_URL/admin/precompute-run?from=2026-02-05&to=2026-05-10"
```

Each call returns a JSON body with `days_processed`, `rows_written`, `duration_ms`, `errors`. Re-run any chunk that has non-empty `errors`.

- [ ] **Step 4: Verify the table is populated**

```bash
psql "$DATABASE_URL" -c "SELECT MIN(day), MAX(day), COUNT(*) FROM production_daily;"
```

Should show a row count in the tens-of-thousands range (roughly: # people × # WCs × # working days).

- [ ] **Step 5: Smoke-test affected pages**

Open `/leaderboards`, `/trophies`, `/staffing/people/<name>`, `/recycling`, `/new-vs`. All should now show populated data and feel snappy.

- [ ] **Step 6: No commit** — this is a runtime operation, not a code change.

---

## Task 15: Schedule the nightly job + CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Configure Windows Task Scheduler**

On the host machine, create a Scheduled Task:

- **Name:** Zira nightly precompute
- **Trigger:** Daily at 3:30 AM
- **Action:** Start a program
  - Program: `powershell.exe`
  - Arguments: `-Command "Invoke-WebRequest -Method POST -Uri '$APP_URL/admin/precompute-run' -Headers @{'X-Admin-Secret'='$ZIRA_ADMIN_SECRET'} -UseBasicParsing | Select-Object -ExpandProperty Content | Out-File -FilePath 'C:\logs\precompute-$(Get-Date -Format yyyy-MM-dd).log' -Encoding utf8"`
- **Run whether user is logged on or not:** yes

Substitute the real values for `$APP_URL` and `$ZIRA_ADMIN_SECRET` (Task Scheduler doesn't expand env vars in arguments; bake them in or wrap with a `.ps1` script that reads them).

- [ ] **Step 2: Trigger one manual run to verify the task works end-to-end**

Right-click the task → Run. After it completes, check the log file in `C:\logs\` for a 200 response with non-zero `days_processed`.

- [ ] **Step 3: Add CHANGELOG entry**

Insert a new `### TIME` block under today's date in `CHANGELOG.md`:

```markdown
### <HH:MM AM/PM>

- **Live pages now read from a 45-second background cache; nightly precompute now scheduled** — the plant scheduler and late/absent report no longer block on StratusTime calls in the request path; both read from the new `today_*_cache` tables refreshed every 45 s by an in-process warmer. The nightly `/admin/precompute-run` job is now scheduled in Windows Task Scheduler at 3:30 AM, keeping `production_daily` fresh for leaderboards, player cards, trophies, and value-stream views. End-to-end effect: every page that used to wait on Odoo/StratusTime is now a Postgres SELECT in the hot path.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): live warmer + nightly precompute scheduled"
```

- [ ] **Step 5: Push**

```bash
git push origin main
```

---

## Done

All daily-OK page reads go through `production_daily` (single SUM/GROUP BY). All live page reads go through `today_*_cache` (~45 s freshness). Odoo and StratusTime are no longer in the request path for any user-facing route covered by this plan.

If a future page is added that needs historical data, point it at `precompute.sum_by_range` / `precompute.sum_by_name` / `precompute.daily_records_in_range`. If a future page is added that needs today's data, point it at `live_cache.read_attendance` / `read_timeoff` / `read_production` with the same cold-start fallback pattern shown in Task 12.
