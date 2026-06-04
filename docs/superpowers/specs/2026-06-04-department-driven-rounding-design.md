# Department-Driven Punch Rounding — Design

**Date:** 2026-06-04
**Status:** Approved (brainstorming → implementation planning)

## Context

Timeclock punch rounding currently resolves **per Odoo work schedule**. Every
punch calls [`_shift_for_punch`](../../../src/zira_dashboard/routes/timeclock.py)
with the employee's Odoo `resource_calendar_id`; if that calendar has a
configured [`work_schedules`](../../../src/zira_dashboard/work_schedule_store.py)
override (with hours for the punch's weekday), the punch uses **that override's
shift boundaries AND its four rounding windows**. Everyone else falls back to
the plant default — [`global_schedule`](../../../src/zira_dashboard/schedule_store.py)
boundaries + [`rounding_settings`](../../../src/zira_dashboard/rounding_store.py)
id=1. The four-window math in [`apply_rounding`](../../../src/zira_dashboard/rounding.py)
pulls a punch toward the boundary if it lands inside the window.

Dale wants the **rounding policy to follow the work, not the Odoo calendar**.
The same employee should get one rounding system when scheduled on the
recycling line and a different one when scheduled on the tablets. Concretely,
GPI has three rounding systems today:

- **Plant Operator** — used by the **Recycled** and **New** departments.
- **Supervisor** — used by the **Supervisor** department.
- **Transportation** — used by the **Transportation** department.

So the grouping key changes from "Odoo work schedule" to "the **department** of
the work center the employee is working that day."

### Two department concepts — which one this uses

The codebase has two notions of "department," and they disagree exactly where
this feature lives:

- **`staffing.Location.department`** (static, in
  [`staffing.py`](../../../src/zira_dashboard/staffing.py)): one of
  `Recycled / New / Supervisor / Maintenance / Transportation`. Here **Tablets →
  Supervisor**, **Truck Driver → Transportation**, Dismantlers/Repairs →
  Recycled. This matches Dale's mental model.
- **`work_centers_store.department`** (user-editable value-stream association):
  Tablets/Loading/Work Orders are set to **Recycled**. The recycling man-hours
  math deliberately *avoids* this one for the same reason.

**This feature keys off the static `Location.department`.**

### What stays the same

Per the design decisions made during brainstorming:

- **Shift boundaries stay Odoo-sourced.** Only the *selection of the four
  windows* becomes department-driven. Hours still resolve from the employee's
  Odoo work schedule (synced into `work_schedules`) or the plant default — so
  drivers still round toward their 5:45 start from Odoo.
- **`apply_rounding` is untouched.** The four-window logic does not change.

## Goals

1. Select a punch's four rounding windows by the **department of the work center
   the employee is working that day**, resolved as: their **scheduled** WC for
   the day → its static department; falling back to the WC they **clock into**
   when they aren't on the published schedule.
2. Model the three rounding systems (**Plant Operator**, **Supervisor**,
   **Transportation**) as named, editable sets of four windows, each assignable
   to one or more departments.
3. Keep **shift boundaries Odoo-sourced** exactly as today (per-calendar synced
   hours → plant default).
4. Leave a safe **plant default** (`rounding_settings` id=1) as the ultimate
   fallback for any punch that can't resolve to a mapped department.
5. Give Dale settings controls to edit the systems and the
   department→system map.

## Non-goals

- **Per-employee rounding.** The key is the department of the day's work, shared
  by everyone working that department.
- **Changing `apply_rounding`.** The four-window math is unchanged.
- **Moving shift hours off Odoo.** Boundaries stay synced from Odoo
  `resource.calendar`; rounding systems do **not** carry hours.
- **Rewriting historical punches.** Migration only seeds config; no
  `timeclock_punches_log` rows are recomputed.
- **Overnight shifts.** Still unsupported (existing `apply_rounding` limitation).
- **Live Odoo reads on the punch path.** Hours remain synced-local.
- **Per-work-center rounding** (finer than department). Out of scope; the
  department is the grouping.

## Design

### Two-part resolution at punch time

`_open_log_row` resolves **hours** and **windows** from two independent
lookups, then calls `apply_rounding(action, occurred_at, start, end, windows)`
exactly as today.

**a) Hours / boundary — unchanged.** Keep today's logic: the employee's
`people.resource_calendar_id` → `work_schedule_store.get(cal_id)` hours for this
weekday → else plant default (`global_schedule` via `shift_config`). Factor the
hours half of the current `_shift_for_punch` into `_hours_for_punch(odoo_id,
local_date) -> tuple[time, time]`.

**b) Windows — new, department-driven.**

