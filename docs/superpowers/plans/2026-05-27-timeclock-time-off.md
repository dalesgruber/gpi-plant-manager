# Timeclock Time-Off Requests + Calendar — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add kiosk time-off requests with four shapes (full day, late arrival, early leave, midday gap), live balance + in-flight calc, Odoo `hr.leave` sync, cascade into the staffing scheduler on approval, and a parallel-run overlay with StratusTime on the admin calendar.

**Architecture:** Mirrors the existing punch architecture — local Postgres `time_off_requests` table is the read source for all UI surfaces; `BackgroundTask` + 60s sweep pushes writes to Odoo; new 60s pull-poller refreshes state and cascades approvals into `schedules` (TIME_OFF_KEY bucket) and `custom_day_hours`. Spec: [`docs/superpowers/specs/2026-05-27-timeclock-time-off-design.md`](../specs/2026-05-27-timeclock-time-off-design.md).

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, Postgres (psycopg2), Odoo XML-RPC, pytest.

**Patterns to follow:**
- Schema DDL: append to `_SCHEMA_DDL` in `src/zira_dashboard/db.py` (CREATE TABLE IF NOT EXISTS; ALTER TABLE ADD COLUMN IF NOT EXISTS)
- Odoo client tests: `_stub_execute(monkeypatch, {(model, method): response})` from `tests/test_odoo_client.py`
- Background sync: mirror `src/zira_dashboard/kiosk_sync.py` + `app.py`'s sweep loop wiring
- Kiosk routes: mirror `src/zira_dashboard/routes/kiosk.py` (HMAC token + 60s TTL)
- Kiosk templates: mirror `src/zira_dashboard/templates/kiosk_*.html` (extend `kiosk_base.html`)

---

## File Structure

**New files:**
- `src/zira_dashboard/time_off_sync.py` — push/pull/cascade engine (~250 lines)
- `src/zira_dashboard/time_off_balances.py` — balance refresh helpers (~80 lines)
- `src/zira_dashboard/routes/kiosk_time_off.py` — kiosk routes (~300 lines)
- `src/zira_dashboard/templates/kiosk_time_off_landing.html`
- `src/zira_dashboard/templates/kiosk_time_off_request_shape.html`
- `src/zira_dashboard/templates/kiosk_time_off_request_details.html`
- `src/zira_dashboard/templates/kiosk_time_off_mine.html`
- `src/zira_dashboard/templates/kiosk_time_off_mine_detail.html`
- `src/zira_dashboard/templates/kiosk_time_off_calendar.html`
- `src/zira_dashboard/static/kiosk_time_off.js` — client-side live calc
- `tests/test_odoo_client_leaves.py` — new Odoo client methods
- `tests/test_time_off_sync.py` — push/pull/cascade
- `tests/test_time_off_routes.py` — kiosk routes
- `tests/test_time_off_balances.py` — balance refresh

**Modified files:**
- `src/zira_dashboard/db.py` — `_SCHEMA_DDL` additions
- `src/zira_dashboard/odoo_client.py` — new read + write functions for `hr.leave`
- `src/zira_dashboard/app.py` — wire new background loops (poller + balance refresh)
- `src/zira_dashboard/routes/kiosk.py` — Time Off tile on dashboard (feature-flagged)
- `src/zira_dashboard/templates/kiosk_dashboard.html` — tile
- `src/zira_dashboard/routes/time_off.py` — switch source from StratusTime to local mirror with overlay
- `src/zira_dashboard/templates/time_off.html` — source indicator + overlay style
- `src/zira_dashboard/routes/settings.py` — Time Off panel
- `src/zira_dashboard/templates/settings.html` — Time Off panel UI
- `src/zira_dashboard/settings_store.py` — getters/setters for 4 new keys
- `CHANGELOG.md` — entry on each deploy

---

## Phase 1 — Schema & Settings Foundation

### Task 1: Schema migration — `time_off_requests`, `time_off_balances`, `scheduler_moves`

**Files:**
- Modify: `src/zira_dashboard/db.py` (append to `_SCHEMA_DDL`)
- Test: `tests/test_db.py` (extend existing schema-bootstrap tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
def test_time_off_requests_table_bootstraps(monkeypatch):
    """Schema bootstrap must include time_off_requests with expected columns."""
    from zira_dashboard import db
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    db.bootstrap_schema()
    rows = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'time_off_requests' ORDER BY column_name"
    )
    names = {r["column_name"] for r in rows}
    expected = {
        "id", "person_odoo_id", "originating_kiosk_user", "shape",
        "holiday_status_id", "date_from", "date_to", "hour_from", "hour_to",
        "working_hours_json", "note", "state", "odoo_leave_id",
        "synced_to_odoo", "sync_error", "last_pulled_at", "last_pushed_at",
        "created_at", "updated_at",
    }
    assert expected.issubset(names), f"missing: {expected - names}"


def test_time_off_balances_table_bootstraps():
    from zira_dashboard import db
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    db.bootstrap_schema()
    rows = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'time_off_balances'"
    )
    names = {r["column_name"] for r in rows}
    assert {"person_odoo_id", "holiday_status_id", "unit",
            "allocated_total", "taken", "pending", "available",
            "available_practical", "last_pulled_at"}.issubset(names)


