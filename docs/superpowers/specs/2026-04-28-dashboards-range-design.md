# Dashboard Date/Time Range — Design

**Date:** 2026-04-28
**Status:** Approved (brainstorming → implementation planning)

## Context

The dashboards (Recycling VS, New VS, Work Centers) are scoped to a single
day, defaulting to today, with a `?day=YYYY-MM-DD` query param for past
days. Dale needs to be able to look at:

- Standard ranges: this week, last week, last month
- Single past day (already works via `?day=`)
- Custom date range
- Sub-day windows (e.g., yesterday 14:00–15:11)

The conceptual model agreed during brainstorming:

> A **range** is `(start_local, end_local)`. Everything the dashboard shows
> is scoped to the **productive portion** of that range = the union of
> `(range ∩ that day's shift)` for every workday in the range, using each
> day's custom hours and breaks. "Today" is the default preset
> (`shift_start_for(today)` → `now`) and reproduces the existing behavior.

Goals + breaks + the first-60-min grace continue to apply, but per-day and
clipped to the range.

## Goals

1. Add a range picker (preset chips + custom popover) to the three dashboard
   tabs.
2. Make every dashboard metric and chart scoped to the chosen range, with
   the existing single-day code path remaining the default ("Today" preset).
3. For multi-day ranges, switch the 15-min progress chart to daily-aggregate
   bars and collapse per-row labels to WC name + operator count.
4. Preserve URL-shareability: a chosen range round-trips through query
   params.

## Non-goals

- Range picker on staffing/scheduler, settings, or production-history pages.
  Those keep their existing single-day pickers.
- New persistence (no remembered "last range" — page always opens to Today).
- New aggregation tables or pre-computed rollups. The existing
  per-day-fetch-then-aggregate pattern handles week/month volumes fine for
  this site's data scale.
- Changes to custom-hours UX (still set per-day on the scheduler).

## Design

### Range data model

Single in-memory representation, used by all dashboard handlers:

```python
@dataclass(frozen=True)
class DashboardRange:
    start_local: datetime  # SITE_TZ-aware
    end_local:   datetime  # SITE_TZ-aware, exclusive
    preset:      str | None  # "today", "yesterday", "this_week",
                             # "last_week", "last_month", "custom_day",
                             # "custom"
    label:       str  # human-readable, e.g. "This week" or "Apr 24, 14:00–15:11"

    @property
    def days(self) -> list[date]: ...   # workdays in [start, end]
    @property
    def is_single_day(self) -> bool: ...
    @property
    def is_today(self) -> bool: ...
```

A small builder module (`range_picker.py`) constructs a `DashboardRange`
from query params:

- `?preset=today` (or absent) → today's shift
- `?preset=yesterday` → yesterday's shift (full day)
- `?preset=this_week` → Mon 00:00 → now
- `?preset=last_week` → previous Mon 00:00 → previous Sun 23:59:59
- `?preset=last_month` → previous calendar month, full days
- `?preset=custom_day&day=YYYY-MM-DD` → that day's shift (replaces existing
  `?day=` without breaking shareable links)
- `?preset=custom&from=YYYY-MM-DDTHH:MM&to=YYYY-MM-DDTHH:MM` → arbitrary

When `preset` is missing but `day=` is present (legacy shared links), it's
treated as `custom_day` and works without the picker chip highlighting.

### Per-day productive intervals across a range

`leaderboard.py` already fetches one day at a time. For a range:

1. For each workday in `range.days`, call the existing per-day fetch (parallelized).
2. Combine the resulting `StationTotal` per station into a range-aggregated
   `StationRangeTotal`:
   - `units` = sum across days of per-day units, with each day's samples
     filtered to the range window (intersected with that day's shift +
     custom hours).
   - `samples` and `active_intervals` = unioned across days, each clipped
     to `range ∩ that-day's-shift`.
   - `downtime_minutes` = sum across days, each computed only against the
     clipped-to-range active intervals.
   - `last_reading_at` / `last_status` = from the latest day's last reading
     (only meaningful for "is this WC running now" on Today).

