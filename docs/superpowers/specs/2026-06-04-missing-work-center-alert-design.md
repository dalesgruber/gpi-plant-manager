# Missing-Work-Center Alert — Design

**Date:** 2026-06-04
**Status:** Approved (brainstorming → implementation planning)

## Context

Punch rounding and production attribution both now key off each punch's
**work center → department** (see
[department-driven-rounding](2026-06-04-department-driven-rounding-design.md)).
A clock-in that carries no work center resolves to no department, so it earns
**no rounding and no production credit**. The kiosk requires a work center at
clock-in, so the real gap is `hr.attendance` records created **outside** the
kiosk — directly in Odoo, or by another system — which never got a work-center
tag.

Dale wants an alert, **modeled on the existing Late/Absence report**, that
surfaces anyone with an attendance record missing a work center and lets a
manager assign one.

### How the Late/Absence report works (the pattern to mirror)

It is NOT a page banner — it's a **nav badge injected by the shared
[`footer.js`](../../../src/zira_dashboard/static/footer.js) on every page**
("🚨 3 Late/Absence", class `late-nav-badge`) that polls `/api/late-report`;
clicking it opens a **modal** (`late-modal`) with actionable rows (Snooze /
Declare Absent / capture reason). Pure logic lives in
[`late_report.py`](../../../src/zira_dashboard/late_report.py); the route
returns the structured sections; `footer.js` renders the badge + modal.
Because `footer.js` loads on every page, the badge appears app-wide — including
the timeclock and the dashboards.

## Goals

1. Surface, as a **nav badge + modal that look and function exactly like the
   Late/Absence report**, every `hr.attendance` record from the **last 14 days**
   whose **kiosk work-center field is empty**, for **hourly** employees.
2. Let a manager **Assign** a work center to a flagged record from the modal —
   writing the WC (and resolved department) onto the Odoo attendance — or
   **Dismiss** a record that legitimately has no WC.
3. Keep the badge/modal off the hot path: a background warmer caches the
   Odoo result; the badge polls a cached endpoint.

## Non-goals

- **Local punch log as a source.** Source is Odoo `hr.attendance` (system of
  record). The kiosk requires a WC at clock-in, so local clock-ins already have
  one; we don't scan `timeclock_punches_log` for missing WCs.
- **All-time history.** Window is the last 14 days. Pre-kiosk / StratusTime-era
  records legitimately have no WC and would be noise.
