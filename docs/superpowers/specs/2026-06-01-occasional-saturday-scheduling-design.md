# Occasional Saturday Scheduling — Design

**Date:** 2026-06-01
**Status:** Approved (brainstorming → implementation planning)

## Context

GPI occasionally works a Saturday — typically **6:00 AM – 12:00 PM** with a
**8:00–8:15 break** and a **10:00–10:30 lunch**. Two things need to hold for
those Saturdays:

1. People can clock in/out at the kiosk when they're working a scheduled
   Saturday.
2. When Dale schedules a Saturday, the day's hours **default to the plant
   Saturday schedule** (the hours above) — still editable per Saturday — and
   however those hours end up set, **punch rounding snaps to that Saturday's
   start/end**.

Reading the current code reframes most of this as already-solved:

- **Punching is never blocked by weekday.** The clock-in/out/transfer
  handlers in [`routes/timeclock.py`](../../../src/zira_dashboard/routes/timeclock.py)
  gate only on token validity, an active person, and the salaried→time-off
  redirect. The Odoo `hr.attendance` write path has no work-day guard either.
  A Saturday punch already works.
- **Rounding already follows a day's scheduled hours — when the day is
  published.** [`_shift_for_punch`](../../../src/zira_dashboard/routes/timeclock.py)
  falls back to `shift_config.shift_start_for(date)` / `shift_end_for(date)`,
  which honor a day's `custom_hours` on a **published** schedule
  (`_published_custom_hours` in
  [`shift_config.py`](../../../src/zira_dashboard/shift_config.py)). And
  `is_workday()` already has a "published-Saturday escape hatch" — a Saturday
  counts as a workday once a schedule is published for it. That published-only
  gate was introduced deliberately (commit `43d99a1`, "source hours from
  published schedule only") so drafts never leak into live dashboards.

Dale confirmed his workflow: he **publishes** a Saturday schedule (assign
people, hit Publish) like any weekday. So the publish-then-punch path is
already reliable.

The genuine gap is the **auto-default**: today nothing pre-fills Saturday
hours, so Dale would have to open the scheduler's "Hours" pill and type
6a–12p + the two breaks by hand every Saturday. This design fills that gap.

## Goals

1. Give Saturdays a **plant-wide default schedule** (6a–12p + the two breaks),
   **editable in Settings**, that applies automatically when a Saturday is
   scheduled — no manual entry per Saturday.
2. Keep every individual Saturday **customizable** via the existing Hours pill;
   a per-day override beats the Saturday default.
3. Make **punch rounding** on a published Saturday snap to that Saturday's
   start/end (6:00 / 12:00 by default), using the **existing plant grace
   windows** — falling straight out of the existing `_shift_for_punch` path
   with no new punch-path code.
4. Make the scheduler **show** the Saturday default (and any per-day override)
   while editing, including on a draft, so what-you-see is what-will-apply.
5. Leave weekdays, the published-gate for dashboards, and the per-schedule
   (Odoo work-schedule) rounding feature **unchanged**.

## Non-goals

- **Sunday / holiday defaults.** Saturday-only (weekday 5). A Sunday with
  different hours wouldn't fit one weekend default; out of scope.
- **Separate Saturday rounding windows.** Reuse the plant grace windows
  (`rounding_store.current()`), applied to the Saturday boundaries. No new
  rounding configuration.
- **Reordering `_shift_for_punch` precedence.** An Odoo work-schedule override
  with hours for the weekday still wins at punch time (see Assumptions).
- **Honoring DRAFT custom hours in dashboards / rounding.** The published-only
  gate stays for every metric and the punch path. Only the *scheduler's own
  editor display* reads ungated (configured) hours.
- **Materializing the default into a day's `custom_hours`.** The default is a
  resolution layer, not a copy written onto each Saturday row.
- **Auto-publishing Saturdays.** Dale publishes, as he does today.
- **Overnight shifts.** Still unsupported (existing limitation); 6a–12p is
  same-day.

## Decisions (locked during brainstorm)

| Decision | Choice | Reason |
|---|---|---|
| Mechanism | **Resolution layer** in `shift_config` | A published Saturday rounds correctly on publish alone; rounding falls out of the existing `_shift_for_punch` fallback; no new write paths. |
| Default source | **Editable in Settings** | Dale owns the Saturday hours/breaks without a code change, mirroring the existing Company Schedule editor. |
| Scope | **Saturday only** (weekday 5) | Matches the stated need; avoids a one-size weekend default. |
| Rounding windows | **Reuse plant windows** | "Rounding works according to that schedule" = snap to the Saturday start/end with the normal grace. |
| Override employees on Saturday | **Fall through to plant Saturday hours** | On a plant Saturday everyone is on 6a–12p; override employees are assumed to have no Saturday hours in Odoo. |

## Resolution model

A day's effective hours resolve in this order (the **only** new rung is the
Saturday default):