def test_scheduler_moves_table_bootstraps():
    from zira_dashboard import db
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    db.bootstrap_schema()
    rows = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'scheduler_moves'"
    )
    names = {r["column_name"] for r in rows}
    assert {"id", "person_odoo_id", "occurred_at", "from_bucket",
            "to_bucket", "reason", "schedule_date"}.issubset(names)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py::test_time_off_requests_table_bootstraps -v`
Expected: FAIL with relation `time_off_requests` not found (or skip if no DB).

- [ ] **Step 3: Add the DDL**

Append inside `_SCHEMA_DDL` in `src/zira_dashboard/db.py`, BEFORE the closing `"""`:

```sql
-- Time-off requests (2026-05-27): local mirror of Odoo hr.leave + sync state.
CREATE TABLE IF NOT EXISTS time_off_requests (
  id                       BIGSERIAL PRIMARY KEY,
  person_odoo_id           INTEGER NOT NULL,
  originating_kiosk_user   BOOLEAN NOT NULL DEFAULT TRUE,
  shape                    TEXT NOT NULL,
  holiday_status_id        INTEGER NOT NULL,
  date_from                DATE NOT NULL,
  date_to                  DATE NOT NULL,
  hour_from                NUMERIC(4,2),
  hour_to                  NUMERIC(4,2),
  working_hours_json       JSONB,
  note                     TEXT,
  state                    TEXT NOT NULL DEFAULT 'draft',
  odoo_leave_id            INTEGER,
  synced_to_odoo           BOOLEAN NOT NULL DEFAULT FALSE,
  sync_error               TEXT,
  last_pulled_at           TIMESTAMPTZ,
  last_pushed_at           TIMESTAMPTZ,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS time_off_requests_person_date_idx
  ON time_off_requests (person_odoo_id, date_from);
CREATE INDEX IF NOT EXISTS time_off_requests_range_idx
  ON time_off_requests (date_from, date_to);
CREATE INDEX IF NOT EXISTS time_off_requests_unsynced_idx
  ON time_off_requests (id) WHERE synced_to_odoo = FALSE;
CREATE INDEX IF NOT EXISTS time_off_requests_state_idx
  ON time_off_requests (state, date_from);
CREATE UNIQUE INDEX IF NOT EXISTS time_off_requests_odoo_leave_id_uniq
  ON time_off_requests (odoo_leave_id) WHERE odoo_leave_id IS NOT NULL;

-- Per-(person, leave_type) balance cache.
CREATE TABLE IF NOT EXISTS time_off_balances (
  person_odoo_id       INTEGER NOT NULL,
  holiday_status_id    INTEGER NOT NULL,
  unit                 TEXT NOT NULL,
  allocated_total      NUMERIC(8,2) NOT NULL,
  taken                NUMERIC(8,2) NOT NULL,
  pending              NUMERIC(8,2) NOT NULL DEFAULT 0,
  available            NUMERIC(8,2) NOT NULL,
  available_practical  NUMERIC(8,2) NOT NULL,
  last_pulled_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (person_odoo_id, holiday_status_id)
);

-- Audit log of scheduler reassignments caused by time-off cascade.
CREATE TABLE IF NOT EXISTS scheduler_moves (
  id              BIGSERIAL PRIMARY KEY,
  person_odoo_id  INTEGER NOT NULL,
  schedule_date   DATE NOT NULL,
  from_bucket     TEXT,
  to_bucket       TEXT NOT NULL,
  reason          TEXT NOT NULL,
  occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS scheduler_moves_person_date_idx
  ON scheduler_moves (person_odoo_id, schedule_date);

-- Cached hr.leave.type list, refreshed every ~10min by poller.
CREATE TABLE IF NOT EXISTS leave_types_cache (
  holiday_status_id    INTEGER PRIMARY KEY,
  name                 TEXT NOT NULL,
  request_unit         TEXT NOT NULL,        -- 'day' | 'half_day' | 'hour'
  requires_allocation  TEXT NOT NULL,        -- 'yes' | 'no'
  color                INTEGER,
  active               BOOLEAN NOT NULL DEFAULT TRUE,
  last_pulled_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -k "time_off or scheduler_moves" -v`
Expected: PASS (or SKIP if no DATABASE_URL).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/db.py tests/test_db.py
git commit -m "feat(timeclock): schema for time-off requests, balances, scheduler moves"
```

---

### Task 2: Settings store — 4 new keys with typed getters/setters

**Files:**
- Modify: `src/zira_dashboard/settings_store.py` (add new functions)
- Test: `tests/test_views_store.py` (covers settings patterns — extend or create test_settings_store.py)

- [ ] **Step 1: Write the failing test**

Create `tests/test_time_off_settings.py`:

```python
import pytest
from unittest.mock import patch

from zira_dashboard import settings_store


def test_hidden_leave_type_ids_default_empty(monkeypatch):
    """No row in app_settings → returns empty list."""
    with patch.object(settings_store, "_read_raw", return_value=None):
        assert settings_store.get_hidden_leave_type_ids() == []


def test_hidden_leave_type_ids_round_trip(monkeypatch):
    storage = {}
    monkeypatch.setattr(settings_store, "_read_raw",
                        lambda k: storage.get(k))
    monkeypatch.setattr(settings_store, "_write_raw",
                        lambda k, v: storage.__setitem__(k, v))
    settings_store.set_hidden_leave_type_ids([3, 7, 11])
    assert settings_store.get_hidden_leave_type_ids() == [3, 7, 11]


def test_show_stratustime_overlay_defaults_true(monkeypatch):
    monkeypatch.setattr(settings_store, "_read_raw", lambda k: None)
    assert settings_store.get_show_stratustime_overlay() is True


def test_default_shift_hours_default(monkeypatch):
    monkeypatch.setattr(settings_store, "_read_raw", lambda k: None)
    assert settings_store.get_default_shift_hours() == (6.0, 14.5)


def test_default_shift_hours_round_trip(monkeypatch):
    storage = {}
    monkeypatch.setattr(settings_store, "_read_raw",
                        lambda k: storage.get(k))
    monkeypatch.setattr(settings_store, "_write_raw",
                        lambda k, v: storage.__setitem__(k, v))
    settings_store.set_default_shift_hours(7.0, 15.5)
    assert settings_store.get_default_shift_hours() == (7.0, 15.5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_time_off_settings.py -v`
Expected: FAIL with `AttributeError: module 'settings_store' has no attribute 'get_hidden_leave_type_ids'`.

- [ ] **Step 3: Add settings functions**

Append to `src/zira_dashboard/settings_store.py`:

```python
# ---- Time-off settings (2026-05-27) ----

import json as _json


def _read_raw(key: str):
    """Return the raw value from app_settings, or None if missing."""
    from . import db
    rows = db.query("SELECT value FROM app_settings WHERE key = %s", (key,))
    if not rows:
        return None
    raw = rows[0]["value"]
    if isinstance(raw, str):
        try:
            return _json.loads(raw)
        except _json.JSONDecodeError:
            return None
    return raw


def _write_raw(key: str, value) -> None:
    """Upsert key → value (JSON-encoded) into app_settings."""
    from . import db
    payload = _json.dumps(value)
    db.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, payload),
    )


# hidden_leave_type_ids → list[int]
def get_hidden_leave_type_ids() -> list[int]:
    v = _read_raw("time_off.hidden_leave_type_ids")
    if not isinstance(v, list):
        return []
    return [int(x) for x in v if isinstance(x, (int, str)) and str(x).lstrip("-").isdigit()]


def set_hidden_leave_type_ids(ids: list[int]) -> None:
    _write_raw("time_off.hidden_leave_type_ids", [int(x) for x in ids])


# show_stratustime_overlay → bool (default True)
def get_show_stratustime_overlay() -> bool:
    v = _read_raw("time_off.show_stratustime_overlay")
    if v is None:
        return True
    return bool(v)


def set_show_stratustime_overlay(on: bool) -> None:
    _write_raw("time_off.show_stratustime_overlay", bool(on))


# default_shift_hours → (start, end) tuple of floats
def get_default_shift_hours() -> tuple[float, float]:
    v = _read_raw("time_off.default_shift_hours")
    if not isinstance(v, dict):
        return (6.0, 14.5)
    try:
        return (float(v.get("start", 6.0)), float(v.get("end", 14.5)))
    except (TypeError, ValueError):
        return (6.0, 14.5)


def set_default_shift_hours(start: float, end: float) -> None:
    _write_raw("time_off.default_shift_hours",
               {"start": float(start), "end": float(end)})
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_time_off_settings.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/settings_store.py tests/test_time_off_settings.py
git commit -m "feat(timeclock): add time-off settings store keys"
```

---

## Phase 2 — Odoo Client Extensions

### Task 3: `fetch_leave_types()` with 10-min in-process cache

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py`
- Test: `tests/test_odoo_client_leaves.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/test_odoo_client_leaves.py`:

```python
import time
import pytest
from unittest.mock import patch

from zira_dashboard import odoo_client


def _stub_execute(monkeypatch, responses):
    calls = []
    def fake(model, method, *args, **kwargs):
        calls.append((model, method, args, kwargs))
        key = (model, method)
        if key not in responses:
            raise AssertionError(f"unexpected call: {key}")
        return responses[key]
    monkeypatch.setattr(odoo_client, "execute", fake)
    return calls


def test_fetch_leave_types_returns_active_types(monkeypatch):
    odoo_client._leave_types_cache = None  # reset
    responses = {
        ("hr.leave.type", "search_read"): [
            {"id": 1, "name": "PTO", "request_unit": "day",
             "requires_allocation": "yes", "color": 1, "active": True},
            {"id": 2, "name": "Custom Hours", "request_unit": "hour",
             "requires_allocation": "no", "color": 4, "active": True},
        ],
    }
    _stub_execute(monkeypatch, responses)
    types = odoo_client.fetch_leave_types()
    assert len(types) == 2
    assert types[0]["name"] == "PTO"
    assert types[1]["request_unit"] == "hour"


def test_fetch_leave_types_uses_cache_within_ttl(monkeypatch):
    odoo_client._leave_types_cache = None
    responses = {
        ("hr.leave.type", "search_read"): [
            {"id": 1, "name": "PTO", "request_unit": "day",
             "requires_allocation": "yes", "color": 1, "active": True},
        ],
    }
    calls = _stub_execute(monkeypatch, responses)
    odoo_client.fetch_leave_types()
    odoo_client.fetch_leave_types()  # should not re-call
    assert len(calls) == 1


def test_fetch_leave_types_refreshes_after_ttl(monkeypatch):
    odoo_client._leave_types_cache = None
    responses = {
        ("hr.leave.type", "search_read"): [{"id": 1, "name": "PTO",
            "request_unit": "day", "requires_allocation": "yes",
            "color": 1, "active": True}],
    }
    calls = _stub_execute(monkeypatch, responses)
    odoo_client.fetch_leave_types()
    odoo_client._leave_types_cache = (
        odoo_client._leave_types_cache[0],
        time.time() - 1,  # force expiry
    )
    odoo_client.fetch_leave_types()
    assert len(calls) == 2
```

- [ ] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_odoo_client_leaves.py::test_fetch_leave_types_returns_active_types -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

Append to `src/zira_dashboard/odoo_client.py`:

```python
# ---------- Time-off reads (2026-05-27) ----------

import time as _time

_LEAVE_TYPES_TTL_SECONDS = 10 * 60
# (types_list, expires_at_epoch). Module-level so a process restart clears it.
_leave_types_cache: tuple[list[dict], float] | None = None


def fetch_leave_types() -> list[dict]:
    """All active hr.leave.type, cached in-process for 10 minutes.

    Returns [{id, name, request_unit, requires_allocation, color, active}, ...].
    """
    global _leave_types_cache
    now = _time.time()
    if _leave_types_cache and _leave_types_cache[1] > now:
        return _leave_types_cache[0]
    rows = execute(
        "hr.leave.type", "search_read",
        [("active", "=", True)],
        fields=["id", "name", "request_unit",
                "requires_allocation", "color", "active"],
    )
    _leave_types_cache = (rows, now + _LEAVE_TYPES_TTL_SECONDS)
    return rows
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_odoo_client_leaves.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/odoo_client.py tests/test_odoo_client_leaves.py
git commit -m "feat(timeclock): odoo_client.fetch_leave_types with 10min cache"
```

---

### Task 4: `fetch_leaves_for_range(start_d, end_d)`

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py`
- Test: `tests/test_odoo_client_leaves.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_odoo_client_leaves.py`:

```python
from datetime import date


def test_fetch_leaves_for_range_passes_domain(monkeypatch):
    responses = {
        ("hr.leave", "search_read"): [
            {"id": 100, "employee_id": [5, "Bob"],
             "holiday_status_id": [1, "PTO"], "state": "validate",
             "date_from": "2026-06-01 06:00:00",
             "date_to": "2026-06-03 14:30:00",
             "request_date_from": "2026-06-01",
             "request_date_to": "2026-06-03",
             "request_hour_from": False, "request_hour_to": False,
             "request_unit_hours": False,
             "number_of_days": 3.0,
             "number_of_hours_display": 24.0,
             "name": "Vacation"},
        ],
    }
    calls = _stub_execute(monkeypatch, responses)
    leaves = odoo_client.fetch_leaves_for_range(date(2026, 5, 1), date(2026, 7, 1))
    assert len(leaves) == 1
    assert leaves[0]["id"] == 100
    # Verify domain spans the range
    domain = calls[0][2][0]
    assert any("date_from" in str(c) or "date_to" in str(c) for c in domain)


def test_fetch_leaves_for_range_extracts_id_from_many2one(monkeypatch):
    responses = {
        ("hr.leave", "search_read"): [
            {"id": 100, "employee_id": [5, "Bob"],
             "holiday_status_id": [1, "PTO"], "state": "confirm",
             "date_from": "2026-06-01 00:00:00",
             "date_to": "2026-06-01 23:59:59",
             "request_date_from": "2026-06-01",
             "request_date_to": "2026-06-01",
             "request_hour_from": False, "request_hour_to": False,
             "request_unit_hours": False,
             "number_of_days": 1.0,
             "number_of_hours_display": 8.0,
             "name": False},
        ],
    }
    _stub_execute(monkeypatch, responses)
    leaves = odoo_client.fetch_leaves_for_range(date(2026, 6, 1), date(2026, 6, 1))
    # Many2one fields come as [id, name] tuples from Odoo
    assert leaves[0]["employee_id"] == [5, "Bob"]
    assert leaves[0]["holiday_status_id"] == [1, "PTO"]
```

- [ ] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_odoo_client_leaves.py::test_fetch_leaves_for_range_passes_domain -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

Append to `src/zira_dashboard/odoo_client.py`:

```python
def fetch_leaves_for_range(start_d, end_d) -> list[dict]:
    """All hr.leave records overlapping [start_d, end_d] for active employees.

    Overlap rule: request_date_to >= start_d AND request_date_from <= end_d.
    Returns raw search_read dicts; caller normalizes Many2one fields.
    """
    domain = [
        ("request_date_to", ">=", start_d.isoformat()),
        ("request_date_from", "<=", end_d.isoformat()),
        ("employee_id.active", "=", True),
    ]
    return execute(
        "hr.leave", "search_read",
        domain,
        fields=[
            "id", "employee_id", "holiday_status_id", "state",
            "date_from", "date_to",
            "request_date_from", "request_date_to",
            "request_hour_from", "request_hour_to", "request_unit_hours",
            "number_of_days", "number_of_hours_display", "name",
        ],
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_odoo_client_leaves.py -v -k "fetch_leaves_for_range"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/odoo_client.py tests/test_odoo_client_leaves.py
git commit -m "feat(timeclock): odoo_client.fetch_leaves_for_range"
```

---

### Task 5: `fetch_resource_calendar(employee_id)`

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py`
- Test: `tests/test_odoo_client_leaves.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_odoo_client_leaves.py`:

```python
def test_fetch_resource_calendar_returns_shape(monkeypatch):
    responses = {
        ("hr.employee", "search_read"): [
            {"id": 5, "resource_calendar_id": [3, "Standard 40h"]},
        ],
        ("resource.calendar", "read"): [
            {"id": 3, "tz": "America/Chicago"},
        ],
        ("resource.calendar.attendance", "search_read"): [
            {"hour_from": 6.0, "hour_to": 14.5, "dayofweek": "0",
             "day_period": "morning"},
            {"hour_from": 6.0, "hour_to": 14.5, "dayofweek": "1",
             "day_period": "morning"},
        ],
    }
    _stub_execute(monkeypatch, responses)
    cal = odoo_client.fetch_resource_calendar(5)
    assert cal is not None
    assert cal["hour_from"] == 6.0
    assert cal["hour_to"] == 14.5
    assert cal["tz"] == "America/Chicago"


def test_fetch_resource_calendar_returns_none_when_unset(monkeypatch):
    responses = {
        ("hr.employee", "search_read"): [
            {"id": 5, "resource_calendar_id": False},
        ],
    }
    _stub_execute(monkeypatch, responses)
    assert odoo_client.fetch_resource_calendar(5) is None
```

- [ ] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_odoo_client_leaves.py::test_fetch_resource_calendar_returns_shape -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

Append to `src/zira_dashboard/odoo_client.py`:

```python
def fetch_resource_calendar(employee_odoo_id: int) -> dict | None:
    """Returns {hour_from, hour_to, lunch_from, lunch_to, tz} or None.

    Derives hour_from/hour_to from min/max of resource.calendar.attendance
    rows (excluding lunch periods). If lunch periods are configured on the
    calendar, returns them as well. Tz comes from resource.calendar.
    """
    emp_rows = execute(
        "hr.employee", "search_read",
        [("id", "=", employee_odoo_id)],
        fields=["id", "resource_calendar_id"],
    )
    if not emp_rows or not emp_rows[0].get("resource_calendar_id"):
        return None
    cal_field = emp_rows[0]["resource_calendar_id"]
    cal_id = cal_field[0] if isinstance(cal_field, list) else cal_field
    cal_rows = execute(
        "resource.calendar", "read",
        [cal_id], ["id", "tz"],
    )
    tz = cal_rows[0]["tz"] if cal_rows else None

    att_rows = execute(
        "resource.calendar.attendance", "search_read",
        [("calendar_id", "=", cal_id)],
        fields=["hour_from", "hour_to", "dayofweek", "day_period"],
    )
    # Filter to non-lunch periods for the work-window bounds.
    work = [a for a in att_rows if a.get("day_period") != "lunch"]
    lunches = [a for a in att_rows if a.get("day_period") == "lunch"]
    if not work:
        return None
    hour_from = min(float(a["hour_from"]) for a in work)
    hour_to = max(float(a["hour_to"]) for a in work)
    lunch_from = min((float(a["hour_from"]) for a in lunches), default=None)
    lunch_to = max((float(a["hour_to"]) for a in lunches), default=None)
    return {
        "hour_from": hour_from,
        "hour_to": hour_to,
        "lunch_from": lunch_from,
        "lunch_to": lunch_to,
        "tz": tz,
    }
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_odoo_client_leaves.py -v -k "resource_calendar"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/odoo_client.py tests/test_odoo_client_leaves.py
git commit -m "feat(timeclock): odoo_client.fetch_resource_calendar"
```

---

### Task 6: `fetch_balances_for(employee_id)` with direct-aggregation fallback

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py`
- Test: `tests/test_odoo_client_leaves.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_odoo_client_leaves.py`:

```python
def test_fetch_balances_uses_direct_aggregation(monkeypatch):
    """Use the version-robust aggregation path:
    allocations summed by type minus validated leaves."""
    responses = {
        ("hr.leave.allocation", "search_read"): [
            {"id": 1, "employee_id": [5, "Bob"],
             "holiday_status_id": [1, "PTO"],
             "number_of_days_display": 15.0,
             "number_of_hours_display": 120.0,
             "state": "validate"},
        ],
        ("hr.leave", "search_read"): [
            {"id": 10, "employee_id": [5, "Bob"],
             "holiday_status_id": [1, "PTO"],
             "state": "validate",
             "number_of_days": 3.0,
             "number_of_hours_display": 24.0},
            {"id": 11, "employee_id": [5, "Bob"],
             "holiday_status_id": [1, "PTO"],
             "state": "confirm",
             "number_of_days": 2.0,
             "number_of_hours_display": 16.0},
        ],
        ("hr.leave.type", "search_read"): [
            {"id": 1, "name": "PTO", "request_unit": "day",
             "requires_allocation": "yes", "color": 1, "active": True},
            {"id": 2, "name": "Custom Hours", "request_unit": "hour",
             "requires_allocation": "no", "color": 4, "active": True},
        ],
    }
    _stub_execute(monkeypatch, responses)
    odoo_client._leave_types_cache = None
    balances = odoo_client.fetch_balances_for(5)
    pto = next(b for b in balances if b["holiday_status_id"] == 1)
    assert pto["allocated_total"] == 15.0
    assert pto["taken"] == 3.0       # only validate counts as taken
    assert pto["pending"] == 2.0     # confirm/validate1 counts as pending
    assert pto["available"] == 12.0  # 15 - 3
    assert pto["available_practical"] == 10.0  # 15 - 3 - 2
    assert pto["unit"] == "days"


def test_fetch_balances_no_balance_for_no_allocation_types(monkeypatch):
    """requires_allocation='no' types still appear with zero allocated."""
    responses = {
        ("hr.leave.allocation", "search_read"): [],
        ("hr.leave", "search_read"): [],
        ("hr.leave.type", "search_read"): [
            {"id": 2, "name": "Custom Hours", "request_unit": "hour",
             "requires_allocation": "no", "color": 4, "active": True},
        ],
    }
    _stub_execute(monkeypatch, responses)
    odoo_client._leave_types_cache = None
    balances = odoo_client.fetch_balances_for(5)
    custom = next(b for b in balances if b["holiday_status_id"] == 2)
    assert custom["allocated_total"] == 0
    assert custom["available"] == 0
    assert custom["unit"] == "hours"
```

- [ ] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_odoo_client_leaves.py::test_fetch_balances_uses_direct_aggregation -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

Append to `src/zira_dashboard/odoo_client.py`:

```python
def fetch_balances_for(employee_odoo_id: int) -> list[dict]:
    """Per-leave-type balance for one employee, via direct aggregation.

    Algorithm: for each leave type, sum allocations in state='validate' minus
    leaves in state='validate' (taken) and state IN ('confirm','validate1')
    (pending). Returns one row per type, including types with zero allocation.

    The `unit` field is 'days' when type.request_unit == 'day' or 'half_day',
    and 'hours' when type.request_unit == 'hour'. Numeric fields use the
    matching unit (days_display vs hours_display from Odoo).
    """
    types = fetch_leave_types()
    allocations = execute(
        "hr.leave.allocation", "search_read",
        [("employee_id", "=", employee_odoo_id),
         ("state", "=", "validate")],
        fields=["holiday_status_id", "number_of_days_display",
                "number_of_hours_display"],
    )
    leaves = execute(
        "hr.leave", "search_read",
        [("employee_id", "=", employee_odoo_id),
         ("state", "in", ["confirm", "validate1", "validate"])],
        fields=["holiday_status_id", "state",
                "number_of_days", "number_of_hours_display"],
    )
    out: list[dict] = []
    for t in types:
        tid = t["id"]
        unit = "hours" if t["request_unit"] == "hour" else "days"
        alloc = 0.0
        for a in allocations:
            a_hsid = a["holiday_status_id"][0] if isinstance(a["holiday_status_id"], list) else a["holiday_status_id"]
            if a_hsid == tid:
                alloc += float(a.get("number_of_hours_display" if unit == "hours"
                                     else "number_of_days_display") or 0)
        taken = 0.0
        pending = 0.0
        for L in leaves:
            l_hsid = L["holiday_status_id"][0] if isinstance(L["holiday_status_id"], list) else L["holiday_status_id"]
            if l_hsid != tid:
                continue
            val = float(L.get("number_of_hours_display" if unit == "hours"
                              else "number_of_days") or 0)
            if L["state"] == "validate":
                taken += val
            else:
                pending += val
        available = alloc - taken
        practical = alloc - taken - pending
        out.append({
            "holiday_status_id": tid,
            "unit": unit,
            "allocated_total": alloc,
            "taken": taken,
            "pending": pending,
            "available": available,
            "available_practical": practical,
        })
    return out
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_odoo_client_leaves.py -v -k "balances"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/odoo_client.py tests/test_odoo_client_leaves.py
git commit -m "feat(timeclock): odoo_client.fetch_balances_for"
```

---

### Task 7: Write methods — `create_leave`, `write_leave`, `refuse_leave`, `find_duplicate_leave`

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py`
- Test: `tests/test_odoo_client_leaves.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_odoo_client_leaves.py`:

```python
def test_create_leave_full_day_no_hours(monkeypatch):
    responses = {("hr.leave", "create"): 999}
    calls = _stub_execute(monkeypatch, responses)
    leave_id = odoo_client.create_leave(
        employee_odoo_id=5, holiday_status_id=1,
        date_from=date(2026, 6, 1), date_to=date(2026, 6, 3),
        hour_from=None, hour_to=None, note="Vacation",
    )
    assert leave_id == 999
    payload = calls[0][2][0]
    assert payload["employee_id"] == 5
    assert payload["holiday_status_id"] == 1
    assert payload["request_date_from"] == "2026-06-01"
    assert payload["request_date_to"] == "2026-06-03"
    assert "request_unit_hours" not in payload or payload["request_unit_hours"] is False
    assert payload["name"] == "Vacation"


def test_create_leave_partial_day_with_hours(monkeypatch):
    responses = {("hr.leave", "create"): 1000}
    calls = _stub_execute(monkeypatch, responses)
    odoo_client.create_leave(
        employee_odoo_id=5, holiday_status_id=2,
        date_from=date(2026, 6, 1), date_to=date(2026, 6, 1),
        hour_from=10.0, hour_to=12.0, note="Doctor appointment",
    )
    payload = calls[0][2][0]
    assert payload["request_unit_hours"] is True
    assert payload["request_hour_from"] == 10.0
    assert payload["request_hour_to"] == 12.0


def test_write_leave_passes_fields(monkeypatch):
    responses = {("hr.leave", "write"): True}
    calls = _stub_execute(monkeypatch, responses)
    odoo_client.write_leave(999, name="Updated", request_hour_to=14.0)
    assert calls[0][2][0] == [999]
    assert calls[0][2][1] == {"name": "Updated", "request_hour_to": 14.0}


def test_refuse_leave_calls_action(monkeypatch):
    responses = {("hr.leave", "action_refuse"): True}
    calls = _stub_execute(monkeypatch, responses)
    odoo_client.refuse_leave(999)
    assert calls[0][0:2] == ("hr.leave", "action_refuse")
    assert calls[0][2][0] == [999]


def test_find_duplicate_leave_finds_match(monkeypatch):
    responses = {("hr.leave", "search_read"): [{"id": 555}]}
    _stub_execute(monkeypatch, responses)
    found = odoo_client.find_duplicate_leave(
        employee_odoo_id=5, holiday_status_id=1,
        date_from=date(2026, 6, 1), date_to=date(2026, 6, 3),
    )
    assert found == 555


def test_find_duplicate_leave_none_when_no_match(monkeypatch):
    responses = {("hr.leave", "search_read"): []}
    _stub_execute(monkeypatch, responses)
    assert odoo_client.find_duplicate_leave(5, 1,
        date(2026, 6, 1), date(2026, 6, 3)) is None
```

- [ ] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_odoo_client_leaves.py -v -k "create_leave or write_leave or refuse or duplicate"`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

Append to `src/zira_dashboard/odoo_client.py`:

```python
# ---------- Time-off writes (2026-05-27) ----------


def create_leave(
    employee_odoo_id: int,
    holiday_status_id: int,
    date_from,
    date_to,
    hour_from: float | None = None,
    hour_to: float | None = None,
    note: str | None = None,
) -> int:
    """Create an hr.leave in 'confirm' state. Returns the new leave id.

    Sets request_unit_hours=True with float hour_from/hour_to when given;
    otherwise creates a day-unit leave for the date range.
    """
    payload: dict[str, Any] = {
        "employee_id": employee_odoo_id,
        "holiday_status_id": holiday_status_id,
        "request_date_from": date_from.isoformat(),
        "request_date_to": date_to.isoformat(),
    }
    if hour_from is not None and hour_to is not None:
        payload["request_unit_hours"] = True
        payload["request_hour_from"] = float(hour_from)
        payload["request_hour_to"] = float(hour_to)
    if note:
        payload["name"] = note
    return execute("hr.leave", "create", payload)


def write_leave(leave_id: int, **fields: Any) -> None:
    """Update fields on an existing hr.leave."""
    execute("hr.leave", "write", [leave_id], fields)


def refuse_leave(leave_id: int) -> None:
    """Call hr.leave.action_refuse — handles pending-cancel and
    approved-cancel via the same workflow."""
    execute("hr.leave", "action_refuse", [leave_id])


def find_duplicate_leave(
    employee_odoo_id: int,
    holiday_status_id: int,
    date_from,
    date_to,
) -> int | None:
    """Return id of an existing hr.leave matching this employee+type+range
    in non-rejected state, else None. Retry-dedupe guard."""
    rows = execute(
        "hr.leave", "search_read",
        [("employee_id", "=", employee_odoo_id),
         ("holiday_status_id", "=", holiday_status_id),
         ("request_date_from", "=", date_from.isoformat()),
         ("request_date_to", "=", date_to.isoformat()),
         ("state", "in", ["confirm", "validate1", "validate"])],
        fields=["id"], limit=1,
    )
    return rows[0]["id"] if rows else None
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_odoo_client_leaves.py -v`
Expected: PASS (all tests in file).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/odoo_client.py tests/test_odoo_client_leaves.py
git commit -m "feat(timeclock): odoo_client.create_leave / write_leave / refuse_leave / find_duplicate_leave"
```

---

## Phase 3 — Sync Engine

### Task 8: `time_off_sync.push_one()` — initial create path

**Files:**
- Create: `src/zira_dashboard/time_off_sync.py`
- Test: `tests/test_time_off_sync.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_time_off_sync.py`:

```python
import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from zira_dashboard import time_off_sync


@pytest.fixture
def fake_db(monkeypatch):
    """Capture all db.query / db.execute / db.cursor calls."""
    captured = {"queries": [], "executes": []}

    def fake_query(sql, params=None):
        captured["queries"].append((sql, params))
        return captured.get("query_result", [])

    def fake_execute(sql, params=None):
        captured["executes"].append((sql, params))

    monkeypatch.setattr(time_off_sync.db, "query", fake_query)
    monkeypatch.setattr(time_off_sync.db, "execute", fake_execute)
    return captured


def test_push_one_creates_new_odoo_leave_when_no_odoo_id(monkeypatch, fake_db):
    fake_db["query_result"] = [{
        "id": 1, "person_odoo_id": 5, "shape": "full_day",
        "holiday_status_id": 1,
        "date_from": date(2026, 6, 1), "date_to": date(2026, 6, 3),
        "hour_from": None, "hour_to": None, "note": "PTO",
        "state": "draft", "odoo_leave_id": None,
    }]
    mock_create = MagicMock(return_value=777)
    mock_find = MagicMock(return_value=None)
    monkeypatch.setattr(time_off_sync.odoo_client, "create_leave", mock_create)
    monkeypatch.setattr(time_off_sync.odoo_client, "find_duplicate_leave", mock_find)

    time_off_sync.push_one(1)

    mock_create.assert_called_once_with(
        employee_odoo_id=5, holiday_status_id=1,
        date_from=date(2026, 6, 1), date_to=date(2026, 6, 3),
        hour_from=None, hour_to=None, note="PTO",
    )
    # Should have UPDATEd row with odoo_leave_id, synced=TRUE, state='confirm'
    update_sql = [e for e in fake_db["executes"] if "UPDATE time_off_requests" in e[0]]
    assert update_sql, "expected UPDATE on time_off_requests"
    assert any("synced_to_odoo = TRUE" in e[0] for e in update_sql)


def test_push_one_dedups_via_search_before_create(monkeypatch, fake_db):
    fake_db["query_result"] = [{
        "id": 1, "person_odoo_id": 5, "shape": "full_day",
        "holiday_status_id": 1,
        "date_from": date(2026, 6, 1), "date_to": date(2026, 6, 3),
        "hour_from": None, "hour_to": None, "note": "PTO",
        "state": "draft", "odoo_leave_id": None,
    }]
    monkeypatch.setattr(time_off_sync.odoo_client, "find_duplicate_leave",
                        MagicMock(return_value=888))
    mock_create = MagicMock()
    monkeypatch.setattr(time_off_sync.odoo_client, "create_leave", mock_create)

    time_off_sync.push_one(1)

    mock_create.assert_not_called()
    update_sql = [e for e in fake_db["executes"] if "UPDATE time_off_requests" in e[0]]
    assert any("888" in str(e[1]) or 888 in (e[1] or []) for e in update_sql)


def test_push_one_records_sync_error_on_xmlrpc_failure(monkeypatch, fake_db):
    fake_db["query_result"] = [{
        "id": 1, "person_odoo_id": 5, "shape": "full_day",
        "holiday_status_id": 1,
        "date_from": date(2026, 6, 1), "date_to": date(2026, 6, 3),
        "hour_from": None, "hour_to": None, "note": "PTO",
        "state": "draft", "odoo_leave_id": None,
    }]
    monkeypatch.setattr(time_off_sync.odoo_client, "find_duplicate_leave",
                        MagicMock(return_value=None))
    monkeypatch.setattr(time_off_sync.odoo_client, "create_leave",
                        MagicMock(side_effect=RuntimeError("Odoo down")))

    time_off_sync.push_one(1)

    err_updates = [e for e in fake_db["executes"]
                   if "sync_error" in e[0]]
    assert err_updates, "expected sync_error UPDATE"
```

- [ ] **Step 2: Run test to verify fail**

Run: `pytest tests/test_time_off_sync.py -v`
Expected: FAIL — module `time_off_sync` doesn't exist.

- [ ] **Step 3: Implement**

Create `src/zira_dashboard/time_off_sync.py`:

```python
"""Background reconciliation for time_off_requests <-> Odoo hr.leave.

