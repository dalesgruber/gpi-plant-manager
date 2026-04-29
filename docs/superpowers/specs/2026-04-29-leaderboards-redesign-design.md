# Leaderboards Redesign — Design

**Date:** 2026-04-29
**Status:** Approved (brainstorming → implementation planning)

## Context

The current `/staffing/leaderboards` page groups by skill category (Repair,
Dismantler, …) and shows per-operator totals aggregated across the
selected date range. Dale wants a different model:

- Group by **individual work center**, not category.
- Each row is **one operator on one specific day** — no aggregation.
- Show the **top 5 best single-day performances** per WC.
- Same operator can occupy multiple slots in a WC's top 5.
- The old "days worked" column moves to a `(N)` suffix on the operator
  name; the column is replaced by **the date of that performance**.
- WCs with no units in the range collapse into an "Inactive" section
  at the bottom; user can also manually mark any WC inactive.
- WC ordering is drag-reorderable; persisted server-side.

## Goals

1. Replace per-category aggregation with per-WC top-5 single-day records.
2. Surface each row's date directly, drop the days column, move the days
   count to `(N)` after the name.
3. Group active WCs (with units in range) above; auto-empty WCs and
   manually-hidden WCs in a collapsible "Inactive" section below.
4. Drag handle on each WC section to reorder; saved server-side.
5. Manual ✕ button to mark a WC inactive (or un-hide from the inactive
   section); persisted server-side.
6. Keep the existing units / % toggle. Default to **% of target**.
7. Keep the existing `?window=week|month|year|...` quick-select; add a
   custom date range form.

## Non-goals

- Per-user / per-device leaderboard layouts. Order + inactive flags are
  global (matches the Views/Settings model).
- Live updates while watching the page. Page renders on load; reload to
  refresh.
- Pulling in the new dashboard range picker. Keep the simpler local
  controls (`window=` chips + custom from/to inputs).

## Design

### Schema

One new table:

```sql
CREATE TABLE IF NOT EXISTS leaderboard_wc_settings (
  wc_name      TEXT PRIMARY KEY,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  is_inactive  BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Bootstrapped via `db._SCHEMA_DDL` (idempotent). No migration needed —
empty table is the default state. WCs missing from this table fall back
to the natural `staffing.LOCATIONS` order (bay-organized) and
`is_inactive = FALSE`.

### Data layer

Add to `production_history.py`:

```python
def daily_records(
    start_d: date, end_d: date, client
) -> list[dict]:
    """Return one record per (day, person, wc) with non-zero attributed
    units. Each record:
        {"day": date, "person": str, "wc": str,
         "units": float, "downtime": float, "hours": float}
    Used by the leaderboards top-5 computation. Days_worked is implicit
    (one record per day-credit)."""
```

Implementation notes:
- Iterate each day in the range.
- For each day, fetch the per-WC attribution (existing internal helper).
- Emit one row per (day, person, wc) where units > 0.
- ~30 days × ~10 stations per day = a few hundred rows for a month
  range. Cheap.

`attribution_range()` stays unchanged — used by the per-person player
card.

### Ranking computation

In `routes/leaderboards.py`:

For each WC `wc`:
1. Filter `daily_records` to those with `record.wc == wc.name`.
2. For each record, compute the metric:
   - `units` mode: sort key = `record.units` (descending)
   - `pct` mode: sort key = `record.units / expected_for_day` where
     `expected_for_day = settings_store.station_target_per_day(loc)`.
     If expected is 0, push to bottom.
3. Tiebreaker: oldest date ranks higher (i.e., ascending date for ties).
4. Take top 5.
5. For each top-5 row, compute that operator's `days_worked` on this
   WC across the entire range (`sum(1 for r in records_for_wc if
   r.person == row.person)`). Inject as `name_count`.

If a WC has no records at all in the range → mark it `auto_inactive`.

### Sectioning

Compute in the route:
```python
active_settings = leaderboard_settings_store.snapshot()  # {wc_name: {sort_order, is_inactive}}
sections = []
for loc in staffing.LOCATIONS:
    s = active_settings.get(loc.name, {"sort_order": 0, "is_inactive": False})
    rows = top5_for(loc, mode=metric)
    auto_inactive = not rows
    sections.append({
        "loc": loc,
        "rows": rows,
        "is_inactive": s["is_inactive"] or auto_inactive,
        "is_manually_inactive": s["is_inactive"],
        "sort_order": s["sort_order"],
    })

active = [s for s in sections if not s["is_inactive"]]
inactive = [s for s in sections if s["is_inactive"]]
active.sort(key=lambda s: (s["sort_order"], staffing.LOCATIONS.index(s["loc"])))
inactive.sort(key=lambda s: (s["sort_order"], staffing.LOCATIONS.index(s["loc"])))
```

### URL params

Keep:
- `?metric=units` or `?metric=pct` (default: `pct`)
- `?window=week|month|quarter|year|today` (existing semantics)

Add:
- `?start=YYYY-MM-DD&end=YYYY-MM-DD` — when both present, override
  `window=` with this custom span. The window radio gets a "Custom" entry
  that's auto-selected when start/end are set.

### UI

```
┌── Toolbar ───────────────────────────────────────────────────────┐
│  Date range:  [Week] [Month] [Quarter] [Year] [Custom: __ to __] │
│  Metric:      ( ) Units   (●) % of target                        │
└──────────────────────────────────────────────────────────────────┘