- **Salaried staff.** Managers clock in without a WC legitimately; hourly only
  (mirrors the Late report's wage-type filter). **Unlike** the Late report, we
  do **not** also exclude flexible-schedule people — a flexible *hourly*
  employee still needs a WC for production credit; flexibility only matters for
  "lateness", which this isn't.
- **Changing rounding math** or the department-driven resolver.

## Design

### Detection + caching (off the hot path)

A background warmer loop in [`app.py`](../../../src/zira_dashboard/app.py),
mirroring `_warm_odoo_attendance_loop`, runs every ~3 minutes:

```
since = today - 14 days
rows = odoo_client.fetch_attendances_missing_wc(since)   # one batched Odoo read
missing_wc.write_cache(rows)                              # single-row JSONB cache
```

- **`odoo_client.fetch_attendances_missing_wc(since) -> list[dict]`** —
  `search_read` on `hr.attendance` with domain
  `[("check_in", ">=", since), (WC_FIELD, "=", False)]` where `WC_FIELD =
  _kiosk_wc_field()`; fields `id, employee_id, check_in, check_out`. Returns
  `[{att_id, employee_odoo_id, employee_name, check_in, check_out}, ...]`
  (employee_id unwrapped via `unwrap_m2o`; name from the m2o label).
  **If `_kiosk_wc_field()` is None (WC field not configured), returns `[]` and
  logs a one-line config warning** — the alert safely shows nothing rather than
  flagging every record.
- The domain returns only untagged records, which is a small set (kiosk punches
  are tagged), so the query is cheap.

New cache table (single row, like the other `today_*_cache` tables):

```sql
CREATE TABLE IF NOT EXISTS missing_wc_cache (
  id           INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  snapshot     JSONB NOT NULL DEFAULT '[]'::jsonb,
  refreshed_at TIMESTAMPTZ
);
```

### Suppression (assigned or dismissed)

```sql
CREATE TABLE IF NOT EXISTS missing_wc_resolved (
  attendance_id BIGINT PRIMARY KEY,
  action        TEXT NOT NULL CHECK (action IN ('assigned','dismissed')),
  name          TEXT,
  wc_name       TEXT,
  resolved_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

A row here hides the attendance from the modal immediately (before the next
warmer pass re-confirms it). Assigned records also naturally leave the Odoo
query once their WC is set; dismissed records (legitimately WC-less) stay
suppressed.

### `missing_wc.py` (new — mirrors `late_report.py`)

Pure-ish layer; no Odoo calls (the warmer owns those):

```python
def write_cache(rows: list[dict]) -> None          # warmer writes the snapshot
def _read_cache() -> list[dict]                     # raw cached rows
def current_rows() -> list[dict]                    # banner data (see below)
def resolve(attendance_id, action, name=None, wc_name=None) -> None  # upsert suppression
def resolved_ids() -> set[int]
```

`current_rows()` shapes the modal/badge payload from local reads only:
1. Read the cached snapshot.
2. Join each `employee_odoo_id` to `people` (name, `wage_type`); **keep only
   `wage_type == 'hourly'`** and active/non-excluded people.
3. Drop `attendance_id` in `resolved_ids()`.
4. Shape each row: `{attendance_id, name, check_in_label ("6:58 AM Mon"),
   employee_odoo_id}`. One row **per attendance record** (each needs its own WC).
5. Sort by check_in descending.

### Routes (mirror the `/api/late-report` route location)

- **`GET /api/missing-wc`** → `{count, rows}` from `current_rows()` (the badge
  polls this; cheap, all-local).
- **`POST /missing-wc/assign`** (`attendance_id`, `wc_name`) →
  `odoo_client.set_attendance_wc(attendance_id, wc_name)` (writes the WC field +
  resolves & writes the Odoo `department_id`); if a matching local
  `timeclock_punches_log` row exists for that person/time, set its `wc_name` and
  re-run `apply_rounding` for it; then `missing_wc.resolve(att_id, 'assigned',
  name, wc_name)`. Validates `wc_name` is a known `staffing.LOCATIONS` name;
  400 on bad input. Returns `{ok: true}`.
- **`POST /missing-wc/dismiss`** (`attendance_id`) →
  `missing_wc.resolve(att_id, 'dismissed', name)`. Returns `{ok: true}`.

The assign route's local-punch re-round is best-effort (wrapped) — the primary
effect is tagging the Odoo attendance's department; many flagged records are
Odoo-origin with no local punch to re-round.

### Badge + modal (`footer.js`, mirrors the late block)

A new block mirroring `initLateBadge` (~`footer.js:351`):
- Poll `GET /api/missing-wc`; when `count > 0`, inject a badge
  **"📍 N No Work Center"** (new `.mwc-nav-badge`, styled like `.late-nav-badge`).
- Click → modal (`.mwc-modal`, same structure/classes-of-style as `.late-modal`)
  listing each row: `name · "clocked in 6:58 AM Mon"` with two inline actions
  (mirroring the late report's expand-in-place rows):
  - **Assign** → reveals a `<select>` of the 22 `staffing.LOCATIONS` names +
    Save → `POST /missing-wc/assign` → row shows "Assigned ✓", removed on
    refresh.
  - **Dismiss** → `POST /missing-wc/dismiss` → row removed.
- Reuse the late report's CSS by sharing class names where practical (or
  parallel `.mwc-*` rules in the same stylesheet).

## Acceptance criteria

- An `hr.attendance` from the last 14 days with no kiosk WC tag, for an hourly
  employee, appears as a row in the "📍 N No Work Center" modal.
- Salaried employees and records older than 14 days never appear.
- **Assign** writes the WC + department onto the Odoo attendance (verified via
  `set_attendance_wc`), and the row disappears (suppressed immediately, and
  absent from the next warmer pass).
- **Dismiss** removes the row and it stays gone (suppressed).
- When the Odoo kiosk WC field is not configured, the badge shows nothing and a
  config warning is logged (no false-flagging).
- The badge/modal render identically in look + interaction to the Late/Absence
  report, on every page (via `footer.js`).
- `/api/missing-wc` does no Odoo I/O (reads the cache); the warmer owns the
  Odoo query.

## Risks

- **WC field configuration.** The whole feature depends on
  `ODOO_KIOSK_WC_FIELD`. Confirmed-unset → no-op + log (acceptance criterion).
  Confirm it's set in the deploy env during implementation.
- **Employee → person mapping.** Join `hr.attendance.employee_id` (Odoo id) to
  `people.odoo_id`; a record whose employee isn't in `people` is skipped
  (can't determine wage_type). Acceptable.
- **Cache staleness vs. assign.** Bridged by the `missing_wc_resolved` table so
  an assigned/dismissed row drops immediately; the next warmer pass reconciles.
- **Local re-round is partial.** Odoo-origin attendance has no local punch, so
  only the department gets fixed (production credit / reports). Rounding
  re-applies only when a kiosk punch exists. Intended.
- **Warmer cost.** One 14-day `hr.attendance` search every ~3 min, filtered to
  untagged records (small result set). Cheap; mirrors the existing warmer.

## File touch list

- Modify: `src/zira_dashboard/_schema.py` — `missing_wc_cache` +
  `missing_wc_resolved` tables.
- Modify: `src/zira_dashboard/odoo_client.py` —
  `fetch_attendances_missing_wc(since)`.
- New: `src/zira_dashboard/missing_wc.py` — cache + shaping + resolve
  (mirrors `late_report.py`).
- Modify: `src/zira_dashboard/app.py` — `_warm_missing_wc_loop` warmer.
- Modify/New: the routes module that defines `/api/late-report` — add
  `GET /api/missing-wc`, `POST /missing-wc/assign`, `POST /missing-wc/dismiss`.
- Modify: `src/zira_dashboard/static/footer.js` — badge + modal block.
- Modify: `src/zira_dashboard/static/footer.css` (and/or the late report's
  stylesheet) — `.mwc-*` styling (reuse late classes where practical).
- New tests: `tests/test_missing_wc.py` (shaping: hourly filter, resolved
  subtraction, label formatting; Postgres for cache/resolve); mocked
  `fetch_attendances_missing_wc` (domain + WC-field-unset no-op); the assign
  route (mocked `set_attendance_wc`, resolve recorded).

## Testing note

Postgres-backed tests run in CI (the `DATABASE_URL` gate); they skip locally
where no test DB is set. Pure shaping/format helpers should be unit-testable
without a DB (pass cached rows + a fake people map in). Verify locally with
`ruff` + `py_compile`; CI is the authority for the DB-gated paths (it caught a
stale contract test in the rounding work — same care here: grep for any test
asserting nav-badge / footer behavior before changing `footer.js`).