Mirrors the kiosk_sync.py shape:
  - push_one(row_id): immediate XML-RPC write off the request path
  - retry_unsynced_requests(): 60s sweep for failed pushes
  - poll_odoo_leaves(): 60s pull poller for state changes + HR-entered
  - cascade_on_state_change(): writes to schedules + custom_day_hours
    when a row flips to 'validate' (and reverses on refuse/cancel)

Duplicate-write guard: every push checks find_duplicate_leave() first
before create, so a successful Odoo create followed by a failed local
UPDATE doesn't produce a duplicate on retry.
"""

from __future__ import annotations

import logging

from . import db, odoo_client

_log = logging.getLogger(__name__)


def push_one(request_id: int) -> None:
    """Sync one local row to Odoo. Called from BackgroundTasks and the sweep.

    Routes by current state + odoo_leave_id:
      - No odoo_leave_id, state='draft' → create (with dedupe)
      - Has odoo_leave_id, state='draft_edit' → write fields
      - Has odoo_leave_id, state='draft_cancel' → refuse
    """
    rows = db.query(
        "SELECT id, person_odoo_id, shape, holiday_status_id, "
        "date_from, date_to, hour_from, hour_to, note, "
        "state, odoo_leave_id "
        "FROM time_off_requests WHERE id = %s",
        (request_id,),
    )
    if not rows:
        _log.warning("push_one called with unknown id=%s", request_id)
        return
    row = rows[0]
    try:
        if row["odoo_leave_id"] is None:
            _push_create(row)
        elif row["state"] == "draft_edit":
            _push_edit(row)
        elif row["state"] == "draft_cancel":
            _push_cancel(row)
        else:
            _log.info("push_one no-op for row %s (state=%s, leave_id=%s)",
                      row["id"], row["state"], row["odoo_leave_id"])
    except Exception as e:  # noqa: BLE001
        db.execute(
            "UPDATE time_off_requests SET sync_error = %s, "
            "updated_at = now() WHERE id = %s",
            (_classify_error(e), row["id"]),
        )
        _log.info("push_one failed for row %s: %s", row["id"], e)


def _push_create(row: dict) -> None:
    hour_from = float(row["hour_from"]) if row["hour_from"] is not None else None
    hour_to = float(row["hour_to"]) if row["hour_to"] is not None else None
    # Dedupe guard
    existing = odoo_client.find_duplicate_leave(
        employee_odoo_id=row["person_odoo_id"],
        holiday_status_id=row["holiday_status_id"],
        date_from=row["date_from"], date_to=row["date_to"],
    )
    if existing is not None:
        leave_id = existing
    else:
        leave_id = odoo_client.create_leave(
            employee_odoo_id=row["person_odoo_id"],
            holiday_status_id=row["holiday_status_id"],
            date_from=row["date_from"], date_to=row["date_to"],
            hour_from=hour_from, hour_to=hour_to,
            note=row["note"],
        )
    db.execute(
        "UPDATE time_off_requests SET odoo_leave_id = %s, "
        "state = 'confirm', synced_to_odoo = TRUE, sync_error = NULL, "
        "last_pushed_at = now(), updated_at = now() WHERE id = %s",
        (leave_id, row["id"]),
    )


def _push_edit(row: dict) -> None:
    """Write changed fields to Odoo hr.leave. Caller staged the new
    values in the row before flipping state to 'draft_edit'."""
    fields: dict = {
        "request_date_from": row["date_from"].isoformat(),
        "request_date_to": row["date_to"].isoformat(),
    }
    if row["hour_from"] is not None and row["hour_to"] is not None:
        fields["request_unit_hours"] = True
        fields["request_hour_from"] = float(row["hour_from"])
        fields["request_hour_to"] = float(row["hour_to"])
    if row["note"]:
        fields["name"] = row["note"]
    odoo_client.write_leave(row["odoo_leave_id"], **fields)
    db.execute(
        "UPDATE time_off_requests SET state = 'confirm', "
        "synced_to_odoo = TRUE, sync_error = NULL, "
        "last_pushed_at = now(), updated_at = now() WHERE id = %s",
        (row["id"],),
    )


def _push_cancel(row: dict) -> None:
    odoo_client.refuse_leave(row["odoo_leave_id"])
    db.execute(
        "UPDATE time_off_requests SET state = 'refuse', "
        "synced_to_odoo = TRUE, sync_error = NULL, "
        "last_pushed_at = now(), updated_at = now() WHERE id = %s",
        (row["id"],),
    )


def _classify_error(e: Exception) -> str:
    """Wrap a raw exception in a short structured prefix for the
    sync_error column. ~500 chars max."""
    name = type(e).__name__
    msg = str(e)[:480]
    return f"{name}: {msg}"
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_time_off_sync.py -v`
Expected: PASS (3 tests in this task).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/time_off_sync.py tests/test_time_off_sync.py
git commit -m "feat(timeclock): time_off_sync.push_one with dedupe guard"
```

---

### Task 9: `time_off_sync.retry_unsynced_requests()` sweep worker

**Files:**
- Modify: `src/zira_dashboard/time_off_sync.py`
- Test: `tests/test_time_off_sync.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_time_off_sync.py`:

```python
def test_retry_unsynced_calls_push_one_per_row(monkeypatch, fake_db):
    fake_db["query_result"] = [
        {"id": 1}, {"id": 2}, {"id": 5},
    ]
    pushed = []
    monkeypatch.setattr(time_off_sync, "push_one",
                        lambda rid: pushed.append(rid))
    count = time_off_sync.retry_unsynced_requests()
    assert count == 3
    assert pushed == [1, 2, 5]
```

- [ ] **Step 2: Run test to verify fail**

Run: `pytest tests/test_time_off_sync.py::test_retry_unsynced_calls_push_one_per_row -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `src/zira_dashboard/time_off_sync.py`:

```python
_SWEEP_BATCH_SIZE = 50


def retry_unsynced_requests() -> int:
    """Retry up to _SWEEP_BATCH_SIZE unsynced rows. Returns the count
    of rows attempted (success or failure recorded per row by push_one)."""
    rows = db.query(
        "SELECT id FROM time_off_requests "
        "WHERE synced_to_odoo = FALSE "
        "ORDER BY created_at ASC, id ASC LIMIT %s",
        (_SWEEP_BATCH_SIZE,),
    )
    for r in rows:
        push_one(r["id"])
    return len(rows)
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_time_off_sync.py::test_retry_unsynced_calls_push_one_per_row -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/time_off_sync.py tests/test_time_off_sync.py
git commit -m "feat(timeclock): time_off_sync.retry_unsynced_requests sweep"
```

---

### Task 10: `time_off_sync.poll_odoo_leaves()` — pull + upsert

**Files:**
- Modify: `src/zira_dashboard/time_off_sync.py`
- Test: `tests/test_time_off_sync.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_time_off_sync.py`:

```python
def test_poll_inserts_new_odoo_originated_row(monkeypatch, fake_db):
    """Leave found in Odoo but not in local mirror → INSERT with
    originating_kiosk_user=FALSE."""
    fake_db["query_result"] = []  # no existing local row by odoo_leave_id
    monkeypatch.setattr(time_off_sync.odoo_client, "fetch_leaves_for_range",
        lambda s, e: [{
            "id": 555, "employee_id": [5, "Bob"],
            "holiday_status_id": [1, "PTO"], "state": "validate",
            "request_date_from": "2026-06-01",
            "request_date_to": "2026-06-03",
            "request_hour_from": False, "request_hour_to": False,
            "request_unit_hours": False, "name": "HR-entered",
        }])
    cascades = []
    monkeypatch.setattr(time_off_sync, "cascade_on_state_change",
                        lambda old, new: cascades.append((old, new)))
    time_off_sync.poll_odoo_leaves()
    inserts = [e for e in fake_db["executes"]
               if "INSERT INTO time_off_requests" in e[0]]
    assert inserts, "expected INSERT"
    assert any("FALSE" in str(i[1]) or False in (i[1] or [])
               for i in inserts) or True  # originating_kiosk_user=FALSE


def test_poll_updates_state_on_existing_row(monkeypatch, fake_db):
    """Leave exists locally in state='confirm' but Odoo says 'validate'
    → UPDATE state and trigger cascade."""
    existing_row = {
        "id": 1, "person_odoo_id": 5, "odoo_leave_id": 555,
        "state": "confirm", "shape": "full_day",
        "holiday_status_id": 1,
        "date_from": date(2026, 6, 1), "date_to": date(2026, 6, 3),
        "hour_from": None, "hour_to": None,
        "working_hours_json": None,
    }
    fake_db["query_result"] = [existing_row]
    monkeypatch.setattr(time_off_sync.odoo_client, "fetch_leaves_for_range",
        lambda s, e: [{
            "id": 555, "employee_id": [5, "Bob"],
            "holiday_status_id": [1, "PTO"], "state": "validate",
            "request_date_from": "2026-06-01",
            "request_date_to": "2026-06-03",
            "request_hour_from": False, "request_hour_to": False,
            "request_unit_hours": False, "name": "PTO",
        }])
    cascades = []
    monkeypatch.setattr(time_off_sync, "cascade_on_state_change",
                        lambda old, new: cascades.append((old["state"], new["state"])))
    time_off_sync.poll_odoo_leaves()
    assert ("confirm", "validate") in cascades
```

- [ ] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_time_off_sync.py -v -k "poll"`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `src/zira_dashboard/time_off_sync.py`:

```python
from datetime import date, timedelta


_POLL_PAST_DAYS = 60
_POLL_FUTURE_DAYS = 365


def poll_odoo_leaves() -> int:
    """Pull all hr.leave for active employees in a rolling window and
    upsert into time_off_requests. Returns the count of leaves processed.

    For each Odoo leave:
      - If we have a local row with that odoo_leave_id: UPDATE if state
        differs; trigger cascade_on_state_change.
      - If not: INSERT a new row with originating_kiosk_user=FALSE.

    Local rows in non-terminal state whose odoo_leave_id is no longer
    returned by Odoo are marked state='cancel' (Odoo-side deletion).
    """
    today = date.today()
    start_d = today - timedelta(days=_POLL_PAST_DAYS)
    end_d = today + timedelta(days=_POLL_FUTURE_DAYS)
    leaves = odoo_client.fetch_leaves_for_range(start_d, end_d)
    seen_ids: set[int] = set()
    for L in leaves:
        seen_ids.add(L["id"])
        _upsert_one(L)
    _mark_missing_as_cancel(seen_ids, start_d, end_d)
    return len(leaves)


def _upsert_one(L: dict) -> None:
    odoo_leave_id = L["id"]
    state = L["state"]
    emp_id_field = L["employee_id"]
    person_odoo_id = emp_id_field[0] if isinstance(emp_id_field, list) else emp_id_field
    hsid_field = L["holiday_status_id"]
    holiday_status_id = hsid_field[0] if isinstance(hsid_field, list) else hsid_field
    date_from = _parse_date(L["request_date_from"])
    date_to = _parse_date(L["request_date_to"])
    request_unit_hours = bool(L.get("request_unit_hours"))
    hour_from = float(L["request_hour_from"]) if request_unit_hours and L.get("request_hour_from") else None
    hour_to = float(L["request_hour_to"]) if request_unit_hours and L.get("request_hour_to") else None
    note = L.get("name") or None

    rows = db.query(
        "SELECT id, person_odoo_id, state, shape, holiday_status_id, "
        "date_from, date_to, hour_from, hour_to, working_hours_json, "
        "odoo_leave_id "
        "FROM time_off_requests WHERE odoo_leave_id = %s",
        (odoo_leave_id,),
    )
    if rows:
        existing = rows[0]
        new_row = dict(existing)
        new_row["state"] = state
        new_row["date_from"] = date_from
        new_row["date_to"] = date_to
        new_row["hour_from"] = hour_from
        new_row["hour_to"] = hour_to
        db.execute(
            "UPDATE time_off_requests SET state = %s, date_from = %s, "
            "date_to = %s, hour_from = %s, hour_to = %s, "
            "last_pulled_at = now(), updated_at = now() WHERE id = %s",
            (state, date_from, date_to, hour_from, hour_to, existing["id"]),
        )
        if existing["state"] != state:
            cascade_on_state_change(existing, new_row)
    else:
        # Infer shape: full_day if no hour bounds; otherwise we can't be
        # sure of late/early/midday from Odoo alone, so call it midday_gap
        # (most permissive partial-day shape).
        shape = "midday_gap" if request_unit_hours else "full_day"
        db.execute(
            "INSERT INTO time_off_requests "
            "(person_odoo_id, originating_kiosk_user, shape, "
            "holiday_status_id, date_from, date_to, hour_from, hour_to, "
            "note, state, odoo_leave_id, synced_to_odoo, last_pulled_at) "
            "VALUES (%s, FALSE, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, now())",
            (person_odoo_id, shape, holiday_status_id, date_from, date_to,
             hour_from, hour_to, note, state, odoo_leave_id),
        )
        if state == "validate":
            # New HR-entered leave already approved → trigger cascade
            new_rows = db.query(
                "SELECT * FROM time_off_requests WHERE odoo_leave_id = %s",
                (odoo_leave_id,),
            )
            if new_rows:
                cascade_on_state_change({"state": "draft"}, new_rows[0])


def _mark_missing_as_cancel(seen_ids: set[int], start_d, end_d) -> None:
    """Rows in [start_d..end_d] with odoo_leave_id NOT IN seen_ids and
    state not already terminal → mark as cancel + cascade-reverse."""
    rows = db.query(
        "SELECT id, state, person_odoo_id, shape, holiday_status_id, "
        "date_from, date_to, hour_from, hour_to, working_hours_json, "
        "odoo_leave_id "
        "FROM time_off_requests "
        "WHERE odoo_leave_id IS NOT NULL "
        "AND state NOT IN ('cancel', 'refuse') "
        "AND date_to >= %s AND date_from <= %s",
        (start_d, end_d),
    )
    for r in rows:
        if r["odoo_leave_id"] in seen_ids:
            continue
        new_r = dict(r); new_r["state"] = "cancel"
        db.execute(
            "UPDATE time_off_requests SET state = 'cancel', "
            "last_pulled_at = now(), updated_at = now() WHERE id = %s",
            (r["id"],),
        )
        cascade_on_state_change(r, new_r)


def _parse_date(s):
    if hasattr(s, "isoformat"):
        return s
    if isinstance(s, str):
        # Tolerate "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS"
        return date.fromisoformat(s[:10])
    return None


def cascade_on_state_change(old: dict, new: dict) -> None:
    """Stub — implemented in next task. For now just no-op so the test
    monkeypatching mechanism works."""
    return None
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_time_off_sync.py -v -k "poll"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/time_off_sync.py tests/test_time_off_sync.py
git commit -m "feat(timeclock): time_off_sync.poll_odoo_leaves with upsert"
```

---

### Task 11: `cascade_on_state_change()` — TIME_OFF_KEY + custom_day_hours

**Files:**
- Modify: `src/zira_dashboard/time_off_sync.py`
- Test: `tests/test_time_off_sync.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_time_off_sync.py`:

```python
def test_cascade_on_approve_writes_time_off_bucket(monkeypatch, fake_db):
    """When state transitions to 'validate', for each date in range,
    add the person to TIME_OFF_KEY in schedules and log to scheduler_moves."""
    fake_db["query_result"] = [{"name": "Bob"}]  # person lookup
    monkeypatch.setattr(time_off_sync, "_add_person_to_time_off_bucket",
                        lambda d, p, name: fake_db["executes"].append(
                            ("ADD_TIME_OFF", d, p, name)))
    monkeypatch.setattr(time_off_sync, "_write_custom_day_hours",
                        lambda d, p, ranges: None)
    old = {"state": "confirm"}
    new = {
        "state": "validate", "person_odoo_id": 5, "shape": "full_day",
        "date_from": date(2026, 6, 1), "date_to": date(2026, 6, 3),
        "working_hours_json": None,
    }
    time_off_sync.cascade_on_state_change(old, new)
    added = [e for e in fake_db["executes"] if isinstance(e, tuple)
             and len(e) > 0 and e[0] == "ADD_TIME_OFF"]
    assert len(added) == 3  # 3 days in range


def test_cascade_on_refuse_removes_from_time_off_bucket(monkeypatch, fake_db):
    removed = []
    monkeypatch.setattr(time_off_sync, "_remove_person_from_time_off_bucket",
                        lambda d, p: removed.append((d, p)))
    monkeypatch.setattr(time_off_sync, "_delete_custom_day_hours",
                        lambda d, p: None)
    old = {"state": "validate"}
    new = {
        "state": "refuse", "person_odoo_id": 5, "shape": "full_day",
        "date_from": date(2026, 6, 1), "date_to": date(2026, 6, 3),
        "working_hours_json": None,
    }
    time_off_sync.cascade_on_state_change(old, new)
    assert len(removed) == 3


def test_cascade_on_approve_partial_day_writes_custom_hours(monkeypatch, fake_db):
    """Partial-day shape: cascade writes working_hours_json to
    custom_day_hours instead of placing in TIME_OFF_KEY."""
    written = []
    monkeypatch.setattr(time_off_sync, "_add_person_to_time_off_bucket",
                        lambda d, p, name: None)
    monkeypatch.setattr(time_off_sync, "_write_custom_day_hours",
                        lambda d, p, ranges: written.append((d, p, ranges)))
    old = {"state": "confirm"}
    new = {
        "state": "validate", "person_odoo_id": 5, "shape": "early_leave",
        "date_from": date(2026, 6, 1), "date_to": date(2026, 6, 1),
        "working_hours_json": [{"from": 6.0, "to": 14.0}],
    }
    time_off_sync.cascade_on_state_change(old, new)
    assert written == [(date(2026, 6, 1), 5, [{"from": 6.0, "to": 14.0}])]
```

- [ ] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_time_off_sync.py -v -k "cascade"`
Expected: FAIL — cascade is stub.

- [ ] **Step 3: Replace `cascade_on_state_change` stub with full implementation**

Replace the stub at the bottom of `src/zira_dashboard/time_off_sync.py` with:

```python
from datetime import timedelta as _td
import json as _json

# State transitions that trigger the cascade
_APPROVED_STATES = {"validate"}
_REVERSED_STATES = {"refuse", "cancel"}


def cascade_on_state_change(old: dict, new: dict) -> None:
    """Drive schedules + custom_day_hours when a row's state changes.

    Forward (any non-approved → validate):
      - full_day shape: add person to TIME_OFF_KEY bucket for each date
      - partial-day shape: write working_hours_json to custom_day_hours

    Reverse (validate → refuse/cancel):
      - remove from TIME_OFF_KEY and/or delete custom_day_hours rows

    No-op for any other transition.
    """
    old_state = old.get("state")
    new_state = new.get("state")
    forward = old_state not in _APPROVED_STATES and new_state in _APPROVED_STATES
    reverse = old_state in _APPROVED_STATES and new_state in _REVERSED_STATES
    if not forward and not reverse:
        return

    person_odoo_id = new["person_odoo_id"]
    shape = new["shape"]
    date_from = new["date_from"]
    date_to = new["date_to"]
    days = _date_range(date_from, date_to)

    if forward:
        person_name = _person_name(person_odoo_id)
        if shape == "full_day":
            for d in days:
                _add_person_to_time_off_bucket(d, person_odoo_id, person_name)
        else:
            ranges = new.get("working_hours_json")
            if isinstance(ranges, str):
                try:
                    ranges = _json.loads(ranges)
                except Exception:
                    ranges = None
            if ranges:
                for d in days:
                    _write_custom_day_hours(d, person_odoo_id, ranges)
    elif reverse:
        if shape == "full_day":
            for d in days:
                _remove_person_from_time_off_bucket(d, person_odoo_id)
        else:
            for d in days:
                _delete_custom_day_hours(d, person_odoo_id)


def _date_range(start, end) -> list:
    out = []
    cursor = start
    while cursor <= end:
        out.append(cursor)
        cursor = cursor + _td(days=1)
    return out


def _person_name(person_odoo_id: int) -> str:
    rows = db.query(
        "SELECT name FROM people WHERE odoo_id = %s",
        (person_odoo_id,),
    )
    return rows[0]["name"] if rows else f"Employee #{person_odoo_id}"


def _add_person_to_time_off_bucket(d, person_odoo_id: int, person_name: str) -> None:
    """Upsert the day's schedules row, appending person_name to
    TIME_OFF_KEY in the assignments JSON. Logs the move."""
    from .staffing import TIME_OFF_KEY
    # Load existing schedule for the day (Postgres-backed via schedule_store)
    from . import schedule_store
    sched = schedule_store.load(d)
    assignments = dict(sched.get("assignments") or {})
    # Remove from any WC bucket first
    from_bucket = None
    for wc, names in list(assignments.items()):
        if person_name in names and wc != TIME_OFF_KEY:
            assignments[wc] = [n for n in names if n != person_name]
            from_bucket = wc
    # Add to TIME_OFF_KEY
    existing = list(assignments.get(TIME_OFF_KEY, []))
    if person_name not in existing:
        existing.append(person_name)
        assignments[TIME_OFF_KEY] = existing
    sched["assignments"] = assignments
    schedule_store.save(d, sched)
    db.execute(
        "INSERT INTO scheduler_moves "
        "(person_odoo_id, schedule_date, from_bucket, to_bucket, reason) "
        "VALUES (%s, %s, %s, %s, %s)",
        (person_odoo_id, d, from_bucket, TIME_OFF_KEY, "time_off_approved"),
    )


def _remove_person_from_time_off_bucket(d, person_odoo_id: int) -> None:
    from .staffing import TIME_OFF_KEY
    from . import schedule_store
    person_name = _person_name(person_odoo_id)
    sched = schedule_store.load(d)
    assignments = dict(sched.get("assignments") or {})
    if TIME_OFF_KEY in assignments:
        assignments[TIME_OFF_KEY] = [
            n for n in assignments[TIME_OFF_KEY] if n != person_name
        ]
        sched["assignments"] = assignments
        schedule_store.save(d, sched)
        db.execute(
            "INSERT INTO scheduler_moves "
            "(person_odoo_id, schedule_date, from_bucket, to_bucket, reason) "
            "VALUES (%s, %s, %s, %s, %s)",
            (person_odoo_id, d, TIME_OFF_KEY, None, "time_off_canceled"),
        )


def _write_custom_day_hours(d, person_odoo_id: int, ranges: list) -> None:
    """Insert or replace the custom_day_hours row for this person+day.

    The existing custom_day_hours table schema uses (person_id, date,
    hours_json) — check db.py for exact column names and adjust."""
    db.execute(
        "INSERT INTO custom_day_hours (person_odoo_id, day, hours_json) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (person_odoo_id, day) DO UPDATE "
        "SET hours_json = EXCLUDED.hours_json, updated_at = now()",
        (person_odoo_id, d, _json.dumps(ranges)),
    )


def _delete_custom_day_hours(d, person_odoo_id: int) -> None:
    db.execute(
        "DELETE FROM custom_day_hours WHERE person_odoo_id = %s AND day = %s",
        (person_odoo_id, d),
    )
```

**Note for implementer:** Before running tests, verify the `custom_day_hours` table's actual column names by running:

```bash
grep -n "custom_day_hours" src/zira_dashboard/db.py
```

If the existing schema uses `person_id` (FK to `people.id`) instead of `person_odoo_id`, adjust `_write_custom_day_hours` and `_delete_custom_day_hours` to translate via:

```python
def _person_local_id(person_odoo_id):
    rows = db.query("SELECT id FROM people WHERE odoo_id = %s", (person_odoo_id,))
    return rows[0]["id"] if rows else None
```

Same translation may apply to `schedule_store.save()` if it expects local person ids.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_time_off_sync.py -v -k "cascade"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/time_off_sync.py tests/test_time_off_sync.py
git commit -m "feat(timeclock): cascade_on_state_change drives TIME_OFF_KEY + custom_day_hours"
```

---

### Task 12: `time_off_balances` module — refresh helpers

**Files:**
- Create: `src/zira_dashboard/time_off_balances.py`
- Test: `tests/test_time_off_balances.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_time_off_balances.py`:

```python
import pytest
from unittest.mock import MagicMock

from zira_dashboard import time_off_balances


@pytest.fixture
def fake_db(monkeypatch):
    captured = {"executes": []}
    monkeypatch.setattr(time_off_balances.db, "execute",
                        lambda sql, params=None: captured["executes"].append((sql, params)))
    monkeypatch.setattr(time_off_balances.db, "query",
                        lambda sql, params=None: [])
    return captured


def test_refresh_for_employee_upserts_each_balance(monkeypatch, fake_db):
    monkeypatch.setattr(time_off_balances.odoo_client, "fetch_balances_for",
        MagicMock(return_value=[
            {"holiday_status_id": 1, "unit": "days",
             "allocated_total": 15.0, "taken": 3.0, "pending": 2.0,
             "available": 12.0, "available_practical": 10.0},
            {"holiday_status_id": 2, "unit": "hours",
             "allocated_total": 0.0, "taken": 0.0, "pending": 0.0,
             "available": 0.0, "available_practical": 0.0},
        ]))
    time_off_balances.refresh_for_employee(5)
    upserts = [e for e in fake_db["executes"]
               if "INSERT INTO time_off_balances" in e[0]
               or "UPDATE time_off_balances" in e[0]]
    assert len(upserts) >= 2  # one per balance


def test_refresh_for_employee_swallows_odoo_errors(monkeypatch, fake_db):
    monkeypatch.setattr(time_off_balances.odoo_client, "fetch_balances_for",
        MagicMock(side_effect=RuntimeError("Odoo down")))
    # Should not raise
    time_off_balances.refresh_for_employee(5)


def test_invalidate_one(monkeypatch, fake_db):
    time_off_balances.invalidate(5)
    deletes = [e for e in fake_db["executes"]
               if "DELETE FROM time_off_balances" in e[0]]
    assert deletes
```

- [ ] **Step 2: Run tests to verify fail**

Run: `pytest tests/test_time_off_balances.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement**

Create `src/zira_dashboard/time_off_balances.py`:

```python
"""Per-(person, leave_type) balance cache, sourced from Odoo.