```
def _windows_for_day(person_name, local_date, effective_wc) -> RoundingSettings:
    # 1. Department for the day.
    dept = None
    sched = staffing.load_schedule(local_date)              # cached per-day
    scheduled_wc = first WC in sched.assignments that lists person_name
    if scheduled_wc is not None:
        dept = LOCATION_DEPARTMENT.get(scheduled_wc)        # static Location.department
    elif effective_wc:
        dept = LOCATION_DEPARTMENT.get(effective_wc)
    # 2. Department -> system -> windows.
    if dept:
        win = rounding_system_store.windows_for_department(dept)   # cache read
        if win is not None:
            return win
    # 3. Fallback: plant default.
    return rounding_store.current()
```

- `LOCATION_DEPARTMENT` is a `{location_name: Location.department}` dict built
  once from `staffing.LOCATIONS` (module-level constant; static data).
- `effective_wc` is the WC that anchors the **clock-in fallback**:
  - **clock_in:** the form `wc_name` being clocked into.
  - **clock_out:** the employee's currently clocked-in WC, read from
    `_current_state(odoo_id)["current_wc"]` (clock-out punches carry no
    `wc_name`).
  - **transfer_in / transfer_out:** never rounded, so the resolver isn't
    consulted.
- **Multi-department day** (person listed under WCs in different departments):
  the **first** scheduled WC (schedule order) wins. Documented limitation; if
  splits across departments become common we add a punch-WC tiebreak.

Hot-path cost: one `staffing.load_schedule` call (already cached per-day, and
already loaded on most punch-adjacent paths) plus in-process dict/cache reads.
No new DB round-trips on the punch. The existing try/except in `_open_log_row`
that preserves the raw punch on any rounding failure stays in place.

### Data model

Two new tables. `rounding_settings` id=1 is **unchanged** and becomes the
explicit "plant default / ultimate fallback."

```sql
CREATE TABLE IF NOT EXISTS rounding_systems (
  id             SERIAL PRIMARY KEY,
  name           TEXT NOT NULL UNIQUE,        -- "Plant Operator", "Supervisor", "Transportation"
  in_before_min  INTEGER NOT NULL DEFAULT 0,
  in_after_min   INTEGER NOT NULL DEFAULT 0,
  out_before_min INTEGER NOT NULL DEFAULT 0,
  out_after_min  INTEGER NOT NULL DEFAULT 0,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS department_rounding (
  department TEXT PRIMARY KEY,                -- static Location.department
  system_id  INTEGER REFERENCES rounding_systems(id) ON DELETE SET NULL
);
```

`ON DELETE SET NULL` means deleting a system leaves the mapping row pointing at
nothing → resolution falls through to the plant default. Safe.

The existing **`work_schedules` table stays** — it still supplies the
per-Odoo-schedule hours that `_hours_for_punch` reads. Its four rounding-window
columns simply stop being read (left in place; harmless, no migration to drop
them).

### `rounding_system_store.py` (new)

Mirrors [`rounding_store`](../../../src/zira_dashboard/rounding_store.py): an
in-process cache behind an `RLock`, invalidated on every write, so the punch
path is a cache read and never a DB hit.

```python
@dataclass(frozen=True)
class RoundingSystem:
    id: int
    name: str
    rounding: RoundingSettings

def windows_for_department(department: str) -> RoundingSettings | None  # punch path
def all_systems() -> list[RoundingSystem]                              # settings UI
def department_map() -> dict[str, int | None]                          # settings UI
def save_system(name: str, r: RoundingSettings) -> None                # create/update by name
def rename_system(system_id: int, new_name: str) -> None
def delete_system(system_id: int) -> None
def set_department_system(department: str, system_id: int | None) -> None
def reload() -> None
```

The cache holds both tables joined: `{department: RoundingSettings}` for the
hot-path lookup, plus the raw systems list + map for the UI. One `reload()`
rebuilds all of it.

### Punch-time resolver changes (`routes/timeclock.py`)