```
published per-day custom_hours        (existing — total replacement when set)
  └─ else, Saturday (weekday == 5)?  →  Saturday default      (NEW)
       └─ else                        →  weekday global schedule (existing)
```

This single change makes both **punches** and **dashboards** correct for a
published Saturday. `is_workday()` is **unchanged**: a Saturday still only
counts as a workday once **published**, so an unscheduled Saturday stays inert
(no dashboard or rounding effect).

### Two views: gated vs. configured

The published-only gate is right for dashboards and the punch path, but wrong
for the scheduler's own editor (which must show a draft's configured hours).
So the resolver takes a `published_only` flag:

- **Gated** (`published_only=True`) — dashboards, progress, and the punch path.
  A per-day override applies only when its schedule is **published**; otherwise
  the day falls to the Saturday default (if Saturday) or the weekday default.
  The public names stay: `shift_start_for(day)`, `shift_end_for(day)`,
  `breaks_for(day)`, `productive_minutes_for(day)`, `in_shift_on`,
  `shift_elapsed_minutes`.
- **Configured** (`published_only=False`) — the scheduler editor only. A
  per-day override applies whether or not it's published, so the Hours pill and
  editor show what *will* apply once published. New helpers, e.g.
  `configured_shift_start_for(day)` / `_end_` / `_breaks_`.

Sketch (names final in the plan):

```python
SATURDAY = 5

def _custom_hours(day, *, published_only):
    from . import staffing                      # lazy: avoid shift_config→staffing cycle
    sched = staffing.load_schedule(day)
    if published_only and not getattr(sched, "published", False):
        return None
    ch = sched.custom_hours
    return ch if isinstance(ch, dict) else None

def _default_start_for(day):
    if day.weekday() == SATURDAY:
        return saturday_schedule_store.current().shift_start
    return shift_start()                         # weekday global default

def _resolve_start(day, *, published_only):
    ch = _custom_hours(day, published_only=published_only)
    if ch and isinstance(ch.get("start"), str):
        try:
            return time.fromisoformat(ch["start"])
        except ValueError:
            pass
    return _default_start_for(day)
```

`breaks` keeps its current semantics precisely: a per-day `custom_hours` with a
`breaks` key (**even an empty list** = "no breaks today") wins; only when there
is **no** per-day `breaks` override do we fall to the default — the Saturday
default breaks on a Saturday, the weekday breaks otherwise.

## Data model + store

A singleton, mirroring [`schedule_store`](../../../src/zira_dashboard/schedule_store.py):

```sql
CREATE TABLE IF NOT EXISTS saturday_schedule (
  id           INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  shift_start  TIME NOT NULL,
  shift_end    TIME NOT NULL,
  breaks       JSONB NOT NULL DEFAULT '[]'::jsonb,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

New `saturday_schedule_store.py`, reusing `schedule_store.Break` and its
parse/format conventions, with the same in-process `RLock` cache (the resolver
runs in hot loops, so `current()` must be a cached read like
`schedule_store.current()`):

```python
@dataclass(frozen=True)
class SaturdaySchedule:
    shift_start: time
    shift_end: time
    breaks: tuple[Break, ...]            # schedule_store.Break

DEFAULT = SaturdaySchedule(
    shift_start=time(6, 0),
    shift_end=time(12, 0),
    breaks=(Break(time(8, 0), time(8, 15), "Morning break"),
            Break(time(10, 0), time(10, 30), "Lunch")),
)