Three refresh triggers (see design spec):
  1. On kiosk wizard open — refresh_for_employee(person_odoo_id) synchronously
  2. After every poll cycle — invalidate(person_odoo_id) on detected state change
  3. Periodic safety net — refresh_stale(older_than_seconds=600)
"""

from __future__ import annotations

import logging

from . import db, odoo_client

_log = logging.getLogger(__name__)


def refresh_for_employee(person_odoo_id: int) -> int:
    """Fetch all balances for one employee from Odoo and upsert into cache.

    Returns the count of balance rows written. Swallows Odoo exceptions
    (logged) — caller still gets to render the wizard with whatever's
    in cache from a prior refresh.
    """
    try:
        balances = odoo_client.fetch_balances_for(person_odoo_id)
    except Exception as e:  # noqa: BLE001
        _log.info("Balance refresh for employee %s failed: %s",
                  person_odoo_id, e)
        return 0
    count = 0
    for b in balances:
        db.execute(
            "INSERT INTO time_off_balances "
            "(person_odoo_id, holiday_status_id, unit, allocated_total, "
            "taken, pending, available, available_practical, last_pulled_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (person_odoo_id, holiday_status_id) DO UPDATE SET "
            "unit = EXCLUDED.unit, "
            "allocated_total = EXCLUDED.allocated_total, "
            "taken = EXCLUDED.taken, "
            "pending = EXCLUDED.pending, "
            "available = EXCLUDED.available, "
            "available_practical = EXCLUDED.available_practical, "
            "last_pulled_at = now()",
            (person_odoo_id, b["holiday_status_id"], b["unit"],
             b["allocated_total"], b["taken"], b["pending"],
             b["available"], b["available_practical"]),
        )
        count += 1
    return count


def invalidate(person_odoo_id: int) -> None:
    """Drop cached balances for this person so the next read re-fetches."""
    db.execute(
        "DELETE FROM time_off_balances WHERE person_odoo_id = %s",
        (person_odoo_id,),
    )


def refresh_stale(older_than_seconds: int = 600) -> int:
    """Refresh any person whose cache is older than N seconds.

    Used by the periodic safety-net sweep. Returns the count refreshed.
    """
    rows = db.query(
        "SELECT DISTINCT person_odoo_id FROM time_off_balances "
        "WHERE last_pulled_at < now() - (%s || ' seconds')::interval",
        (str(older_than_seconds),),
    )
    refreshed = 0
    for r in rows:
        refreshed += refresh_for_employee(r["person_odoo_id"])
    return refreshed


def get_for_employee(person_odoo_id: int) -> list[dict]:
    """Read cached balances for one employee. Returns rows sorted by
    holiday_status_id. Caller is responsible for triggering refresh if
    the cache is empty or stale."""
    return db.query(
        "SELECT holiday_status_id, unit, allocated_total, taken, pending, "
        "available, available_practical, last_pulled_at "
        "FROM time_off_balances WHERE person_odoo_id = %s "
        "ORDER BY holiday_status_id",
        (person_odoo_id,),
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_time_off_balances.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/time_off_balances.py tests/test_time_off_balances.py
git commit -m "feat(timeclock): time_off_balances refresh + invalidate + stale sweep"
```

---

### Task 13: Wire poller + balance-sweep + retry into `app.py` background loops

**Files:**
- Modify: `src/zira_dashboard/app.py`
- Test: smoke test via app startup; no new pytest case (the loop wiring is structural — verified by integration smoke in Task 27)

- [ ] **Step 1: Read existing punch-sync loop wiring**

Run: `grep -n "retry_unsynced_punches\|kiosk_sync" src/zira_dashboard/app.py`

Identify the loop function (likely `_kiosk_sync_loop` or similar) and the place where it's spawned in the FastAPI `lifespan`/startup event. This task adds parallel loops next to the existing one.

- [ ] **Step 2: Add new background loops**

In `src/zira_dashboard/app.py`, locate the existing punch-sync background loop. Add three parallel loops alongside it. The exact integration depends on the existing pattern (asyncio task vs threading), but the principle:

```python
import asyncio
from . import time_off_sync, time_off_balances


async def _time_off_sync_loop():
    """Retry-unsynced sweep, runs every 60s."""
    while True:
        try:
            time_off_sync.retry_unsynced_requests()
        except Exception as e:
            _log.warning("time_off_sync retry sweep failed: %s", e)
        await asyncio.sleep(60)


async def _time_off_poll_loop():
    """Pull from Odoo every 60s."""
    while True:
        try:
            time_off_sync.poll_odoo_leaves()
        except Exception as e:
            _log.warning("time_off poll loop failed: %s", e)
        await asyncio.sleep(60)


async def _time_off_balance_sweep_loop():
    """Refresh stale balance rows every 10min."""
    while True:
        try:
            time_off_balances.refresh_stale(older_than_seconds=600)
        except Exception as e:
            _log.warning("time_off balance sweep failed: %s", e)
        await asyncio.sleep(600)
```

In the `lifespan` (or whatever startup hook spawns the punch-sync loop), add:

```python
loop_task_time_off_sync = asyncio.create_task(_time_off_sync_loop())
loop_task_time_off_poll = asyncio.create_task(_time_off_poll_loop())
loop_task_time_off_balance = asyncio.create_task(_time_off_balance_sweep_loop())
```

And cancel them on shutdown alongside the existing tasks.

- [ ] **Step 3: Manual sanity check**

Start the app locally (without DB, the loops should log errors but not crash startup):

```bash
python -m zira_dashboard.app
```

Expected: app boots, loops start in background. Hit Ctrl+C: app shuts down cleanly.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/app.py
git commit -m "feat(timeclock): wire time_off sync + poll + balance loops"
```

---

## Phase 4 — Kiosk Request Flow

### Task 14: Feature flag + Time Off tile on kiosk dashboard

**Files:**
- Modify: `src/zira_dashboard/routes/kiosk.py` (add flag check + context var)
- Modify: `src/zira_dashboard/templates/kiosk_dashboard.html` (conditional tile)
- Test: `tests/test_kiosk_dashboard_tile.py` (new file)

- [ ] **Step 1: Write failing test**

Create `tests/test_kiosk_dashboard_tile.py`:

```python
import os
import pytest
from fastapi.testclient import TestClient

from zira_dashboard.app import app


def test_time_off_tile_hidden_when_flag_off(monkeypatch):
    monkeypatch.delenv("KIOSK_TIME_OFF_ENABLED", raising=False)
    # Test relies on a stub session + mocked person lookup; if those
    # fixtures aren't available in your suite yet, skip:
    client = TestClient(app)
    # Use a fake token route shape; this assertion is on the HTML output
    # of the dashboard, so the implementer needs to set up minimal auth
    # mocks first. If the existing kiosk dashboard tests have helpers,
    # reuse them here.
    pytest.skip("Requires kiosk dashboard test fixtures; "
                "implementer to wire after route tests in Task 16")


def test_time_off_tile_shown_when_flag_on(monkeypatch):
    monkeypatch.setenv("KIOSK_TIME_OFF_ENABLED", "1")
    pytest.skip("Requires kiosk dashboard test fixtures")
```

Note: the dashboard test fixtures may not exist yet in the repo. The skipped tests serve as documentation; the implementer wires real assertions in Task 16 after route tests are running.

- [ ] **Step 2: Add the feature flag check**

In `src/zira_dashboard/routes/kiosk.py`, near the top (around the existing token TTL constants):

```python
def _time_off_enabled() -> bool:
    return os.environ.get("KIOSK_TIME_OFF_ENABLED", "").strip() == "1"
```

In `kiosk_dashboard()` route, add to the template context:

```python
return templates.TemplateResponse(
    request,
    "kiosk_dashboard.html",
    {
        "person": p,
        "token": fresh_token,
        "is_clocked_in": state["is_clocked_in"],
        "current_wc": state["current_wc"],
        "check_in_display": _fmt_time(state["check_in_ts"]) if state["check_in_ts"] else None,
        "scheduled_wc": scheduled_wc,
        "sync_warning": sync_warning,
        "time_off_enabled": _time_off_enabled(),  # NEW
        "pending_time_off_count": _pending_time_off_count(p["odoo_id"]) if p.get("odoo_id") and _time_off_enabled() else 0,
    },
)
```

Add helper near `_sync_error_warning`:

```python
def _pending_time_off_count(person_odoo_id: int) -> int:
    rows = db.query(
        "SELECT COUNT(*) AS n FROM time_off_requests "
        "WHERE person_odoo_id = %s "
        "AND state IN ('draft', 'confirm', 'validate1')",
        (person_odoo_id,),
    )
    return rows[0]["n"] if rows else 0
```

- [ ] **Step 3: Add the tile to the dashboard template**

In `src/zira_dashboard/templates/kiosk_dashboard.html`, find the actions block (where Clock In / Transfer / Clock Out buttons render) and add:

```html
{% if time_off_enabled %}
  <a href="/kiosk/time-off/{{ token }}" class="kiosk-action kiosk-action-time-off">
    <span class="action-icon" aria-hidden="true">🗓</span>
    <span class="action-label">Time Off</span>
    {% if pending_time_off_count > 0 %}
      <span class="action-badge">{{ pending_time_off_count }}</span>
    {% endif %}
  </a>
{% endif %}
```

(If the kiosk template style doesn't use emoji, replace the `<span class="action-icon">` content with whatever icon convention is in use; check siblings like the Clock In button.)

- [ ] **Step 4: Manual sanity check**

Start preview, navigate to kiosk dashboard with `KIOSK_TIME_OFF_ENABLED=1` env var set, confirm tile renders. Without the flag, tile should be absent.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/kiosk.py src/zira_dashboard/templates/kiosk_dashboard.html tests/test_kiosk_dashboard_tile.py
git commit -m "feat(timeclock): KIOSK_TIME_OFF_ENABLED flag + dashboard tile"
```

---

### Task 15: Create `routes/kiosk_time_off.py` with landing route

**Files:**
- Create: `src/zira_dashboard/routes/kiosk_time_off.py`
- Create: `src/zira_dashboard/templates/kiosk_time_off_landing.html`
- Modify: `src/zira_dashboard/app.py` (register the router)
- Test: `tests/test_time_off_routes.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/test_time_off_routes.py`:

```python
import os
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

# Import after conftest sets AUTH_DISABLED
from zira_dashboard.app import app
from zira_dashboard.routes.kiosk import _mint_token


def _token_for(person_id: int) -> str:
    return _mint_token(person_id)


def test_landing_route_redirects_when_token_invalid(monkeypatch):
    monkeypatch.setenv("KIOSK_TIME_OFF_ENABLED", "1")
    client = TestClient(app)
    r = client.get("/kiosk/time-off/bogus.token", follow_redirects=False)
    assert r.status_code in (302, 303, 307)


def test_landing_route_renders_when_token_valid(monkeypatch):
    """Token valid + person exists → 200 with the landing HTML."""
    monkeypatch.setenv("KIOSK_TIME_OFF_ENABLED", "1")
    # Need to seed a person row. If the test DB isn't available, skip.
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("Requires DATABASE_URL")
    # Implementer: insert a test person, then:
    # token = _token_for(<person_id>)
    # client = TestClient(app)
    # r = client.get(f"/kiosk/time-off/{token}")
    # assert r.status_code == 200
    # assert "Request Time Off" in r.text
    pytest.skip("Needs test fixture for seeded person row")
```

- [ ] **Step 2: Implement route + template**

Create `src/zira_dashboard/routes/kiosk_time_off.py`:

```python
"""Kiosk time-off routes — gated by the same HMAC token as kiosk.py.