This requires a new helper in `leaderboard.py`:

```python
def fetch_station_range(
    client, station, range: DashboardRange,
    now_utc: datetime | None
) -> StationRangeTotal: ...

def leaderboard_range(
    client, stations, range: DashboardRange,
    now_utc: datetime | None
) -> list[StationRangeTotal]: ...
```

`fetch_station_day` stays as today and is reused per-day inside the
aggregator. The window-clipping is done by passing each day's range bounds
in as a new optional `(window_start_utc, window_end_utc)` pair on the
existing per-day function — every sample/interval gets intersected with
that window before being returned. For full-day "Today" with no time-of-day
window, the window is the full shift and the existing math is unchanged.

### Per-day grace handling

The first-60-min-of-shift grace currently lives in `routes/value_streams.py`
and adds `(shift_start_local, shift_start_local + 60min)` to scheduled WCs'
productive intervals. For a range:

- For each workday in the range, build the day's grace window
  `(shift_start_for(d), shift_start_for(d) + 60min)`.
- Clip it to that day's portion of the range
  `(max(grace_start, day_range_start), min(grace_end, day_range_end))`.
- If the clipped grace has positive duration, append to the productive
  intervals for any WC that was scheduled on that day.

For "yesterday 14:00–15:11" the clipped grace is empty (range start is past
shift_start + 60min). For "today 7:00–8:30" with custom 7:18 start, the
grace is (7:18, 8:18) clipped to (7:18, 8:18) — full grace.

Schedule lookups also become per-day: `staffing.load_schedule(d)` for each
workday `d` in the range, used to determine "active" WCs and headcount on
each day.

### "Active WC" definition for a range

Today: a WC is active iff scheduled today **or** produced > 5 units today.
For a range: a WC is active iff scheduled on any day in the range **or**
produced > 5 units summed across the range. Single-day ranges reduce to
the existing rule.

### Headcount denominator across a range

`pph_per_person` (from the polish bundle) needs a range-aware denominator.
Definition: sum across workdays in the range of (sum of scheduled headcount
on active WCs that day). One person on Repair-1 Mon and Repair-2 Tue counts
as 2 person-day-slots, matching the per-WC throughput intuition. For a
single-day range this reduces to the polish-bundle definition.

### Progress chart for multi-day ranges

Single-day ranges: existing 15-min buckets, axis labels every 30 min from
the polish bundle.

Multi-day ranges: switch to **one bucket per workday**:
- x-axis label = day (e.g., `Mon 4/22`)
- bar height = units produced on that day
- target line = day's expected (using that day's productive minutes ×
  per-WC hourly target sum, clipped to the range)
- "in-progress" treatment applies only to the day that contains "now" (if
  the range extends to today)

A new helper in `progress.py`:

```python
def progress_buckets_daily(
    group: Iterable[StationRangeTotal],
    range: DashboardRange,
    now_utc: datetime,
) -> list[dict]: ...
```

Routes pick the helper based on `range.is_single_day`.

### Per-row labels for multi-day ranges

Single-day: person name(s) on top + WC grayed below (polish bundle behavior).

Multi-day: WC name in bold (no second line), with `(N)` suffix where N =
distinct operator names that touched this WC across the workdays in the
range (case-insensitive, trimmed). Empty schedule = `(0)`.

The downtime widget uses the same logic.

### Bar widget axis ticks

The widget's axis-row currently shows `start · HH:MM` and `now · HH:MM`.
For a range:

- Single-day range, ending at "now": same as today (`start · 7:00`,
  `now · 8:18`).
- Single-day past range: `start · 7:00`, `end · HH:MM` (range end).
- Sub-day window same day: `start · 14:00`, `end · 15:11`.
- Multi-day range: hide both ticks (no single shift_start applies).

The `widget_target_pct` computation already represents "fraction of expected
elapsed" — for past/windowed ranges it pins to 100% (the entire range is
"elapsed"), so the `end` tick lands at 100% naturally.

