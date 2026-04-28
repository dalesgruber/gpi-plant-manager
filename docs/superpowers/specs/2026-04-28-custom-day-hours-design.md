# Custom Day Hours — Design

**Date:** 2026-04-28
**Status:** Approved (brainstorming → implementation planning)

## Context

The shift schedule (start, end, breaks, work weekdays) is currently configured
globally in `schedule_store` and applied to every day. Dale needs per-day
overrides so the dashboards and leaderboards stop penalizing operators when
the day's productive window was shorter than usual:

- **Half day** — early shutdown, e.g., 7:00–11:00 instead of 7:00–15:00.
- **Late start** — power-out, weather, etc., e.g., shift starts at 9:00.
- **Company meeting / fire drill** — 30-minute block where everyone steps off
  the line.

Today these scenarios make every metric look bad: % of target craters because
the system still expects a full 8-hour shift, the per-bucket goal line stays
high through the dead time, and leaderboards rank operators below where they
should be.

The fix is one small data-model addition (a `custom_hours` field on the
per-day schedule) plus a thin UI panel on the scheduler. Everything
downstream — uptime, group goals, bar widget expected, progress chart bucket
targets, player-card per-WC stats, leaderboard % of target — already routes
through `shift_start()` / `shift_end()` / `breaks()`. Once those funnel
through a day-aware lookup that respects overrides, the entire cascade lights
up automatically.

## Goals

1. Let Dale set per-day overrides for shift start, shift end, and the break
   list, from the scheduler page.
2. Make every metric and chart that already respects the global schedule also
   respect per-day overrides — without per-call plumbing changes at each
   dashboard.
3. Surface that a day has custom hours visually, so it's never a silent
   override.
4. Keep the global schedule (Settings → Company Schedule) as the unmodified
   source of truth for non-overridden days.

## Non-Goals

- No retroactive recompute / cache. Stats compute on-demand from the JSON
  files, so editing a past day's hours updates dashboards on next load
  without any further work.
- No "weekly templates" or "recurring overrides." One day at a time.
- No "shift hours" CRUD permissions / audit trail. The user editing the
  scheduler is the one editing the hours.
- No partial-day attendance per person. Custom hours are global to the day —
  they're for company-wide events. Per-person time-off already lives in the
  Time Off list and is unaffected.

## Decision (locked during brainstorm)

| Decision | Choice | Reason |
|---|---|---|
| Override scope | **Bookends + break list** (option B from the brainstorm) | Half-day + late start + company meeting all expressible. Reuses the existing `Break` type so downstream math is unchanged. |
| Override semantics | **Total replacement** when set | A `custom_hours` value on a day is the *complete* schedule for that day. To "remove the lunch break for a half-day," empty out the break list. To "use defaults again," clear the override entirely. |

## Data model

Add one optional field to `staffing.Schedule` (the per-day record stored in
`schedules/YYYY-MM-DD.json`):

```python
@dataclass
class Schedule:
    day: date
    published: bool = False
    assignments: dict[str, list[str]] = field(default_factory=dict)
    notes: str = ""
    wc_notes: dict[str, str] = field(default_factory=dict)
    testing_day: bool = False
    published_snapshot: dict | None = None
    custom_hours: dict | None = None   # ← new
```

JSON shape when present:
```json
{
  "custom_hours": {
    "start": "09:00",
    "end":   "15:00",
    "breaks": [
      {"start": "12:00", "end": "12:30", "name": "Lunch"},
      {"start": "10:00", "end": "10:30", "name": "All-hands meeting"}
    ]
  }
}
```

`null` (or absent key) means "use the global schedule." This is the default
for every day going forward.