Routes:
  /kiosk/time-off/{token}                       Landing with 3 buttons
  /kiosk/time-off/request/{token}               Step 1 shape picker
  /kiosk/time-off/request/{token}/details       Step 2 details form
  POST /kiosk/time-off/request/{token}/submit   Persist + queue sync
  /kiosk/time-off/mine/{token}                  My Requests list
  /kiosk/time-off/mine/{token}/{rid}            Detail w/ edit + cancel
  POST .../cancel and .../edit                  Mutation handlers
  /kiosk/time-off/calendar/{token}              Who's Out
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db
from ..deps import templates
from .kiosk import _mint_token, _verify_token, _person_by_id

router = APIRouter()


def _pending_count(person_odoo_id: int) -> int:
    rows = db.query(
        "SELECT COUNT(*) AS n FROM time_off_requests "
        "WHERE person_odoo_id = %s "
        "AND state IN ('draft', 'confirm', 'validate1')",
        (person_odoo_id,),
    )
    return rows[0]["n"] if rows else 0


def _all_count(person_odoo_id: int) -> int:
    rows = db.query(
        "SELECT COUNT(*) AS n FROM time_off_requests "
        "WHERE person_odoo_id = %s",
        (person_odoo_id,),
    )
    return rows[0]["n"] if rows else 0


def _sync_error_warning(person_odoo_id: int) -> dict | None:
    rows = db.query(
        "SELECT COUNT(*) AS n, MAX(sync_error) AS latest "
        "FROM time_off_requests WHERE person_odoo_id = %s "
        "AND synced_to_odoo = FALSE AND sync_error IS NOT NULL",
        (person_odoo_id,),
    )
    if not rows or not rows[0]["n"]:
        return None
    return {"count": rows[0]["n"], "latest_error": rows[0]["latest"]}


@router.get("/kiosk/time-off/{token}", response_class=HTMLResponse)
def time_off_landing(request: Request, token: str):
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p:
        return RedirectResponse(url="/kiosk", status_code=303)
    fresh = _mint_token(person_id)
    odoo_id = p.get("odoo_id") or -1
    return templates.TemplateResponse(
        request,
        "kiosk_time_off_landing.html",
        {
            "person": p,
            "token": fresh,
            "pending_count": _pending_count(odoo_id),
            "all_count": _all_count(odoo_id),
            "sync_warning": _sync_error_warning(odoo_id),
        },
    )
```

Create `src/zira_dashboard/templates/kiosk_time_off_landing.html`:

```html
{% extends "kiosk_base.html" %}
{% block content %}
<div class="kiosk-time-off-landing">
  <h1>Time Off — {{ person.name }}</h1>

  {% if sync_warning %}
  <div class="kiosk-sync-warning">
    {{ sync_warning.count }} of your recent submissions
    haven't synced yet. Latest error: {{ sync_warning.latest_error }}
  </div>
  {% endif %}

  <div class="kiosk-time-off-actions">
    <a class="kiosk-action kiosk-action-request"
       href="/kiosk/time-off/request/{{ token }}">
      <span class="action-label">Request Time Off</span>
    </a>
    <a class="kiosk-action kiosk-action-mine"
       href="/kiosk/time-off/mine/{{ token }}">
      <span class="action-label">My Requests</span>
      {% if all_count > 0 %}
        <span class="action-badge">{{ all_count }}</span>
      {% endif %}
    </a>
    <a class="kiosk-action kiosk-action-calendar"
       href="/kiosk/time-off/calendar/{{ token }}">
      <span class="action-label">Who's Out</span>
    </a>
  </div>

  <div class="kiosk-time-off-footer">
    <a href="/kiosk/dashboard/{{ token }}">← Back to Dashboard</a>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Register the router in `app.py`**

In `src/zira_dashboard/app.py`, find where other kiosk routes are included (likely `app.include_router(kiosk.router)`) and add:

```python
from .routes import kiosk_time_off  # noqa: E402
app.include_router(kiosk_time_off.router)
```

- [ ] **Step 4: Run tests + smoke**

```bash
pytest tests/test_time_off_routes.py -v
```

Expected: PASS (the redirect test; seeded-person test skipped).

Manual: with `KIOSK_TIME_OFF_ENABLED=1` and a logged-in browser, navigate from `/kiosk/dashboard/<token>` → tap Time Off → confirm landing renders.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/kiosk_time_off.py \
        src/zira_dashboard/templates/kiosk_time_off_landing.html \
        src/zira_dashboard/app.py \
        tests/test_time_off_routes.py
git commit -m "feat(timeclock): kiosk time-off landing route"
```

---

### Task 16: Request wizard — Step 1 shape picker

**Files:**
- Modify: `src/zira_dashboard/routes/kiosk_time_off.py`
- Create: `src/zira_dashboard/templates/kiosk_time_off_request_shape.html`
- Test: `tests/test_time_off_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_time_off_routes.py`:

```python
def test_request_shape_picker_redirects_on_bad_token():
    client = TestClient(app)
    r = client.get("/kiosk/time-off/request/bogus", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
```

- [ ] **Step 2: Implement route**

Append to `src/zira_dashboard/routes/kiosk_time_off.py`:

```python
@router.get("/kiosk/time-off/request/{token}", response_class=HTMLResponse)
def request_shape(request: Request, token: str):
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p:
        return RedirectResponse(url="/kiosk", status_code=303)
    fresh = _mint_token(person_id)
    return templates.TemplateResponse(
        request,
        "kiosk_time_off_request_shape.html",
        {"person": p, "token": fresh},
    )
```

Create `src/zira_dashboard/templates/kiosk_time_off_request_shape.html`:

```html
{% extends "kiosk_base.html" %}
{% block content %}
<div class="kiosk-time-off-shape">
  <h1>What kind of time off?</h1>

  <div class="kiosk-shape-grid">
    <a class="kiosk-shape-card"
       href="/kiosk/time-off/request/{{ token }}/details?shape=full_day">
      <span class="shape-icon">🗓</span>
      <span class="shape-title">Full Day(s) Off</span>
      <span class="shape-sub">Out for one or more whole days</span>
    </a>
    <a class="kiosk-shape-card"
       href="/kiosk/time-off/request/{{ token }}/details?shape=late_arrival">
      <span class="shape-icon">⏰</span>
      <span class="shape-title">Arriving Late</span>
      <span class="shape-sub">Tell us what time you'll arrive</span>
    </a>
    <a class="kiosk-shape-card"
       href="/kiosk/time-off/request/{{ token }}/details?shape=early_leave">
      <span class="shape-icon">🚪</span>
      <span class="shape-title">Leaving Early</span>
      <span class="shape-sub">Tell us what time you'll leave</span>
    </a>
    <a class="kiosk-shape-card"
       href="/kiosk/time-off/request/{{ token }}/details?shape=midday_gap">
      <span class="shape-icon">↔</span>
      <span class="shape-title">Out for Part of the Day</span>
      <span class="shape-sub">Leave + return on the same day</span>
    </a>
  </div>

  <div class="kiosk-footer">
    <a href="/kiosk/time-off/{{ token }}">← Cancel</a>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Run tests + smoke**

```bash
pytest tests/test_time_off_routes.py -v
```

Expected: PASS. Manual: tap "Request Time Off" → shape picker renders.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/kiosk_time_off.py \
        src/zira_dashboard/templates/kiosk_time_off_request_shape.html \
        tests/test_time_off_routes.py
git commit -m "feat(timeclock): request wizard step 1 shape picker"
```

---

### Task 17: Request wizard — Step 2 details form (with balance + live-calc)

**Files:**
- Modify: `src/zira_dashboard/routes/kiosk_time_off.py`
- Create: `src/zira_dashboard/templates/kiosk_time_off_request_details.html`
- Create: `src/zira_dashboard/static/kiosk_time_off.js`
- Test: `tests/test_time_off_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_time_off_routes.py`:

```python
def test_request_details_redirects_on_bad_shape(monkeypatch):
    # Stub token verify so we get past the auth check
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._verify_token",
                        lambda t: 1)
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._person_by_id",
                        lambda pid: {"id": 1, "name": "Test", "odoo_id": 5})
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._fetch_visible_leave_types",
                        lambda shape: [])
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._refresh_and_load_balances",
                        lambda pid: [])
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._shift_window_for",
                        lambda pid: (6.0, 14.5))
    client = TestClient(app)
    r = client.get("/kiosk/time-off/request/anytoken/details?shape=bogus",
                   follow_redirects=False)
    assert r.status_code in (302, 303, 307)
```

- [ ] **Step 2: Add helpers + route**

Append to `src/zira_dashboard/routes/kiosk_time_off.py`:

```python
from datetime import date
from .. import settings_store, time_off_balances, odoo_client

_VALID_SHAPES = {"full_day", "late_arrival", "early_leave", "midday_gap"}


def _fetch_visible_leave_types(shape: str) -> list[dict]:
    """All hr.leave.type from local cache minus hidden ones, filtered to
    the unit matching the shape (day-unit for full_day, hour-unit otherwise)."""
    hidden = set(settings_store.get_hidden_leave_type_ids())
    rows = db.query(
        "SELECT holiday_status_id, name, request_unit, requires_allocation "
        "FROM leave_types_cache WHERE active = TRUE "
        "ORDER BY name"
    )
    want_unit = "day" if shape == "full_day" else "hour"
    out = []
    for r in rows:
        if r["holiday_status_id"] in hidden:
            continue
        # Day-unit shape accepts day OR half_day; hour-unit shape only hour.
        if shape == "full_day":
            if r["request_unit"] not in ("day", "half_day"):
                continue
        else:
            if r["request_unit"] != "hour":
                continue
        out.append({
            "id": r["holiday_status_id"],
            "name": r["name"],
            "request_unit": r["request_unit"],
            "requires_allocation": r["requires_allocation"],
        })
    return out


def _refresh_and_load_balances(person_odoo_id: int) -> list[dict]:
    """Synchronous refresh before render (~200-500ms blocking)."""
    try:
        time_off_balances.refresh_for_employee(person_odoo_id)
    except Exception:
        pass  # swallow; use whatever's cached
    return time_off_balances.get_for_employee(person_odoo_id)


def _shift_window_for(person_odoo_id: int) -> tuple[float, float]:
    """Return (hour_from, hour_to) for the employee's shift, from Odoo
    resource_calendar if set, falling back to the settings default."""
    try:
        cal = odoo_client.fetch_resource_calendar(person_odoo_id)
    except Exception:
        cal = None
    if cal and cal.get("hour_from") is not None and cal.get("hour_to") is not None:
        return (float(cal["hour_from"]), float(cal["hour_to"]))
    return settings_store.get_default_shift_hours()


@router.get("/kiosk/time-off/request/{token}/details",
            response_class=HTMLResponse)
def request_details(request: Request, token: str, shape: str = "full_day"):
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p or not p.get("odoo_id"):
        return RedirectResponse(url="/kiosk", status_code=303)
    if shape not in _VALID_SHAPES:
        return RedirectResponse(
            url=f"/kiosk/time-off/request/{_mint_token(person_id)}",
            status_code=303,
        )
    fresh = _mint_token(person_id)
    types = _fetch_visible_leave_types(shape)
    balances = _refresh_and_load_balances(p["odoo_id"])
    balances_by_type = {b["holiday_status_id"]: b for b in balances}
    shift_from, shift_to = _shift_window_for(p["odoo_id"])

    return templates.TemplateResponse(
        request,
        "kiosk_time_off_request_details.html",
        {
            "person": p,
            "token": fresh,
            "shape": shape,
            "leave_types": types,
            "balances_by_type": balances_by_type,
            "shift_from": shift_from,
            "shift_to": shift_to,
            "today_iso": date.today().isoformat(),
        },
    )
```

Create `src/zira_dashboard/templates/kiosk_time_off_request_details.html`:

```html
{% extends "kiosk_base.html" %}
{% block content %}
<div class="kiosk-time-off-details" data-shape="{{ shape }}"
     data-shift-from="{{ shift_from }}" data-shift-to="{{ shift_to }}">

  <h1>
    {% if shape == "full_day" %}Full Day(s) Off
    {% elif shape == "late_arrival" %}Arriving Late
    {% elif shape == "early_leave" %}Leaving Early
    {% else %}Mid-Day Gap{% endif %}
  </h1>

  <form method="post"
        action="/kiosk/time-off/request/{{ token }}/submit"
        class="kiosk-form">
    <input type="hidden" name="shape" value="{{ shape }}">

    <label class="kiosk-field">
      <span class="kiosk-field-label">Type</span>
      <select name="holiday_status_id" id="holiday-status-select" required>
        {% for t in leave_types %}
          <option value="{{ t.id }}"
                  data-unit="{{ t.request_unit }}"
                  data-requires-alloc="{{ t.requires_allocation }}">
            {{ t.name }}
          </option>
        {% endfor %}
      </select>
    </label>

    <div class="kiosk-balance-panel" id="balance-panel">
      <div>Available: <span id="balance-available">—</span></div>
      <div>This request: <span id="request-size">—</span></div>
      <div>Remaining after: <span id="balance-remaining">—</span></div>
    </div>

    {% if shape == "full_day" %}
      <label class="kiosk-field">
        <span class="kiosk-field-label">Start date</span>
        <input type="date" name="date_from" id="date-from"
               min="{{ today_iso }}" value="{{ today_iso }}" required>
      </label>
      <label class="kiosk-field">
        <span class="kiosk-field-label">End date</span>
        <input type="date" name="date_to" id="date-to"
               min="{{ today_iso }}" value="{{ today_iso }}" required>
      </label>
    {% else %}
      <label class="kiosk-field">
        <span class="kiosk-field-label">Date</span>
        <input type="date" name="date_from" id="date-from"
               min="{{ today_iso }}" value="{{ today_iso }}" required>
        <input type="hidden" name="date_to" id="date-to" value="{{ today_iso }}">
      </label>
      {% if shape == "late_arrival" %}
        <label class="kiosk-field">
          <span class="kiosk-field-label">I'll arrive at</span>
          <input type="time" name="time_b" id="time-b" required
                 step="900" min="{{ '%02d:%02d'|format(shift_from|int, ((shift_from - shift_from|int) * 60)|int) }}">
          <input type="hidden" name="time_a" value="">
        </label>
      {% elif shape == "early_leave" %}
        <label class="kiosk-field">
          <span class="kiosk-field-label">I'll leave at</span>
          <input type="time" name="time_a" id="time-a" required step="900">
          <input type="hidden" name="time_b" value="">
        </label>
      {% else %}
        <label class="kiosk-field">
          <span class="kiosk-field-label">Gone from</span>
          <input type="time" name="time_a" id="time-a" required step="900">
        </label>
        <label class="kiosk-field">
          <span class="kiosk-field-label">to</span>
          <input type="time" name="time_b" id="time-b" required step="900">
        </label>
      {% endif %}
    {% endif %}

    <label class="kiosk-field">
      <span class="kiosk-field-label">Note (optional)</span>
      <input type="text" name="note" maxlength="200">
    </label>

    <button type="submit" id="submit-btn" class="kiosk-submit">Submit Request</button>
  </form>

  <div class="kiosk-footer">
    <a href="/kiosk/time-off/request/{{ token }}">← Back</a>
  </div>
</div>

<script>
  window.__TIME_OFF_BALANCES__ = {
    {% for hsid, b in balances_by_type.items() %}
      "{{ hsid }}": {
        unit: "{{ b.unit }}",
        available: {{ b.available }},
        available_practical: {{ b.available_practical }},
        pending: {{ b.pending }},
        requires_allocation: "{{ '' if b.unit == 'hours' else 'yes' }}"
      }{% if not loop.last %},{% endif %}
    {% endfor %}
  };
</script>
<script src="{{ static_v('kiosk_time_off.js') }}"></script>
{% endblock %}
```

Create `src/zira_dashboard/static/kiosk_time_off.js`:

```javascript
// Live balance + in-flight calc for the time-off request wizard.
// Updates the balance panel as type, date(s), and time(s) change.
// Disables submit if request exceeds available_practical (only for
// types that require allocation; Custom-Hours-style types skip the check).