┌── Repair 1 ──────────────────────────────────  [☰] [✕]  ─────┐
│  #1   Alice Smith (12)        Tue 4/29   320   297    108%   │
│  #2   Alice Smith (12)        Mon 4/28   315   297    106%   │
│  #3   Bob Jones (4)           Thu 4/24   305   297    103%   │
│  #4   Alice Smith (12)        Wed 4/23   300   297    101%   │
│  #5   Carol Lee (8)           Fri 4/26   295   297     99%   │
└──────────────────────────────────────────────────────────────┘

┌── Dismantler 1 ─────────────────────────────────  [☰] [✕]  ──┐
│  ... 5 rows                                                  │
└──────────────────────────────────────────────────────────────┘

... more active WC sections ...

▸ Inactive (8)  ← collapsed by default; click to expand
   When expanded:
     Each WC section in here also has [☰] (drag back to active)
     and [↶] (un-hide manually) controls.
```

Per-section bits:
- `[☰]` = drag handle. HTML5 native drag-and-drop. On drop, POST the new
  order to `/staffing/leaderboards/order` with the array of `wc_name`
  values in active-section order.
- `[✕]` = mark this WC inactive (manually). POST to
  `/staffing/leaderboards/wc/{name}/inactive`. WC moves to the inactive
  section, the page re-renders the top.
- In the inactive section, `[↶]` un-hides. POST to
  `/staffing/leaderboards/wc/{name}/active`. Auto-empty WCs (no units)
  flip back to active immediately on next render after they accrue data,
  unless user explicitly hid them.
- `(N)` after each name = total days that operator worked at this WC
  across the selected range (carried from the old "days" column).

### Endpoints

Existing GET `/staffing/leaderboards` — same path, new template + data
shape.

New:
- `POST /staffing/leaderboards/order` body: `{"order": [wc_name, ...]}` →
  upserts `sort_order` for each WC by index.
- `POST /staffing/leaderboards/wc/{name}/inactive` → upsert
  `is_inactive=TRUE` for that WC.
- `POST /staffing/leaderboards/wc/{name}/active` → upsert
  `is_inactive=FALSE`.

All return JSON `{ok: bool, ...}`.

### Date format

`Tue 4/29` (weekday + month/day, no year). Year is implicit from the
selected date range.

### Sort tiebreakers

Within a WC's top 5, when two days tie on the metric, **oldest date
ranks higher** (per Dale's call). Means earlier achievements get
priority for the leaderboard slot.

### Drag-and-drop edge cases

- Dragging a section from active → inactive (drop on inactive header):
  marks `is_inactive=TRUE`.
- Dragging from inactive → active: marks `is_inactive=FALSE`. Position
  in the active list is determined by drop target.
- Dragging within active: re-orders.
- Dragging within inactive: re-orders within inactive (less important
  but consistent).

After drop, immediately POST the new state. No optimistic UI rollback —
if the server rejects, alert + reload.

## Acceptance criteria

- `/staffing/leaderboards` renders with sections per active WC, top 5
  rows each, inactive section collapsed at the bottom.
- Default metric is `% of target`; toggle works.
- Each row shows `Operator (N)` where N = total days they worked at this
  WC in range; the row's date is `Tue 4/29` formatted; columns are rank,
  name, date, units, expected, %.
- Tied days within top 5 are ordered oldest-first.
- WCs with no records in the selected range auto-inactivate.
- Manually marking a WC inactive (✕) sticks across reloads and across
  date-range changes (until you ↶ it).
- Dragging a section reorders it; the new order persists across
  reloads, devices, and users.
- Custom date inputs override `?window=`.

## Risks

- **Performance.** `daily_records` iterates each day's attribution. For
  a year range that's ~250 workdays × ~10 stations = ~2500 records.
  Cheap. Unlikely to hit any limit.
- **Operators no longer in roster.** Past records reference names that
  may have changed (e.g., the recent name truncation pass). Show whatever
  the production_history attributes; don't try to resolve to current
  names. If the name doesn't match anyone today, show as-is.
- **Drag-and-drop polish.** Native HTML5 DnD has known quirks (no
  visual move-preview, requires precise drop target). Acceptable for
  internal ops use; can swap to a small library later if it feels off.

## File touch list

- New: `src/zira_dashboard/leaderboard_settings_store.py`
- Modified: `src/zira_dashboard/db.py` (append DDL)
- Modified: `src/zira_dashboard/production_history.py` (add `daily_records`)
- Modified: `src/zira_dashboard/routes/leaderboards.py` (full rewrite of
  ranking + new endpoints)
- Modified: `src/zira_dashboard/templates/leaderboards.html` (new
  layout, drag, inactive section, custom range form)
- New: `tests/test_leaderboard_settings_store.py`
- Modified: `tests/test_production_history.py` (extend if exists; or
  add tests for `daily_records`)