def current() -> SaturdaySchedule    # cached singleton; DEFAULT if no row yet
def save(s: SaturdaySchedule) -> None # upsert id=1; invalidate cache
def reload() -> SaturdaySchedule
```

No seed migration: like `global_schedule`, `current()` returns `DEFAULT` until
the first save inserts a row. The Settings panel shows `DEFAULT` initially,
then the saved row.

## Settings UI

Under the existing **Company Schedule** editor in
[`settings.html`](../../../src/zira_dashboard/templates/settings.html)
(`POST /settings/schedule`), add a **"Saturday Default"** sub-section —
shift start/end + break rows, **no** weekday checkboxes — backed by
`saturday_schedule_store`. A short note: *"Applied to Saturdays in the
scheduler. Any individual Saturday can still be customized from its Hours
pill."*

- GET `/settings`: add `saturday_schedule` context (shaped like the existing
  `schedule_ctx`).
- `POST /settings/saturday_schedule`: parse `shift_start`/`shift_end` +
  indexed break fields, validate `start < end` and each break within the
  shift (mirroring `settings_save_schedule`), `saturday_schedule_store.save(...)`.

## Scheduler display

In [`routes/staffing.py`](../../../src/zira_dashboard/routes/staffing.py),
compute the pill/editor hours from the **configured** (ungated) resolver
instead of the gated `shift_start_for(d)`, and pass a `hours_source` flag:

- `"custom"` — `sched.custom_hours is not None`
- `"saturday_default"` — else `d.weekday() == 5`
- `"weekday_default"` — else

[`staffing.html`](../../../src/zira_dashboard/templates/staffing.html):

- The Hours pill renders all three states; `saturday_default` reads e.g.
  **`Hours · 6:00–12:00 · 2 breaks · Saturday default`** in a style distinct
  from both plain weekday and `custom`. Generalize the current
  `has_custom_hours` checks (pill label/breaks count at lines ~23, 135, 139,
  315) to switch on `hours_source`.
- The editor pre-fills from the (now ungated) `eff_hours_*` / `eff_breaks`, so
  opening the Hours editor on a Saturday shows 6a–12p + the two breaks.
- **"Reset to defaults"** clears `custom_hours`; the resolver then returns the
  Saturday default on a Saturday — no special-casing needed.

This ungated display also fixes a pre-existing quirk: editing a published day's
assignments flips it to draft, after which the gated path hid that day's
configured hours from the editor. The dashboards/punch paths stay gated and
unchanged.

## Timeclock / rounding

**No new punch-path code.** On a published Saturday, for an employee with no
Odoo work-schedule override (or one lacking Saturday hours),
[`_shift_for_punch`](../../../src/zira_dashboard/routes/timeclock.py) falls
through to:

```
shift_config.shift_start_for(saturday)  → 06:00   (Saturday default)
shift_config.shift_end_for(saturday)    → 12:00
rounding_store.current()                → plant grace windows
```

[`apply_rounding`](../../../src/zira_dashboard/rounding.py) then snaps a
`clock_in` near 6:00 up to 6:00 and a `clock_out` near 12:00 to 12:00 within
the plant windows; `transfer_in`/`transfer_out` are never rounded. The
`try/except` that preserves the raw punch on any rounding failure is untouched.

## Assumptions

- **Override employees have no Saturday hours in Odoo.** `_shift_for_punch`
  checks an employee's Odoo work-schedule override *first*; if that override
  defines hours for the weekday, those win (existing precedence). We assume
  Drivers / override schedules don't include Saturday, so on a plant Saturday
  they fall through to the plant Saturday default with plant windows. If that
  ever stops being true, we'd revisit precedence — explicitly out of scope now.
- **Dale publishes Saturday schedules** before the crew clocks in. The
  published-gate means an unpublished Saturday's hours don't drive dashboards
  or rounding (the scheduler still *shows* the default while editing).

## Acceptance criteria

- **Settings:** the Saturday Default panel shows 6:00–12:00 + the two breaks;
  saving validates `start < end` and breaks within the shift, persists, and a
  reload reflects the change.
- **Scheduler, no edits:** opening a Saturday shows
  `Hours · 6:00–12:00 · 2 breaks · Saturday default`; the Hours editor
  pre-fills 6a–12p + the two breaks. A weekday is unchanged.
- **Published Saturday, no edits:** treated as a workday (`is_workday` true);
  productive minutes = 6h − breaks; progress/dashboards populate against
  6a–12p.
- **Punch on a published Saturday:** a `clock_in` within the plant
  `in_before` window of 6:00 records 6:00; a `clock_out` within `out_after`
  of 12:00 records 12:00; punches outside the windows pass through;
  `transfer_*` never rounded.
- **Customized Saturday:** setting the Hours pill to e.g. 6a–2p overrides the
  default; once published, punches round to 6:00/14:00 and dashboards use
  6a–2p. ("However the hours are set in the scheduler, rounding follows.")
- **Editing the Saturday default in Settings** changes future Saturdays;
  Saturdays already carrying a per-day override keep their override. Past
  published Saturdays *without* an override recompute their dashboards against
  the new default on next load (on-demand stats model); already-persisted
  punch `rounded_at` values are unaffected.
- **Unscheduled/unpublished Saturday** is inert for dashboards and rounding,
  while the scheduler still shows the 6a–12p default during editing.
- **Weekdays, the published-gate, and per-schedule (Odoo) rounding** are
  unchanged.

## Risks

- **Hot-path cost.** The resolver runs in tight loops. `saturday_schedule_store.current()`
  must be an in-process cached singleton like `schedule_store`; the added work
  per call is one weekday compare plus, on Saturdays, one cached read.
- **Gate leakage.** Dashboards/punch must stay `published_only=True`; only the
  scheduler uses the configured view. A mix-up would leak draft hours into live
  metrics — covered by explicit gated-vs-configured tests.
- **Break empty-list semantics.** The Saturday default applies only when there
  is **no** per-day `breaks` override; an empty per-day `breaks` list still
  means "no breaks today." Tested.
- **Scheduler display change touches weekday custom-hours days** (fixes the
  draft-editor quirk). Verify the common no-override weekday path is unchanged.
- **Override precedence** (see Assumptions) — documented, not coded around.
- **Live layer, not a copy.** Changing the Saturday default retroactively
  changes how past no-override Saturdays render on dashboards (on-demand
  recompute). Accepted: it matches the existing custom-hours stats model, and
  persisted punch rounding (`rounded_at`) never changes. Materializing the
  default onto each day was considered and rejected (Non-goals).

## File touch list

- Modify `src/zira_dashboard/db.py` — `CREATE TABLE saturday_schedule`.
- New `src/zira_dashboard/saturday_schedule_store.py` — `SaturdaySchedule`,
  `DEFAULT`, cached `current`/`save`/`reload` (reuse `schedule_store.Break`).
- Modify `src/zira_dashboard/shift_config.py` — shared `_resolve_*` with a
  `published_only` flag + Saturday-default fallback; keep gated `*_for(day)`
  names; add ungated `configured_*_for(day)`. Import `saturday_schedule_store`.
- Modify `src/zira_dashboard/routes/settings.py` — GET context
  `saturday_schedule`; `POST /settings/saturday_schedule`.
- Modify `src/zira_dashboard/templates/settings.html` — Saturday Default
  sub-section.
- Modify `src/zira_dashboard/routes/staffing.py` — eff hours from the
  configured resolver; `hours_source` flag in context.
- Modify `src/zira_dashboard/templates/staffing.html` — third pill state +
  banner; generalize `has_custom_hours` usages to `hours_source`.
- No change to `routes/timeclock.py` or `rounding.py`.
- New tests: `tests/test_saturday_schedule_store.py` (cache/save/reload/parse,
  mirroring `test_rounding_store.py` / a schedule_store test); shift_config
  resolution tests (Saturday default, gated vs. configured, per-day override
  precedence, weekday unchanged, break empty-list); settings GET/POST/
  validation; a Saturday punch-rounding test (mirroring
  `test_work_schedule_rounding.py`) asserting 6:00/12:00 snapping with plant
  windows; a staffing-context test for `hours_source`.

## Testing note

Per the local environment (Python 3.9; the suite targets a newer runtime and
can't run locally), tests run in CI / on Railway. Locally, verify with
`py_compile` + a small ast-exec smoke of the pure functions
(`saturday_schedule_store` parse/format, the `shift_config` resolver with a
fake schedule).