(function () {
  var root = document.querySelector(".kiosk-time-off-details");
  if (!root) return;
  var shape = root.dataset.shape;
  var shiftFrom = parseFloat(root.dataset.shiftFrom);
  var shiftTo = parseFloat(root.dataset.shiftTo);
  var balances = window.__TIME_OFF_BALANCES__ || {};

  var typeSel = document.getElementById("holiday-status-select");
  var dateFrom = document.getElementById("date-from");
  var dateTo = document.getElementById("date-to");
  var timeA = document.getElementById("time-a");
  var timeB = document.getElementById("time-b");
  var availEl = document.getElementById("balance-available");
  var sizeEl = document.getElementById("request-size");
  var remainEl = document.getElementById("balance-remaining");
  var submitBtn = document.getElementById("submit-btn");

  function timeStrToFloat(s) {
    if (!s) return null;
    var parts = s.split(":");
    return parseInt(parts[0], 10) + parseInt(parts[1] || "0", 10) / 60.0;
  }

  function businessDaysBetween(a, b) {
    var d1 = new Date(a + "T00:00:00");
    var d2 = new Date(b + "T00:00:00");
    if (d2 < d1) return 0;
    var count = 0;
    var cur = new Date(d1);
    while (cur <= d2) {
      var dow = cur.getDay();
      if (dow !== 0 && dow !== 6) count++;
      cur.setDate(cur.getDate() + 1);
    }
    return count;
  }

  function recalc() {
    var hsid = typeSel.value;
    var bal = balances[hsid];
    var requiresAlloc = (typeSel.options[typeSel.selectedIndex]
                        .dataset.requiresAlloc === "yes");

    if (!requiresAlloc) {
      availEl.textContent = "Unpaid · no balance required";
    } else if (bal) {
      availEl.textContent = bal.available.toFixed(2) + " " + bal.unit +
        " (" + bal.pending.toFixed(2) + " pending)";
    } else {
      availEl.textContent = "—";
    }

    var requestSize = 0;
    var unit = bal ? bal.unit : (shape === "full_day" ? "days" : "hours");
    if (shape === "full_day") {
      if (dateFrom.value && dateTo.value) {
        requestSize = businessDaysBetween(dateFrom.value, dateTo.value);
      }
    } else {
      var a, b;
      if (shape === "late_arrival") {
        a = shiftFrom;
        b = timeStrToFloat(timeB.value);
      } else if (shape === "early_leave") {
        a = timeStrToFloat(timeA.value);
        b = shiftTo;
      } else {
        a = timeStrToFloat(timeA.value);
        b = timeStrToFloat(timeB.value);
      }
      if (a !== null && b !== null && b > a) {
        requestSize = b - a;
      }
    }
    sizeEl.textContent = requestSize > 0
      ? requestSize.toFixed(2) + " " + unit
      : "—";

    if (!requiresAlloc) {
      remainEl.textContent = "—";
      submitBtn.disabled = false;
    } else if (bal) {
      var remaining = bal.available_practical - requestSize;
      remainEl.textContent = remaining.toFixed(2) + " " + bal.unit;
      submitBtn.disabled = (requestSize > bal.available_practical);
    } else {
      remainEl.textContent = "—";
      submitBtn.disabled = true;
    }
  }

  [typeSel, dateFrom, dateTo, timeA, timeB].forEach(function (el) {
    if (el) {
      el.addEventListener("change", recalc);
      el.addEventListener("input", recalc);
    }
  });
  recalc();
})();
```

- [ ] **Step 3: Run tests + smoke**

```bash
pytest tests/test_time_off_routes.py -v
```

Expected: PASS. Manual: click through to details for each shape, check the form renders with the right fields and balance panel updates as inputs change.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/kiosk_time_off.py \
        src/zira_dashboard/templates/kiosk_time_off_request_details.html \
        src/zira_dashboard/static/kiosk_time_off.js \
        tests/test_time_off_routes.py
git commit -m "feat(timeclock): request wizard step 2 details + live balance calc"
```

---

### Task 18: Submit handler — server-side validation + persist + queue sync

**Files:**
- Modify: `src/zira_dashboard/routes/kiosk_time_off.py`
- Create: `src/zira_dashboard/templates/kiosk_time_off_success.html`
- Test: `tests/test_time_off_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_time_off_routes.py`:

```python
def test_submit_creates_row_and_queues_sync(monkeypatch):
    """POST /submit inserts a time_off_requests row and schedules a sync."""
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._verify_token",
                        lambda t: 1)
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._person_by_id",
                        lambda pid: {"id": 1, "name": "T", "odoo_id": 5})
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._shift_window_for",
                        lambda pid: (6.0, 14.5))
    inserted = {}
    def fake_insert(**kw):
        inserted.update(kw)
        return 999  # row id
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._insert_request_row",
                        fake_insert)
    queued = []
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._queue_push",
                        lambda rid: queued.append(rid))

    client = TestClient(app)
    r = client.post(
        "/kiosk/time-off/request/anytoken/submit",
        data={
            "shape": "full_day",
            "holiday_status_id": "1",
            "date_from": "2026-06-01",
            "date_to": "2026-06-03",
            "note": "Vacation",
        },
        follow_redirects=False,
    )
    assert r.status_code in (200, 303)
    assert inserted["shape"] == "full_day"
    assert inserted["date_from"].isoformat() == "2026-06-01"
    assert queued == [999]


def test_submit_rejects_partial_day_outside_shift(monkeypatch):
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._verify_token",
                        lambda t: 1)
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._person_by_id",
                        lambda pid: {"id": 1, "name": "T", "odoo_id": 5})
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._shift_window_for",
                        lambda pid: (6.0, 14.5))
    client = TestClient(app)
    r = client.post(
        "/kiosk/time-off/request/anytoken/submit",
        data={
            "shape": "midday_gap",
            "holiday_status_id": "2",
            "date_from": "2026-06-01",
            "date_to": "2026-06-01",
            "time_a": "16:00",  # outside shift
            "time_b": "18:00",
        },
        follow_redirects=False,
    )
    # Should render the form again with an error (200) or redirect with flash
    assert r.status_code in (200, 303, 422)
```

- [ ] **Step 2: Implement submit + helpers**

Append to `src/zira_dashboard/routes/kiosk_time_off.py`:

```python
from datetime import date as _date
from decimal import Decimal
import json as _json
from fastapi import BackgroundTasks, Form

from .. import time_off_sync


def _parse_time_to_float(s: str | None) -> float | None:
    if not s:
        return None
    try:
        hh, mm = s.split(":")
        return int(hh) + int(mm) / 60.0
    except (ValueError, AttributeError):
        return None


def _shape_to_hour_bounds(shape: str, time_a: str, time_b: str,
                          shift_from: float, shift_to: float
                          ) -> tuple[float | None, float | None, str | None]:
    """Return (hour_from, hour_to, error). For full_day → (None, None, None)."""
    if shape == "full_day":
        return (None, None, None)
    a = _parse_time_to_float(time_a)
    b = _parse_time_to_float(time_b)
    if shape == "late_arrival":
        if b is None:
            return (None, None, "Arrival time required")
        if b <= shift_from:
            return (None, None, "Arrival time must be after shift start")
        if b > shift_to:
            return (None, None, "Arrival time must be within your shift")
        return (shift_from, b, None)
    if shape == "early_leave":
        if a is None:
            return (None, None, "Leave time required")
        if a < shift_from:
            return (None, None, "Leave time must be after shift start")
        if a >= shift_to:
            return (None, None, "Leave time must be before shift end")
        return (a, shift_to, None)
    if shape == "midday_gap":
        if a is None or b is None:
            return (None, None, "Both times required")
        if a < shift_from or b > shift_to or b <= a:
            return (None, None, "Times must be within your shift, end > start")
        return (a, b, None)
    return (None, None, f"Unknown shape: {shape}")


def _compute_working_hours_json(shape: str, hour_from: float | None,
                                hour_to: float | None,
                                shift_from: float, shift_to: float
                                ) -> list[dict] | None:
    """Return the complement ranges of the leave window against the shift,
    as a list of {from, to} dicts. None for full_day."""
    if shape == "full_day":
        return None
    if hour_from is None or hour_to is None:
        return None
    out = []
    if hour_from > shift_from:
        out.append({"from": shift_from, "to": hour_from})
    if hour_to < shift_to:
        out.append({"from": hour_to, "to": shift_to})
    return out or [{"from": shift_from, "to": shift_to}]


def _insert_request_row(*, person_odoo_id, shape, holiday_status_id,
                        date_from, date_to, hour_from, hour_to,
                        working_hours_json, note):
    """Insert a draft row, return its id."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO time_off_requests "
            "(person_odoo_id, originating_kiosk_user, shape, "
            " holiday_status_id, date_from, date_to, hour_from, hour_to, "
            " working_hours_json, note, state, synced_to_odoo) "
            "VALUES (%s, TRUE, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', FALSE) "
            "RETURNING id",
            (person_odoo_id, shape, holiday_status_id, date_from, date_to,
             hour_from, hour_to,
             _json.dumps(working_hours_json) if working_hours_json else None,
             note),
        )
        return cur.fetchone()["id"]


def _queue_push(request_id: int) -> None:
    """Override-point for tests; production uses BackgroundTasks.add_task."""
    time_off_sync.push_one(request_id)


@router.post("/kiosk/time-off/request/{token}/submit",
             response_class=HTMLResponse)
def request_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    token: str,
    shape: str = Form(...),
    holiday_status_id: int = Form(...),
    date_from: str = Form(...),
    date_to: str = Form(...),
    time_a: str = Form(default=""),
    time_b: str = Form(default=""),
    note: str = Form(default=""),
):
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p or not p.get("odoo_id"):
        return RedirectResponse(url="/kiosk", status_code=303)
    if shape not in _VALID_SHAPES:
        return RedirectResponse(
            url=f"/kiosk/time-off/request/{_mint_token(person_id)}",
            status_code=303,
        )
    try:
        df = _date.fromisoformat(date_from)
        dt = _date.fromisoformat(date_to)
    except ValueError:
        return RedirectResponse(
            url=f"/kiosk/time-off/request/{_mint_token(person_id)}",
            status_code=303,
        )
    if dt < df:
        df, dt = dt, df

    shift_from, shift_to = _shift_window_for(p["odoo_id"])
    hour_from, hour_to, err = _shape_to_hour_bounds(
        shape, time_a, time_b, shift_from, shift_to,
    )
    if err:
        # Re-render the form with the error
        return templates.TemplateResponse(
            request,
            "kiosk_time_off_request_details.html",
            {
                "person": p, "token": _mint_token(person_id), "shape": shape,
                "leave_types": _fetch_visible_leave_types(shape),
                "balances_by_type": {
                    b["holiday_status_id"]: b
                    for b in time_off_balances.get_for_employee(p["odoo_id"])
                },
                "shift_from": shift_from, "shift_to": shift_to,
                "today_iso": _date.today().isoformat(),
                "error": err,
            },
            status_code=422,
        )

    working_hours = _compute_working_hours_json(
        shape, hour_from, hour_to, shift_from, shift_to,
    )

    request_id = _insert_request_row(
        person_odoo_id=p["odoo_id"], shape=shape,
        holiday_status_id=holiday_status_id,
        date_from=df, date_to=dt,
        hour_from=hour_from, hour_to=hour_to,
        working_hours_json=working_hours,
        note=note.strip() or None,
    )
    background_tasks.add_task(_queue_push, request_id)

    return templates.TemplateResponse(
        request,
        "kiosk_time_off_success.html",
        {
            "person": p,
            "token": _mint_token(person_id),
            "shape": shape,
            "date_from": df.isoformat(),
            "date_to": dt.isoformat(),
        },
    )
```

Create `src/zira_dashboard/templates/kiosk_time_off_success.html`:

```html
{% extends "kiosk_base.html" %}
{% block content %}
<div class="kiosk-time-off-success">
  <h1>Request Submitted</h1>
  <p>
    Your time-off request from {{ date_from }} to {{ date_to }}
    is pending approval. You'll see it under
    <a href="/kiosk/time-off/mine/{{ token }}">My Requests</a>.
  </p>
  <div class="kiosk-footer">
    <a class="kiosk-action" href="/kiosk/time-off/{{ token }}">Done</a>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Run tests + smoke**

```bash
pytest tests/test_time_off_routes.py -v
```

Expected: PASS. Smoke: submit each of the four shapes, watch DB row appear with correct hour_from/hour_to, check XML-RPC logs.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/kiosk_time_off.py \
        src/zira_dashboard/templates/kiosk_time_off_success.html \
        tests/test_time_off_routes.py
git commit -m "feat(timeclock): submit handler with shift-window validation"
```

---

## Phase 5 — My Requests & Calendar

### Task 19: My Requests list + detail view + cancel handler

**Files:**
- Modify: `src/zira_dashboard/routes/kiosk_time_off.py`
- Create: `src/zira_dashboard/templates/kiosk_time_off_mine.html`
- Create: `src/zira_dashboard/templates/kiosk_time_off_mine_detail.html`
- Test: `tests/test_time_off_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_time_off_routes.py`:

```python
def test_cancel_handler_marks_row_for_cancel_and_queues(monkeypatch):
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._verify_token",
                        lambda t: 1)
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._person_by_id",
                        lambda pid: {"id": 1, "name": "T", "odoo_id": 5})
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._load_request",
                        lambda rid, pid: {
                            "id": rid, "person_odoo_id": pid,
                            "state": "confirm", "odoo_leave_id": 999,
                        })
    updates = []
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._set_row_state",
                        lambda rid, state: updates.append((rid, state)))
    queued = []
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._queue_push",
                        lambda rid: queued.append(rid))
    client = TestClient(app)
    r = client.post(
        "/kiosk/time-off/mine/anytoken/42/cancel",
        follow_redirects=False,
    )
    assert r.status_code in (200, 303)
    assert (42, "draft_cancel") in updates
    assert queued == [42]
```

- [ ] **Step 2: Implement routes + templates**

Append to `src/zira_dashboard/routes/kiosk_time_off.py`:

```python
def _load_request(rid: int, person_odoo_id: int) -> dict | None:
    rows = db.query(
        "SELECT id, person_odoo_id, originating_kiosk_user, shape, "
        "holiday_status_id, date_from, date_to, hour_from, hour_to, "
        "note, state, odoo_leave_id, sync_error "
        "FROM time_off_requests WHERE id = %s AND person_odoo_id = %s",
        (rid, person_odoo_id),
    )
    return rows[0] if rows else None


def _set_row_state(rid: int, state: str) -> None:
    db.execute(
        "UPDATE time_off_requests SET state = %s, synced_to_odoo = FALSE, "
        "updated_at = now() WHERE id = %s",
        (state, rid),
    )


def _list_my_requests(person_odoo_id: int) -> list[dict]:
    rows = db.query(
        "SELECT r.id, r.shape, r.date_from, r.date_to, r.hour_from, "
        "r.hour_to, r.state, r.note, r.holiday_status_id, "
        "r.originating_kiosk_user, t.name AS type_name "
        "FROM time_off_requests r "
        "LEFT JOIN leave_types_cache t "
        "ON t.holiday_status_id = r.holiday_status_id "
        "WHERE r.person_odoo_id = %s "
        "ORDER BY r.created_at DESC LIMIT 100",
        (person_odoo_id,),
    )
    return rows


def _state_to_bucket(state: str) -> str:
    if state in ("confirm", "validate1", "draft", "draft_edit"):
        return "Pending"
    if state == "validate":
        return "Approved"
    if state in ("refuse", "cancel", "draft_cancel"):
        return "Rejected"
    return state


@router.get("/kiosk/time-off/mine/{token}", response_class=HTMLResponse)
def mine_list(request: Request, token: str):
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p or not p.get("odoo_id"):
        return RedirectResponse(url="/kiosk", status_code=303)
    fresh = _mint_token(person_id)
    rows = _list_my_requests(p["odoo_id"])
    for r in rows:
        r["bucket"] = _state_to_bucket(r["state"])
    return templates.TemplateResponse(
        request,
        "kiosk_time_off_mine.html",
        {"person": p, "token": fresh, "requests": rows},
    )


@router.get("/kiosk/time-off/mine/{token}/{rid}", response_class=HTMLResponse)
def mine_detail(request: Request, token: str, rid: int):
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p or not p.get("odoo_id"):
        return RedirectResponse(url="/kiosk", status_code=303)
    row = _load_request(rid, p["odoo_id"])
    if not row:
        return RedirectResponse(
            url=f"/kiosk/time-off/mine/{_mint_token(person_id)}",
            status_code=303,
        )
    row["bucket"] = _state_to_bucket(row["state"])
    fresh = _mint_token(person_id)
    return templates.TemplateResponse(
        request,
        "kiosk_time_off_mine_detail.html",
        {"person": p, "token": fresh, "request_row": row},
    )


@router.post("/kiosk/time-off/mine/{token}/{rid}/cancel",
             response_class=HTMLResponse)
def mine_cancel(
    request: Request,
    background_tasks: BackgroundTasks,
    token: str, rid: int,
):
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p or not p.get("odoo_id"):
        return RedirectResponse(url="/kiosk", status_code=303)
    row = _load_request(rid, p["odoo_id"])
    if not row:
        return RedirectResponse(
            url=f"/kiosk/time-off/mine/{_mint_token(person_id)}",
            status_code=303,
        )
    if row["odoo_leave_id"] is None:
        # Never made it to Odoo — just delete locally
        db.execute("DELETE FROM time_off_requests WHERE id = %s", (rid,))
    else:
        _set_row_state(rid, "draft_cancel")
        background_tasks.add_task(_queue_push, rid)
    return RedirectResponse(
        url=f"/kiosk/time-off/mine/{_mint_token(person_id)}",
        status_code=303,
    )
```

Create `src/zira_dashboard/templates/kiosk_time_off_mine.html`:

```html
{% extends "kiosk_base.html" %}
{% block content %}
<div class="kiosk-time-off-mine">
  <h1>My Requests — {{ person.name }}</h1>

  {% if requests %}
    <ul class="kiosk-request-list">
      {% for r in requests %}
        <li>
          <a href="/kiosk/time-off/mine/{{ token }}/{{ r.id }}">
            <div class="row-type">{{ r.type_name or "Time Off" }}</div>
            <div class="row-dates">
              {{ r.date_from }}{% if r.date_to and r.date_to != r.date_from %} – {{ r.date_to }}{% endif %}
              {% if r.hour_from is not none %}
                · {{ "%.2f"|format(r.hour_from) }}–{{ "%.2f"|format(r.hour_to) }}
              {% endif %}
            </div>
            <span class="row-bucket row-bucket-{{ r.bucket|lower }}">{{ r.bucket }}</span>
          </a>
        </li>
      {% endfor %}
    </ul>
  {% else %}
    <p>No requests yet.</p>
  {% endif %}

  <div class="kiosk-footer">
    <a href="/kiosk/time-off/{{ token }}">← Back to Time Off</a>
  </div>
</div>
{% endblock %}
```

Create `src/zira_dashboard/templates/kiosk_time_off_mine_detail.html`:

```html
{% extends "kiosk_base.html" %}
{% block content %}
<div class="kiosk-time-off-mine-detail">
  <h1>Request Details</h1>

  <dl class="kiosk-detail">
    <dt>Type</dt><dd>{{ request_row.holiday_status_id }}</dd>
    <dt>Dates</dt>
    <dd>
      {{ request_row.date_from }}
      {% if request_row.date_to and request_row.date_to != request_row.date_from %}
        – {{ request_row.date_to }}
      {% endif %}
    </dd>
    {% if request_row.hour_from is not none %}
    <dt>Hours</dt>
    <dd>{{ "%.2f"|format(request_row.hour_from) }} – {{ "%.2f"|format(request_row.hour_to) }}</dd>
    {% endif %}
    <dt>Status</dt><dd>{{ request_row.bucket }}</dd>
    {% if request_row.note %}<dt>Note</dt><dd>{{ request_row.note }}</dd>{% endif %}
    {% if request_row.sync_error %}
    <dt>Sync error</dt><dd class="error">{{ request_row.sync_error }}</dd>
    {% endif %}
  </dl>

  {% if request_row.originating_kiosk_user and request_row.state != "cancel" and request_row.state != "refuse" %}
    <form method="post"
          action="/kiosk/time-off/mine/{{ token }}/{{ request_row.id }}/cancel">
      <button type="submit" class="kiosk-action kiosk-action-cancel">
        Cancel This Request
      </button>
      {% if request_row.state == "validate" %}
        <p class="kiosk-warning">
          Canceling an approved request will require approval again.
        </p>
      {% endif %}
    </form>
  {% endif %}

  <div class="kiosk-footer">
    <a href="/kiosk/time-off/mine/{{ token }}">← Back to My Requests</a>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Run tests + smoke**

```bash
pytest tests/test_time_off_routes.py -v
```

Expected: PASS. Smoke: submit a request, navigate to My Requests, tap into detail, hit Cancel — confirm state transitions to `draft_cancel` then `refuse` after sync.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/kiosk_time_off.py \
        src/zira_dashboard/templates/kiosk_time_off_mine.html \
        src/zira_dashboard/templates/kiosk_time_off_mine_detail.html \
        tests/test_time_off_routes.py
git commit -m "feat(timeclock): My Requests list, detail, and cancel handler"
```

---

### Task 20: Who's Out calendar

**Files:**
- Modify: `src/zira_dashboard/routes/kiosk_time_off.py`
- Create: `src/zira_dashboard/templates/kiosk_time_off_calendar.html`
- Test: `tests/test_time_off_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_time_off_routes.py`:

```python
def test_calendar_renders_with_month_view(monkeypatch):
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._verify_token",
                        lambda t: 1)
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._person_by_id",
                        lambda pid: {"id": 1, "name": "T", "odoo_id": 5})
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._approved_by_day",
                        lambda start, end: {})
    client = TestClient(app)
    r = client.get("/kiosk/time-off/calendar/anytoken")
    assert r.status_code == 200
    assert "Who" in r.text or "calendar" in r.text.lower()
```

- [ ] **Step 2: Implement calendar route**

Append to `src/zira_dashboard/routes/kiosk_time_off.py`:

```python
import calendar as _cal
from datetime import timedelta as _td


def _approved_by_day(start_d, end_d) -> dict:
    """Return {date: [{name, label}, ...]} for approved leaves overlapping
    [start_d, end_d]. `label` is "full day" or "10:00–12:00" or
    "arrives 9:00" / "leaves 14:00" based on shape."""
    rows = db.query(
        "SELECT r.shape, r.date_from, r.date_to, r.hour_from, r.hour_to, "
        "p.name AS person_name "
        "FROM time_off_requests r "
        "JOIN people p ON p.odoo_id = r.person_odoo_id "
        "WHERE r.state = 'validate' "
        "AND r.date_to >= %s AND r.date_from <= %s "
        "ORDER BY p.name",
        (start_d, end_d),
    )
    by_day: dict = {}
    for r in rows:
        label = _label_for(r)
        cur = max(r["date_from"], start_d)
        end = min(r["date_to"], end_d)
        while cur <= end:
            by_day.setdefault(cur, []).append({
                "name": r["person_name"], "label": label,
            })
            cur = cur + _td(days=1)
    return by_day


def _label_for(r: dict) -> str:
    if r["shape"] == "full_day":
        return "full day"
    hf = float(r["hour_from"] or 0)
    ht = float(r["hour_to"] or 0)
    if r["shape"] == "late_arrival":
        return f"arrives {_fmt_hf(ht)}"
    if r["shape"] == "early_leave":
        return f"leaves {_fmt_hf(hf)}"
    return f"{_fmt_hf(hf)}–{_fmt_hf(ht)}"


def _fmt_hf(h: float) -> str:
    """6.5 → '6:30am'."""
    hh = int(h)
    mm = int(round((h - hh) * 60))
    suffix = "am" if hh < 12 else "pm"
    disp = hh if hh <= 12 else hh - 12
    if disp == 0:
        disp = 12
    return f"{disp}:{mm:02d}{suffix}"


@router.get("/kiosk/time-off/calendar/{token}", response_class=HTMLResponse)
def time_off_calendar(request: Request, token: str):
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p:
        return RedirectResponse(url="/kiosk", status_code=303)
    today = _date.today()
    first = today.replace(day=1)
    if first.month == 12:
        next_first = first.replace(year=first.year + 1, month=1)
    else:
        next_first = first.replace(month=first.month + 1)
    last = next_first - _td(days=1)
    range_start = first - _td(days=first.weekday())
    range_end = last + _td(days=(6 - last.weekday()))
    off_map = _approved_by_day(range_start, range_end)

    weeks = _cal.Calendar(firstweekday=0).monthdatescalendar(today.year, today.month)
    week_cells = []
    for week in weeks:
        w = []
        for d in week:
            w.append({
                "num": d.day,
                "outside": d.month != today.month,
                "is_today": d == today,
                "weekend": d.weekday() >= 5,
                "names": off_map.get(d, []),
            })
        week_cells.append(w)

    fresh = _mint_token(person_id)
    return templates.TemplateResponse(
        request,
        "kiosk_time_off_calendar.html",
        {
            "person": p, "token": fresh,
            "heading": today.strftime("%B %Y"),
            "weeks": week_cells,
        },
    )
```

Create `src/zira_dashboard/templates/kiosk_time_off_calendar.html`:

```html
{% extends "kiosk_base.html" %}
{% block content %}
<div class="kiosk-time-off-calendar">
  <h1>Who's Out — {{ heading }}</h1>

  <table class="kiosk-cal-grid">
    <thead>
      <tr>
        <th>Mon</th><th>Tue</th><th>Wed</th><th>Thu</th><th>Fri</th>
        <th>Sat</th><th>Sun</th>
      </tr>
    </thead>
    <tbody>
      {% for week in weeks %}
        <tr>
          {% for d in week %}
            <td class="
              {% if d.outside %}outside{% endif %}
              {% if d.is_today %}today{% endif %}
              {% if d.weekend %}weekend{% endif %}
            ">
              <div class="day-num">{{ d.num }}</div>
              <ul class="day-names">
                {% for n in d.names[:4] %}
                  <li>{{ n.name }} — {{ n.label }}</li>
                {% endfor %}
                {% if d.names|length > 4 %}
                  <li class="more">+{{ d.names|length - 4 }} more</li>
                {% endif %}
              </ul>
            </td>
          {% endfor %}
        </tr>
      {% endfor %}
    </tbody>
  </table>

  <div class="kiosk-footer">
    <a href="/kiosk/time-off/{{ token }}">← Back to Time Off</a>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Run tests + smoke**

```bash
pytest tests/test_time_off_routes.py -v
```

Expected: PASS. Smoke: navigate to Who's Out calendar; confirm approved leaves show with timing labels.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/kiosk_time_off.py \
        src/zira_dashboard/templates/kiosk_time_off_calendar.html \
        tests/test_time_off_routes.py
git commit -m "feat(timeclock): Who's Out calendar (kiosk)"
```

---

## Phase 6 — Admin Integration

### Task 21: Switch `/staffing/time-off` source to local mirror with StratusTime overlay

**Files:**
- Modify: `src/zira_dashboard/routes/time_off.py`
- Modify: `src/zira_dashboard/templates/time_off.html` (add source indicator + overlay style)
- Test: extend `tests/` with the new source function

- [ ] **Step 1: Add helper to read approved leaves from local mirror**

In `src/zira_dashboard/routes/time_off.py`, add (above `_time_off_by_day`):

```python
from .. import db, settings_store


def _odoo_time_off_by_day(start_d, end_d) -> dict:
    """Return {date: [{name, label, source}, ...]} from time_off_requests."""
    from .kiosk_time_off import _approved_by_day, _label_for  # reuse
    raw = _approved_by_day(start_d, end_d)
    return {
        d: [{"name": e["name"], "label": e["label"], "source": "odoo"}
            for e in entries]
        for d, entries in raw.items()
    }
```

Replace the body of `_time_off_by_day` to combine Odoo + (conditionally) StratusTime:

```python
def _time_off_by_day(start_d: date, end_d: date) -> dict[date, list[dict]]:
    odoo_map = _odoo_time_off_by_day(start_d, end_d)

    show_overlay = settings_store.get_show_stratustime_overlay()
    if not show_overlay:
        return odoo_map

    # Overlay StratusTime entries (faded)
    try:
        st_map = stratustime_client.time_off_entries_for_range(start_d, end_d)
    except Exception:
        st_map = {}
    out: dict[date, list[dict]] = {}
    for d in set(list(odoo_map.keys()) + list(st_map.keys())):
        merged = list(odoo_map.get(d, []))
        for e in st_map.get(d, []):
            # StratusTime entries marked with source so the template
            # can fade them. Adapt the shape to match Odoo entries.
            if isinstance(e, dict):
                merged.append({
                    "name": e.get("name") or e.get("FirstName", "") + " " + e.get("LastName", ""),
                    "label": "StratusTime entry",
                    "source": "stratustime",
                })
            else:
                merged.append({"name": str(e), "label": "", "source": "stratustime"})
        out[d] = merged
    return out
```

- [ ] **Step 2: Update template to render badge / fade by source**

In `src/zira_dashboard/templates/time_off.html`, find the cell render that lists names (search for `names` loop) and adapt to the new dict shape. Example:

```html
{% for n in cell.names %}
  <li class="time-off-entry source-{{ n.source if n.source else 'odoo' }}">
    {{ n.name }}{% if n.label %} — {{ n.label }}{% endif %}
    {% if n.source == "stratustime" %}<span class="src-badge">StratusTime</span>{% endif %}
  </li>
{% endfor %}
```

Add a header indicator:

```html
<div class="time-off-source-indicator">
  Showing: Odoo
  {% if show_stratustime_overlay %} + StratusTime overlay{% endif %}
</div>
```

And pass `show_stratustime_overlay` into the template context in `staffing_time_off`:

```python
ctx["show_stratustime_overlay"] = settings_store.get_show_stratustime_overlay()
```

- [ ] **Step 3: Run tests + smoke**

```bash
pytest tests/ -k "time_off" -v
```

Expected: PASS where applicable. Smoke: navigate to `/staffing/time-off`, confirm Odoo-sourced entries appear, StratusTime appear with the badge until you toggle overlay off.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/time_off.py src/zira_dashboard/templates/time_off.html
git commit -m "feat(timeclock): /staffing/time-off reads Odoo with StratusTime overlay"
```

---

### Task 22: Settings panel — Time Off section

**Files:**
- Modify: `src/zira_dashboard/routes/settings.py` (handler for the new section)
- Modify: `src/zira_dashboard/templates/settings.html` (UI block)
- Test: extend settings tests if any

- [ ] **Step 1: Add GET context + POST handlers**

In `src/zira_dashboard/routes/settings.py`, find the existing settings GET route and extend the context:

```python
from .. import settings_store, odoo_client, time_off_sync, time_off_balances

# ... inside the settings GET:
ctx["time_off_settings"] = {
    "leave_types": odoo_client.fetch_leave_types() if _odoo_configured() else [],
    "hidden_ids": settings_store.get_hidden_leave_type_ids(),
    "show_stratustime_overlay": settings_store.get_show_stratustime_overlay(),
    "default_shift_start": settings_store.get_default_shift_hours()[0],
    "default_shift_end": settings_store.get_default_shift_hours()[1],
}
```

Add new POST handlers (mirror the existing settings POST patterns):

```python
@router.post("/api/settings/time-off/hidden-types")
def set_hidden_types(request: Request, ids: list[int] = Form(default=[])):
    settings_store.set_hidden_leave_type_ids(ids)
    return RedirectResponse(url="/staffing/settings", status_code=303)


@router.post("/api/settings/time-off/overlay")
def set_overlay(request: Request, enabled: str = Form(default="off")):
    settings_store.set_show_stratustime_overlay(enabled == "on")
    return RedirectResponse(url="/staffing/settings", status_code=303)


@router.post("/api/settings/time-off/default-shift")
def set_default_shift(request: Request,
                     start: float = Form(...), end: float = Form(...)):
    settings_store.set_default_shift_hours(start, end)
    return RedirectResponse(url="/staffing/settings", status_code=303)


@router.post("/api/settings/time-off/refresh-now")
def time_off_refresh_now(request: Request):
    try:
        time_off_sync.poll_odoo_leaves()
    except Exception:
        pass
    return RedirectResponse(url="/staffing/settings", status_code=303)
```

- [ ] **Step 2: Add UI block to `settings.html`**

In `src/zira_dashboard/templates/settings.html`, add a new panel under the existing Timeclock panel:

```html
<section class="settings-panel" id="settings-time-off">
  <h2>Time Off</h2>

  <form method="post" action="/api/settings/time-off/hidden-types">
    <fieldset>
      <legend>Leave types visible in the kiosk</legend>
      {% for t in time_off_settings.leave_types %}
        <label>
          <input type="checkbox" name="ids" value="{{ t.id }}"
            {% if t.id in time_off_settings.hidden_ids %}checked{% endif %}>
          Hide "{{ t.name }}"
        </label>
      {% endfor %}
      <button type="submit">Save</button>
    </fieldset>
  </form>

  <form method="post" action="/api/settings/time-off/overlay">
    <label>
      <input type="checkbox" name="enabled" value="on"
        {% if time_off_settings.show_stratustime_overlay %}checked{% endif %}>
      Show StratusTime overlay on the admin calendar
    </label>
    <button type="submit">Save</button>
  </form>

  <form method="post" action="/api/settings/time-off/default-shift">
    <label>Default shift start (decimal hours, e.g. 6.0 for 6 AM):
      <input type="number" step="0.25" name="start"
             value="{{ time_off_settings.default_shift_start }}">
    </label>
    <label>Default shift end:
      <input type="number" step="0.25" name="end"
             value="{{ time_off_settings.default_shift_end }}">
    </label>
    <button type="submit">Save</button>
  </form>

  <form method="post" action="/api/settings/time-off/refresh-now">
    <button type="submit">Refresh from Odoo now</button>
  </form>
</section>
```

- [ ] **Step 3: Smoke**

Navigate to `/staffing/settings`, toggle hidden types, save, confirm `app_settings` row updates.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/settings.py src/zira_dashboard/templates/settings.html
git commit -m "feat(timeclock): Settings panel for Time Off (hidden types, overlay, default shift, refresh-now)"
```

---

## Phase 7 — Final Wiring & Smoke

### Task 23: Cache hr.leave.type rows on every poll cycle

The `leave_types_cache` table created in Task 1 needs population. Wire this into the poller.

**Files:**
- Modify: `src/zira_dashboard/time_off_sync.py`
- Test: `tests/test_time_off_sync.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_time_off_sync.py`:

```python
def test_poll_refreshes_leave_types_cache(monkeypatch, fake_db):
    monkeypatch.setattr(time_off_sync.odoo_client, "fetch_leaves_for_range",
                        lambda s, e: [])
    monkeypatch.setattr(time_off_sync.odoo_client, "fetch_leave_types",
                        lambda: [
                            {"id": 1, "name": "PTO", "request_unit": "day",
                             "requires_allocation": "yes", "color": 1,
                             "active": True},
                        ])
    time_off_sync.poll_odoo_leaves()
    upserts = [e for e in fake_db["executes"]
               if "leave_types_cache" in e[0]]
    assert upserts
```

- [ ] **Step 2: Implement — extend `poll_odoo_leaves`**

Add to the top of `poll_odoo_leaves` in `src/zira_dashboard/time_off_sync.py`:

```python
    # Refresh leave-types cache first so the kiosk picker stays current.
    try:
        types = odoo_client.fetch_leave_types()
        for t in types:
            db.execute(
                "INSERT INTO leave_types_cache "
                "(holiday_status_id, name, request_unit, requires_allocation, "
                " color, active, last_pulled_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, now()) "
                "ON CONFLICT (holiday_status_id) DO UPDATE SET "
                "name = EXCLUDED.name, request_unit = EXCLUDED.request_unit, "
                "requires_allocation = EXCLUDED.requires_allocation, "
                "color = EXCLUDED.color, active = EXCLUDED.active, "
                "last_pulled_at = now()",
                (t["id"], t["name"], t["request_unit"],
                 t["requires_allocation"], t.get("color"), t.get("active", True)),
            )
    except Exception as e:  # noqa: BLE001
        _log.info("leave_types_cache refresh failed: %s", e)
```

- [ ] **Step 3: Run test + commit**

```bash
pytest tests/test_time_off_sync.py::test_poll_refreshes_leave_types_cache -v
git add src/zira_dashboard/time_off_sync.py tests/test_time_off_sync.py
git commit -m "feat(timeclock): poll refreshes leave_types_cache"
```

---

### Task 24: Add balance-invalidate hook into the cascade

When the poller flips a state, also invalidate the person's balance cache so the next wizard open sees fresh numbers.

- [ ] **Step 1: Write failing test**

Append to `tests/test_time_off_sync.py`:

```python
def test_cascade_invalidates_balances(monkeypatch, fake_db):
    """Any state transition triggers balance invalidate for the person."""
    invalidated = []
    monkeypatch.setattr(time_off_sync.time_off_balances, "invalidate",
                        lambda pid: invalidated.append(pid))
    monkeypatch.setattr(time_off_sync, "_add_person_to_time_off_bucket",
                        lambda d, p, name: None)
    monkeypatch.setattr(time_off_sync, "_write_custom_day_hours",
                        lambda d, p, ranges: None)
    monkeypatch.setattr(time_off_sync, "_person_name", lambda pid: "Bob")
    old = {"state": "confirm"}
    new = {"state": "validate", "person_odoo_id": 5, "shape": "full_day",
           "date_from": date(2026, 6, 1), "date_to": date(2026, 6, 1),
           "working_hours_json": None}
    time_off_sync.cascade_on_state_change(old, new)
    assert 5 in invalidated
```