### Picker UI

Top-right of each dashboard page (Recycling VS, New VS, Work Centers).
Compact preset chips with one highlighted, plus a "Custom ▾" button:

```
[Today] [Yesterday] [This Week] [Last Week] [Last Month] [Custom ▾]
```

`Custom ▾` opens a popover:

```
From: [date]  [time (optional)]
To:   [date]  [time (optional)]
[Cancel]                    [Apply]
```

- Time fields are visible whenever both dates match (sub-day window).
- Apply navigates to the same path with the new query params.
- Reset / clear behavior: clicking the highlighted chip is a no-op; clicking
  another chip switches.

The picker is a small partial template
(`_dashboard_range_picker.html`) included by all three dashboard pages.

### URL encoding

All three dashboard routes accept the same query params:

- `?preset=<name>` — drives chip selection
- `?from=YYYY-MM-DDTHH:MM` — custom range start (local time)
- `?to=YYYY-MM-DDTHH:MM` — custom range end (local time)
- `?day=YYYY-MM-DD` — legacy single-day, treated as `custom_day`

The page renders with whichever subset is consistent. Conflicts resolve in
favor of explicit `from`/`to` over `preset`.

### Default range

Always **Today** on first page load. No persistence of last selection. Going
to `/recycling` (or `/new-vs`, or `/`) without query params produces the
same view it does today, byte-for-byte. The chip row appears regardless;
"Today" is highlighted by default.

## Acceptance criteria

- The picker appears on all three dashboard tabs and not elsewhere.
- "Today" is highlighted by default and produces visually identical output
  to the current dashboards.
- "Yesterday" / "This Week" / "Last Week" / "Last Month" each populate
  metrics, bars, downtime, and progress chart correctly with per-day
  custom hours and breaks honored.
- Custom range with two dates works for any date span, including
  multi-month.
- Custom range with the same date but different times produces a sub-day
  window with the bar widget showing `start · HH:MM` / `end · HH:MM`.
- Multi-day ranges show daily-aggregate progress bars and `(N)` operator
  counts on the labels.
- The legacy `?day=YYYY-MM-DD` URL still works.
- Past-day ranges never show a "now" tick on bar widgets; multi-day ranges
  show neither start nor end tick.

## Risks / open questions

- **Performance for "last month":** ~22 workdays × ~10–15 stations =
  ~300 per-station Zira reads, parallelized. Should be fine, but a coarse
  loading indicator on the page might be wanted. Out of scope for this
  spec — implement if the wait is noticeable.
- **Schedule resolution for multi-day ranges:** the current
  `staffing.load_schedule(d)` is a file-per-day read. Multi-day ranges
  do N reads. Same scale comment as above.
- **Time-zone consistency:** all range bounds are SITE_TZ. Ensure no UTC
  drift sneaks in by always converting at the boundary.
- **`StationRangeTotal` dataclass duplicating `StationTotal`:** consider
  whether range becomes the canonical type and `StationTotal` becomes a
  special case. Out of scope for this spec — defer until the polish
  bundle ships and we can refactor with a clear before/after.

## File touch list

- New: `src/zira_dashboard/range_picker.py` (range parser + presets)
- New: `src/zira_dashboard/templates/_dashboard_range_picker.html` (UI)
- Modified: `src/zira_dashboard/leaderboard.py` (range aggregator,
  per-day window clipping)
- Modified: `src/zira_dashboard/progress.py` (`progress_buckets_daily`)
- Modified: `src/zira_dashboard/routes/value_streams.py` (both handlers
  switch to range-driven flow)
- Modified: `src/zira_dashboard/routes/dashboard.py` (Work Centers index
  route consumes range)
- Modified: `src/zira_dashboard/templates/recycling.html`,
  `templates/new_vs.html`, `templates/index.html` (include picker partial,
  branch on `is_single_day`)
- Tests: range parser presets, per-day grace clipping, multi-day
  aggregation, sub-day window math.