- Split `_shift_for_punch` into `_hours_for_punch` (today's hours logic) and
  `_windows_for_day` (above).
- `_open_log_row` computes `effective_wc` from `action` (form `wc_name` on
  clock-in; `_current_state` current WC on clock-out), looks up the person's
  name once (extend the existing `people`-by-`odoo_id` query — which already
  fetches `resource_calendar_id` for the hours half — to also return `name`),
  then calls both resolvers and `apply_rounding`.

### Settings UI (`routes/settings.py` + `settings.html`)

The rounding area becomes four blocks:

1. **Plant default** — the existing global `/settings/rounding` form. Relabeled
   as the ultimate fallback. Unchanged behavior.
2. **Rounding systems** *(new)* — one card per `rounding_systems` row: name +
   the four `0–60` window inputs + Save; plus add / rename / remove controls.
3. **Department rounding** *(new)* — a row per static department (Recycled, New,
   Supervisor, Maintenance, Transportation) with a dropdown choosing a system or
   "Plant default" (NULL). Saving writes `department_rounding`.
4. **Custom shift hours** *(reframed from today's "Per-schedule rounding")* —
   the same "add an Odoo work schedule" control and read-only synced-hours
   display, **minus the rounding inputs** (those moved to block 2). This is
   still how non-default hours — the drivers' 5:45–2:30 — get registered for the
   boundary.

New POST routes mirror the existing `/settings/rounding` validation (`0–60`
clamp): `/settings/rounding_system` (save), `/settings/rounding_system/add`,
`/settings/rounding_system/remove`, `/settings/department_rounding` (set map).

### Migration / seeding (one-time, in `db.py`)

After `CREATE TABLE`, seed idempotently (`ON CONFLICT DO NOTHING`):

- `rounding_systems`:
  - **Plant Operator** = current `rounding_settings` id=1 values.
  - **Transportation** = the existing "Drivers" `work_schedules` rounding
    (expected `20/0/0/0`) if such a row exists, else `0/0/0/0`.
  - **Supervisor** = `0/0/0/0` (Dale sets it later).
- `department_rounding`: Recycled→Plant Operator, New→Plant Operator,
  Supervisor→Supervisor, Transportation→Transportation,
  **Maintenance→Plant Operator**.
- `rounding_settings` id=1 untouched. No `timeclock_punches_log` rows are
  recomputed.

## Acceptance criteria

- An employee **scheduled** to Dismantler 1 (Recycled) rounds with the **Plant
  Operator** system; the **same** employee scheduled to Tablets (Supervisor) the
  next day rounds with the **Supervisor** system.
- An employee **scheduled** to Truck Driver (Transportation) rounds with the
  **Transportation** system, toward their Odoo 5:45 start (hours unchanged).
- An employee **not on the published schedule** rounds by the department of the
  WC they **clock into**; their **clock-out** uses the department of their
  currently clocked-in WC.
- An employee with **no schedule entry and no resolvable WC** rounds with the
  **plant default**.
- A department whose mapping is unset, or whose system was deleted, rounds with
  the **plant default**.
- **Shift boundaries are identical** to today for every employee (hours logic
  unchanged); only the windows differ.
- `transfer_in` / `transfer_out` are never rounded.
- Editing a system on the settings page changes rounding for every department
  mapped to it on the next punch; `0–60` validation enforced.
- Production analytics (leaderboard / staffing / dashboards) are unaffected —
  this touches only the kiosk punch path.

## Risks

- **Clock-out WC lookup.** Resolving the current WC at clock-out adds a read of
  attendance state. It must be cheap and must fail safe to the plant default
  (never block or mis-round a clock-out). Wrap in the existing try/except.
- **Schedule not published before early clock-ins.** Drivers/openers may punch
  before the day's schedule exists. Handled by the clock-in-WC fallback, but
  worth confirming the publish cadence; if schedules routinely lag, the
  clock-in-WC path becomes the common case (still correct).
- **Person under multiple departments in one day.** First-scheduled-WC wins; a
  documented simplification. Revisit with a punch-WC tiebreak if it bites.
- **Name vs odoo_id join.** Schedule assignments are keyed by person **name**;
  the punch path has `odoo_id`. The single `people` lookup must return both;
  a name mismatch (rename mid-day) → no scheduled WC found → clock-in fallback.
- **Driver scheduled off Transportation.** Gets Odoo 5:45 hours + the windows of
  wherever they're scheduled. Intended: windows follow the work.
- **Vestigial `work_schedules` rounding columns.** Left unread to avoid a
  migration; a later cleanup can drop them.

## File touch list

- Modify: `src/zira_dashboard/db.py` — `CREATE TABLE rounding_systems`,
  `department_rounding`; idempotent seed.
- New: `src/zira_dashboard/rounding_system_store.py` — cached store +
  `RoundingSystem`.
- Modify: `src/zira_dashboard/routes/timeclock.py` — split `_shift_for_punch`
  into `_hours_for_punch` + `_windows_for_day`; `effective_wc` in
  `_open_log_row`.
- Modify: `src/zira_dashboard/routes/settings.py` — GET context (systems +
  department map + available systems); POST save / add / remove / set-map.
- Modify: `src/zira_dashboard/templates/settings.html` — four-block rounding
  area; relabel global form; reframe per-schedule block to hours-only.
- New tests: `tests/test_rounding_system_store.py` (cache / CRUD / map, mirroring
  `test_rounding_store.py`); `tests/test_rounding_windows_for_day.py` (scheduled
  → dept → system; clock-in fallback; clock-out via current WC; multi-dept
  tiebreak; unmapped → default; driver hours + Transportation windows); settings
  route GET/POST/validation tests.
- Update: `CHANGELOG.md` (per deploy).

## Testing note

Per the local Python constraint (only the Odoo-bundled runtime is available;
the suite targets a newer Python), the full pytest suite runs in **CI** (the
GitHub Actions workflow with the Postgres service) and on Railway. Locally,
verify with `ruff` + `py_compile` and a small ast-exec smoke of the pure
resolution helpers (`_windows_for_day` against a stub schedule + system map).