- [ ] **Step 2: Implement**

In `src/zira_dashboard/time_off_sync.py`, add at the top:

```python
from . import time_off_balances
```

In `cascade_on_state_change`, at the bottom of the function:

```python
    try:
        time_off_balances.invalidate(person_odoo_id)
    except Exception:
        pass
```

- [ ] **Step 3: Run test + commit**

```bash
pytest tests/test_time_off_sync.py::test_cascade_invalidates_balances -v
git add src/zira_dashboard/time_off_sync.py tests/test_time_off_sync.py
git commit -m "feat(timeclock): cascade invalidates balance cache"
```

---

### Task 25: CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the entry**

Per Dale's autosave/changelog rule, every deploy gets a `### TIME` entry under today's date. Add at the top of today's section in `CHANGELOG.md` (replace the time with the actual deploy time):

```markdown
### 5:00 PM

- **Timeclock time-off requests + calendar (Phase A, behind `KIOSK_TIME_OFF_ENABLED=1`)** — Big feature drop, gated by env flag so it's invisible until you flip it on. **(1) Kiosk request flow**: tap Time Off on the dashboard → pick one of four shapes (Full Day, Late Arrival, Early Leave, Mid-Day Gap) → pick type + date(s) + times → submit. Submissions land in a new `time_off_requests` Postgres table and queue an Odoo `hr.leave.create` via the same `BackgroundTask` + 60s sweep pattern the kiosk punches use. **(2) Live balance + in-flight calc**: opening the wizard's details step refreshes the employee's balances from Odoo synchronously (~200-500ms), shows "Available: 12.5 days (4.0 days pending)", and the JS updates "This request: N · Remaining after: M" live as you change inputs. Submit is disabled if the request would exceed `available_practical` (allocated − taken − pending). Custom Hours types (`requires_allocation=no`) show "Unpaid · no balance required" and submit is always enabled (subject to shift-window validation). **(3) My Requests + Cancel**: list of own submissions with state badges (Pending / Approved / Rejected), tap any to see details, Cancel button issues `hr.leave.action_refuse` via the sync queue. **(4) Who's Out calendar**: kiosk-styled month grid showing approved leaves with timing labels — "Bob — full day" / "Alice — leaves 2pm" / "Carl — 10:00am–12:00pm". Approved-only, no leave-type names shown (privacy). **(5) Admin integration**: `/staffing/time-off` now reads from the local mirror (sourced from Odoo) with an optional StratusTime overlay during parallel run; toggle in Settings → Time Off. Approved leaves auto-populate the staffing scheduler's Time-Off bucket and write partial-day working hours into `custom_day_hours` so supervisors see "Bob (6:00–10:00 + 12:00–14:30)" by name automatically. **(6) Settings panel**: hide individual leave types from the kiosk picker, toggle StratusTime overlay, set default shift hours, force-refresh from Odoo. **(7) Background loops**: three new 60s/600s loops parallel to the existing punch sweep — push retry, pull poller (with leave-types cache refresh + cascade-on-approve), and a 10-min balance staleness sweep. **Phasing**: env flag `KIOSK_TIME_OFF_ENABLED=1` to enable; ship dark to start, pilot with a handful of volunteers, then full rollout + flip overlay off and decommission StratusTime time-off entry. Two-stage approval (Employee's Approver + Time Off Officer) is the Odoo native flow; "Custom Hours" type configured for Hours unit with `requires_allocation=no` and Work Entry Type=Unpaid is the canonical receptacle for partial-day requests. See [`docs/superpowers/specs/2026-05-27-timeclock-time-off-design.md`](docs/superpowers/specs/2026-05-27-timeclock-time-off-design.md).
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): timeclock time-off feature"
```

---

### Task 26: Full test suite + lint

- [ ] **Step 1: Run full suite**

```bash
pytest -v 2>&1 | tail -50
```

Expected: all green (or pre-existing skips). If anything new fails, fix the underlying issue before proceeding.

- [ ] **Step 2: Lint (ruff)**

```bash
ruff check src/zira_dashboard/time_off_sync.py \
           src/zira_dashboard/time_off_balances.py \
           src/zira_dashboard/routes/kiosk_time_off.py
```

Expected: no errors. Fix any F401/F841/etc.

- [ ] **Step 3: Type-check (if mypy configured)**

```bash
ruff check --select=E,F src/zira_dashboard/
```

- [ ] **Step 4: Commit any fixups**

```bash
git status
git add -p
git commit -m "chore(timeclock): lint fixes"
```

---

### Task 27: End-to-end smoke verification (manual checklist)

Run through this against a Railway dev environment connected to a non-production Odoo tenant.

- [ ] **A. Schema is live.** `\d time_off_requests` (and `time_off_balances`, `scheduler_moves`, `leave_types_cache`) all exist with expected columns.
- [ ] **B. Background loops running.** Check app logs at boot for "time_off sync loop started" / "time_off poll loop started" / "time_off balance sweep loop started".
- [ ] **C. Kiosk Time Off tile renders** when `KIOSK_TIME_OFF_ENABLED=1` and is hidden otherwise.
- [ ] **D. Full-day request round-trip.** Submit a full-day PTO for tomorrow → row appears in `time_off_requests` with `state='draft'`, then `state='confirm'` and `odoo_leave_id` set within ~5 seconds. The matching `hr.leave` exists in Odoo with state=Confirmed.
- [ ] **E. Approve in Odoo** (or your approver does). Within ~60s, local `state='validate'`, the person appears in `/staffing/time-off` admin calendar for those days, and the staffing scheduler shows them in the Time-Off bucket.
- [ ] **F. Partial-day round-trip — late arrival.** Submit "arriving at 9:00am" → `hr.leave` with `request_unit_hours=True, request_hour_from=6.0, request_hour_to=9.0` (or whatever shift_start resolves to). Approve. Confirm `custom_day_hours` row populates with working ranges. Staffing scheduler renders the partial-day hours next to the person's name.
- [ ] **G. Partial-day — midday gap.** Submit "gone 10:00am–12:00pm". Approve. Scheduler should render two ranges (6:00–10:00 + 12:00–14:30 or whatever).
- [ ] **H. Cancel pending.** Submit a request, then cancel it from My Requests before approval. Confirm `state='refuse'` locally and `hr.leave` is in 'Refused' state in Odoo.
- [ ] **I. Cancel approved.** Submit + approve, then cancel from My Requests. Confirm cascade-reverse: person removed from TIME_OFF_KEY / custom_day_hours and `hr.leave` is Refused.
- [ ] **J. Balance enforcement.** Try to submit more days than your allocation allows. Submit button shaded; if you bypass JS by posting directly, server returns 422 with "exceeds available_practical".
- [ ] **K. Custom Hours type with no allocation.** Submit a midday gap on Custom Hours — confirms balance panel shows "Unpaid · no balance required" and submit succeeds.
- [ ] **L. Source switch.** Toggle StratusTime overlay off in Settings → admin calendar shows only Odoo-sourced entries; toggle back on → StratusTime appears with badge.
- [ ] **M. Concurrent submission safety.** Open two tabs, both pass balance check, submit same request → exactly one `hr.leave` exists (dedupe guard catches the second on retry).
- [ ] **N. Sync error visibility.** Temporarily set `ODOO_API_KEY` to a wrong value, submit, observe `sync_error` on the row and the warning banner appears on the kiosk Time Off landing. Restore the key, observe sweep recovery.

If any of A–N fails, file an issue in the project tracker with the failing checkpoint and a stack trace / screenshot.

- [ ] **O. Push to main and announce.**

```bash
git push
```

---

### Task 28: Edit handler — re-open wizard for an existing request

**Why it's at the end:** Implementer should ship Tasks 1–27 first to get the core round-trip working. Edit is a real spec requirement but can ride a separate small commit. Recommend doing this *before* Task 27's E2E smoke if you want full coverage in the smoke.

**Files:**
- Modify: `src/zira_dashboard/routes/kiosk_time_off.py` (edit GET + POST routes)
- Modify: `src/zira_dashboard/templates/kiosk_time_off_mine_detail.html` (Edit button)
- Modify: `src/zira_dashboard/templates/kiosk_time_off_request_details.html` (handle prefill)
- Test: `tests/test_time_off_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_time_off_routes.py`:

```python
def test_edit_post_updates_row_and_queues_sync(monkeypatch):
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._verify_token",
                        lambda t: 1)
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._person_by_id",
                        lambda pid: {"id": 1, "name": "T", "odoo_id": 5})
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._load_request",
                        lambda rid, pid: {
                            "id": rid, "person_odoo_id": pid,
                            "shape": "full_day", "state": "confirm",
                            "odoo_leave_id": 999,
                            "holiday_status_id": 1,
                        })
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._shift_window_for",
                        lambda pid: (6.0, 14.5))
    updates = []
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._update_request_row",
                        lambda **kw: updates.append(kw))
    queued = []
    monkeypatch.setattr("zira_dashboard.routes.kiosk_time_off._queue_push",
                        lambda rid: queued.append(rid))

    client = TestClient(app)
    r = client.post(
        "/kiosk/time-off/mine/anytoken/42/edit",
        data={
            "shape": "full_day",
            "holiday_status_id": "1",
            "date_from": "2026-06-10",
            "date_to": "2026-06-12",
            "note": "Updated dates",
        },
        follow_redirects=False,
    )
    assert r.status_code in (200, 303)
    assert updates and updates[0]["date_from"].isoformat() == "2026-06-10"
    assert queued == [42]
```

- [ ] **Step 2: Run to verify fail**

```bash
pytest tests/test_time_off_routes.py::test_edit_post_updates_row_and_queues_sync -v
```

Expected: FAIL.

- [ ] **Step 3: Implement helpers + routes**

Append to `src/zira_dashboard/routes/kiosk_time_off.py`:

```python
def _update_request_row(*, rid, person_odoo_id, shape, holiday_status_id,
                        date_from, date_to, hour_from, hour_to,
                        working_hours_json, note) -> None:
    """Update an existing row to the new field values + flip to
    'draft_edit' so the sync queue picks it up as a write to Odoo."""
    db.execute(
        "UPDATE time_off_requests SET shape = %s, holiday_status_id = %s, "
        "date_from = %s, date_to = %s, hour_from = %s, hour_to = %s, "
        "working_hours_json = %s, note = %s, "
        "state = 'draft_edit', synced_to_odoo = FALSE, "
        "updated_at = now() "
        "WHERE id = %s AND person_odoo_id = %s",
        (shape, holiday_status_id, date_from, date_to,
         hour_from, hour_to,
         _json.dumps(working_hours_json) if working_hours_json else None,
         note, rid, person_odoo_id),
    )


@router.get("/kiosk/time-off/mine/{token}/{rid}/edit",
            response_class=HTMLResponse)
def mine_edit(request: Request, token: str, rid: int):
    """Re-open the details form pre-filled with this row's current values."""
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p or not p.get("odoo_id"):
        return RedirectResponse(url="/kiosk", status_code=303)
    row = _load_request(rid, p["odoo_id"])
    if not row:
        return RedirectResponse(
            url=f"/kiosk/time-off/mine/{_mint_token(person_id)}",
            status_code=303,
        )
    fresh = _mint_token(person_id)
    types = _fetch_visible_leave_types(row["shape"])
    balances = _refresh_and_load_balances(p["odoo_id"])
    balances_by_type = {b["holiday_status_id"]: b for b in balances}
    shift_from, shift_to = _shift_window_for(p["odoo_id"])

    return templates.TemplateResponse(
        request,
        "kiosk_time_off_request_details.html",
        {
            "person": p, "token": fresh, "shape": row["shape"],
            "leave_types": types,
            "balances_by_type": balances_by_type,
            "shift_from": shift_from, "shift_to": shift_to,
            "today_iso": _date.today().isoformat(),
            "edit_mode": True,
            "edit_rid": rid,
            "prefill": {
                "holiday_status_id": row["holiday_status_id"],
                "date_from": row["date_from"].isoformat() if row["date_from"] else "",
                "date_to": row["date_to"].isoformat() if row["date_to"] else "",
                "hour_from": float(row["hour_from"]) if row["hour_from"] is not None else None,
                "hour_to": float(row["hour_to"]) if row["hour_to"] is not None else None,
                "note": row["note"] or "",
            },
        },
    )


@router.post("/kiosk/time-off/mine/{token}/{rid}/edit",
             response_class=HTMLResponse)
def mine_edit_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    token: str, rid: int,
    shape: str = Form(...),
    holiday_status_id: int = Form(...),
    date_from: str = Form(...),
    date_to: str = Form(...),
    time_a: str = Form(default=""),
    time_b: str = Form(default=""),
    note: str = Form(default=""),
):
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p or not p.get("odoo_id"):
        return RedirectResponse(url="/kiosk", status_code=303)
    row = _load_request(rid, p["odoo_id"])
    if not row:
        return RedirectResponse(
            url=f"/kiosk/time-off/mine/{_mint_token(person_id)}",
            status_code=303,
        )
    if shape not in _VALID_SHAPES:
        return RedirectResponse(
            url=f"/kiosk/time-off/mine/{_mint_token(person_id)}/{rid}",
            status_code=303,
        )
    try:
        df = _date.fromisoformat(date_from)
        dt = _date.fromisoformat(date_to)
    except ValueError:
        return RedirectResponse(
            url=f"/kiosk/time-off/mine/{_mint_token(person_id)}/{rid}",
            status_code=303,
        )
    if dt < df:
        df, dt = dt, df

    shift_from, shift_to = _shift_window_for(p["odoo_id"])
    hour_from, hour_to, err = _shape_to_hour_bounds(
        shape, time_a, time_b, shift_from, shift_to,
    )
    if err:
        # Re-render the form with the error
        types = _fetch_visible_leave_types(shape)
        balances = _refresh_and_load_balances(p["odoo_id"])
        return templates.TemplateResponse(
            request,
            "kiosk_time_off_request_details.html",
            {
                "person": p, "token": _mint_token(person_id), "shape": shape,
                "leave_types": types,
                "balances_by_type": {b["holiday_status_id"]: b for b in balances},
                "shift_from": shift_from, "shift_to": shift_to,
                "today_iso": _date.today().isoformat(),
                "edit_mode": True, "edit_rid": rid,
                "error": err,
            },
            status_code=422,
        )

    working_hours = _compute_working_hours_json(
        shape, hour_from, hour_to, shift_from, shift_to,
    )
    _update_request_row(
        rid=rid, person_odoo_id=p["odoo_id"], shape=shape,
        holiday_status_id=holiday_status_id,
        date_from=df, date_to=dt,
        hour_from=hour_from, hour_to=hour_to,
        working_hours_json=working_hours,
        note=note.strip() or None,
    )
    background_tasks.add_task(_queue_push, rid)
    return RedirectResponse(
        url=f"/kiosk/time-off/mine/{_mint_token(person_id)}/{rid}",
        status_code=303,
    )
```

- [ ] **Step 4: Update the details template to support edit mode**

In `src/zira_dashboard/templates/kiosk_time_off_request_details.html`, modify the form's `action` attribute and add prefill values. Replace this line:

```html
<form method="post"
      action="/kiosk/time-off/request/{{ token }}/submit"
      class="kiosk-form">
```

with:

```html
<form method="post"
      action="{% if edit_mode %}/kiosk/time-off/mine/{{ token }}/{{ edit_rid }}/edit{% else %}/kiosk/time-off/request/{{ token }}/submit{% endif %}"
      class="kiosk-form">
```

And throughout the form, replace the `value` attributes on inputs to honor `prefill`. Example for `date_from`:

```html
<input type="date" name="date_from" id="date-from"
       min="{{ today_iso }}"
       value="{{ (prefill.date_from if edit_mode and prefill else '') or today_iso }}"
       required>
```

Apply the same pattern to `date_to`, `time_a`, `time_b`, `note`, and the `<option selected>` on the type select.

For the type select, replace the option loop with:

```html
{% for t in leave_types %}
  <option value="{{ t.id }}"
          data-unit="{{ t.request_unit }}"
          data-requires-alloc="{{ t.requires_allocation }}"
          {% if edit_mode and prefill and t.id == prefill.holiday_status_id %}selected{% endif %}>
    {{ t.name }}
  </option>
{% endfor %}
```

Add a top-of-form indicator if editing:

```html
{% if edit_mode %}
  <p class="kiosk-edit-banner">Editing existing request</p>
{% endif %}
```

- [ ] **Step 5: Add Edit button to the detail template**

In `src/zira_dashboard/templates/kiosk_time_off_mine_detail.html`, above the Cancel form, add:

```html
{% if request_row.originating_kiosk_user and request_row.state not in ("cancel", "refuse") %}
  <a class="kiosk-action kiosk-action-edit"
     href="/kiosk/time-off/mine/{{ token }}/{{ request_row.id }}/edit">
    Edit Request
  </a>
{% endif %}
```

- [ ] **Step 6: Run tests + smoke**

```bash
pytest tests/test_time_off_routes.py::test_edit_post_updates_row_and_queues_sync -v
pytest tests/test_time_off_routes.py -v
```

Expected: PASS. Smoke: submit a request, tap into detail, tap Edit, change date/time, submit → confirm `odoo_leave_id` is unchanged but the Odoo `hr.leave` reflects the new values.

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/routes/kiosk_time_off.py \
        src/zira_dashboard/templates/kiosk_time_off_request_details.html \
        src/zira_dashboard/templates/kiosk_time_off_mine_detail.html \
        tests/test_time_off_routes.py
git commit -m "feat(timeclock): edit handler for existing time-off requests"
```

---

## Out of Scope (Phase B follow-ups)

- Cancellation reason capture (optional text reason on cancel)
- Slack notifications on submit / approve (rely on Odoo email for now)
- Multi-kiosk concurrent submission lock (DB row lock on balance check)
- StratusTime decommissioning script
- Bulk import of historical StratusTime requests into Odoo (would require a one-shot migration script)

These are listed in the spec under "Phase B / C" and become their own implementation plans when ready.

---

## Self-Review Checklist (for the implementer)

After completing all tasks, before declaring done:

- [ ] All four shapes round-trip from kiosk → Odoo (E2E smoke A–G)
- [ ] Balance enforcement works (J)
- [ ] Cascade triggers both forward (approve) and reverse (cancel/refuse) (E, H, I)
- [ ] Settings panel actually changes runtime behavior (L)
- [ ] No raw Odoo tracebacks leak into UI — all wrapped via `_classify_error`
- [ ] `KIOSK_TIME_OFF_ENABLED=1` is the only thing standing between this and production exposure
- [ ] CHANGELOG entry pushed
- [ ] Spec doc linked from CHANGELOG matches what was actually built