Validation on save:
- `start < end` (else reject with 400)
- Each break: `start < end`, `shift_start ≤ break_start`, `break_end ≤ shift_end`
- Duplicate / overlapping breaks allowed (Dale's call) — they're summed by
  the existing productive-minutes math and de-duped naturally because the
  break-subtraction algorithm uses interval-difference.

## Cascade architecture

The downstream code already routes everything through three accessors in
`shift_config`:

```python
def shift_start() -> time
def shift_end()   -> time
def breaks()      -> tuple[Break, ...]
def productive_minutes_per_day() -> int
def in_shift(local_dt: datetime) -> bool
def shift_elapsed_minutes(day: date, now: datetime) -> int
```

Add **day-aware twins** that consult the per-day override and fall back to
the global schedule:

```python
def shift_start_for(day: date) -> time
def shift_end_for(day: date)   -> time
def breaks_for(day: date)      -> tuple[Break, ...]
def productive_minutes_for(day: date) -> int
def in_shift_on(local_dt: datetime) -> bool   # derives day from local_dt.date()
def shift_elapsed_minutes(day, now) -> int    # already day-aware, just rewires internally
```

Implementation: each new function loads `staffing.load_schedule(day)`
(cheap — one JSON file), checks `sched.custom_hours`, returns the override
if present, falls back to the existing global helper otherwise.

To avoid a circular import (`shift_config` → `staffing` → `schedule_store`),
do the staffing import lazily inside each function (the codebase already
uses this pattern in `production_history` and elsewhere).

### Migration strategy: don't migrate

Existing call sites that use `shift_start()` / `shift_end()` / `breaks()`
without a day continue working — they keep reading the global schedule.
Dashboards and analytics already have a `day` in scope, so they switch to
the new `*_for(day)` variants. There's no big refactor — just a targeted
swap at each call site that already knows which day it's working with:

- `routes/value_streams.py::recycling()` — `shift_start_for(d)`, `shift_end_for(d)`, `breaks_for(d)`
- `routes/value_streams.py::new_vs()` — same
- `progress.py::progress_buckets()` — already takes `day`, swap to `shift_start_for(day)` / `shift_end_for(day)` / `breaks_for(day)`
- `leaderboard.py::fetch_station_day()` — already derives day from `end_iso`, swap to day-aware helpers when computing the active-interval cap
- `production_history.py::_elapsed_minutes_for(d)` — already takes day, swap to `shift_elapsed_minutes(d, now)` (which becomes day-aware internally)
- `staffing.in_shift(...)` filtering — change call sites to `in_shift_on(local_dt)` (derives day)

`shift_config.in_shift(local_dt)` (no day param) stays for back-compat —
falls back to the global schedule. Anywhere we don't have day context, the
global schedule is fine.

### Knock-on effects (verify they "just work")

- **Per-WC bar widget** `expected = hourly × productive_minutes / 60` —
  hourly is `daily_goal / productive_hours`, where `productive_hours` shrinks
  on a custom-hours day. Result: a station productive every minute of a
  half-day shift hits its (proportionally-smaller) settings goal.
- **First-60-min grace** in the route — `shift_start_local + 60 min` →
  uses custom start, so a 9:00 late start has its grace at 9:00–10:00.
- **Active-interval tail cap** in `leaderboard.py` — caps at custom shift
  end, so a 11:00 shutdown doesn't generate spurious "still-active" tail
  past the actual end.
- **Progress chart bucket grid** — `progress_buckets()` walks from custom
  start to `min(now, custom_end)` in 15-min steps, skipping break buckets.
  Buckets that fall inside an inserted "All-hands meeting" break are
  skipped exactly like lunch.
- **Active-WC filter** ("scheduled OR > 5 units") — unchanged.
- **Player Cards / Leaderboards** — pull through `attribution_range` which
  funnels through `shift_elapsed_minutes(d, now)` which is now day-aware.
  Per-person % of target rebases to the correct day length automatically.

## UI

### Location

A "Hours" pill / button in the scheduler title bar, next to the existing
Testing Day pill. Two visual states:

- **Standard hours** (default, no override): pill reads `Hours · 7:00–15:00`
  in muted gray, no extra emphasis.
- **Custom hours** (override set): pill reads
  `Hours · 9:00–15:00 · 4 breaks` in accent color with a colored border, so
  it stands out next to the date.

Clicking the pill opens an inline editor (popover, same style as the
existing Override popups in the scheduler).

### Editor

```
┌──────────────────────────────────────────────────────────────┐
│  Custom hours for Mon 2026-04-28                              │
│                                                                │
│  Shift  [09:00]  →  [15:00]                                   │
│                                                                │
│  Breaks                                                        │
│    [10:00]  [10:30]  [All-hands meeting]   [×]                │
│    [12:00]  [12:30]  [Lunch]                [×]                │
│    [+ Add break]                                              │
│                                                                │
│       [Reset to defaults]   [Cancel]   [Save]                  │
└──────────────────────────────────────────────────────────────┘
```

- Pre-fills with the day's current effective values: global defaults if
  there's no override, otherwise the override.
- "Reset to defaults" clears the override entirely (`custom_hours = None`)
  and closes the editor.
- "Save" validates and writes; on success, the pill updates and the page
  re-renders (or a top-center toast confirms, mirroring the rest of the app).

### Save flow

POST to `/staffing/hours?day=YYYY-MM-DD` with the form body. Server:
1. Validates start < end, break orderings.
2. Loads the day's schedule.
3. Sets `sched.custom_hours = {...}` (or `None` for reset).
4. Persists via `staffing.save_schedule(sched)`.
5. Redirects back to `/staffing?day=...` (or returns 200 JSON for autosave
   if we wire it that way).

### Discoverability

Two visual cues so it's never silent:
- The pill itself, in the title bar.
- A subtle banner in the day-notes section: "Custom hours: 9:00–15:00,
  4 breaks." Only when the override is set.

## Edge cases

- **Editing a published day.** Same path as today — capture
  `published_snapshot` if not yet snapshotted, flip to draft, custom hours
  go in alongside other edits. Re-publish or Discard handle the rest.
- **Custom hours set on a day with no roster work** (weekend, holiday).
  Allowed; the schedule still won't drive any production data, but lets
  Dale "block out" weird days for record-keeping.
- **Future-day override.** Allowed. The day picker's date input has no
  upper bound for custom hours edits.
- **Past-day override.** Allowed. Recomputes immediately on the next
  dashboard load.
- **`custom_breaks == []`** (empty list, override active). Means "no breaks
  today." `productive_minutes_for(d)` returns the full custom shift span.
- **Custom shift overlaps a global break.** The override's break list is the
  complete list. Globals don't merge in.
- **Custom hours set during the shift while the dashboard is open.** Next
  page load picks them up. No live recompute (acceptable — no auto-refresh
  on the scheduler today).

## Module + file plan

### Modified

- `src/zira_dashboard/staffing.py`
  - `Schedule` dataclass: add `custom_hours: dict | None = None`.
  - `load_schedule()`: read the field from JSON.
  - `save_schedule()`: write it.
- `src/zira_dashboard/shift_config.py`
  - Add `shift_start_for(day)`, `shift_end_for(day)`, `breaks_for(day)`,
    `productive_minutes_for(day)`, `in_shift_on(local_dt)`.
  - Update `shift_elapsed_minutes(day, now)` to use the day-aware helpers
    internally.
- `src/zira_dashboard/leaderboard.py` — swap `shift_end()` for
  `shift_end_for(day_local)` in `fetch_station_day` (one call site).
- `src/zira_dashboard/progress.py` — swap `shift_start()` / `shift_end()` /
  `breaks()` to the `*_for(day)` variants.
- `src/zira_dashboard/production_history.py` — `_elapsed_minutes_for(d)`
  already calls `shift_elapsed_minutes(d, now)` which becomes day-aware.
  No change needed if shift_elapsed_minutes is rewired internally.
- `src/zira_dashboard/routes/value_streams.py` — swap to `*_for(d)` in both
  `/recycling` and `/new-vs` for the productive-interval / break-subtraction
  computations.
- `src/zira_dashboard/routes/staffing.py`
  - Pass effective shift start/end/breaks for `d` into the template context
    (so the pill can render the right values).
  - Pass a `custom_hours` flag for badge styling.
  - Add new POST handler at `/staffing/hours`.
- `src/zira_dashboard/templates/staffing.html`
  - Hours pill + editor popover next to the Testing Day pill in the title bar.
  - Optional "Custom hours: …" banner in day-notes section when override is
    active.

### New

- (None.) The feature fits inside existing modules; no new files needed.

## Confirm-before-save guard for past-day edits

Editing the custom hours of a **past day** retroactively reshuffles every
operator's % of target on the leaderboards and player cards for the window
that contained that day. To prevent fat-finger rewrites:

- **Today or future days:** Save proceeds without a confirm.
- **Past days** (`day < today` in `SITE_TZ`): on Save, show a confirm popup
  that names the consequence ("Editing past-day hours updates leaderboards
  and player cards for any window that includes 2026-04-22. Continue?").
  The popup uses the same style as the existing Override / Overstaffed
  popups in the scheduler.

The confirm gates the POST. If the user cancels, the editor stays open with
its current values. The "Reset to defaults" button uses the same confirm
when on a past day.

## Open questions deferred to implementation

- Should the editor autosave on field change like the rest of the
  scheduler, or stay click-Save? Leaning **click-Save** because the
  validation rules (start < end, break orderings) are easier to enforce
  on a single submit than on every keystroke. The confirm-on-past-day
  guard is also cleaner with click-Save. Implementation can revisit.

## Risks

- **Forgetting a call site** when swapping to `*_for(day)` helpers — leaves
  one widget reading the global schedule on a custom-hours day. Mitigation:
  grep for every call to `shift_start()` / `shift_end()` / `breaks()` /
  `productive_minutes_per_day()` / `in_shift(` and audit each.
- **Cycle from `shift_config` → `staffing` → `schedule_store` → … —**
  mitigated by lazy imports inside each `*_for` function (already a
  codebase pattern).
- **Validation gaps** letting invalid hours through — small risk; the
  POST handler validates and there's no other code path that writes
  `custom_hours`.
