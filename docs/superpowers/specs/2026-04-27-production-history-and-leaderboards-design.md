# Production History, Player Cards & Leaderboards — Design

**Date:** 2026-04-27
**Status:** Approved (brainstorming → implementation planning)

## Context

The app today shows production data **per work center** (units, downtime, % of
target). Dale wants the same data **per person**:

1. Value Stream dashboards should label each WC widget with the operator(s)
   working it that day, instead of the WC's static name.
2. Each employee should have a "player card" — pick a name, see how they've
   performed across every WC they've worked at, filtered by date and WC.
3. Historical leaderboards — "best dismantler this week / month / quarter /
   year", "best repairer this week / month / quarter / year", and so on for
   every WC category.

All three rest on the same primitive: **per-day, per-person production
attribution**, derived by joining the published schedule (who worked where)
with the Zira leaderboard call (what each WC produced).

## Goals

1. Build a single attribution layer that, given a date, returns
   `{person → {wc → {units, downtime, hours, days_worked}}}`.
2. Use it to label VS dashboard widgets with operator names.
3. Add a Player Cards tab in the Staffing section with date + WC filtering.
4. Add a Leaderboards page with weekly / monthly / quarterly / yearly views,
   one section per WC category.
5. Reuse the existing schedule files (`schedules/YYYY-MM-DD.json`) as the
   source of truth for who-worked-where — no new persistence required.

## Non-Goals

- No retroactive editing of who worked where. The published schedule is the
  source of truth; if the wrong person was on the line that day, fix the
  schedule (Edit + Re-publish), not the leaderboard.
- No machine-learning ranking or skill regression. Metrics are direct: units,
  downtime, % of target.
- No real-time per-minute attribution. The smallest unit of attribution is
  one operator × one WC × one day.
- No data migration. We start counting from the first published day after
  this ships; older days that were never published don't have attribution
  data.
- No team / shift leaderboards. Just per-person.

## Decisions (locked during brainstorm)

| Decision | Choice | Reason |
|---|---|---|
| Multi-person WCs | **Equal split** of units + downtime among all assigned operators | Only model that fairly compares solo and pair WC performance |
| Best metric | **% of target** (default), with toggle to raw units | Cross-category comparison; raw is still useful within one category |
| Min days to qualify on a leaderboard | **3 days** in the time window | Filters out flukes; tunable later |
| Category bucket | **By the WC's category** (`Location.skill`), not the person's roster skill | Leaderboard reflects what someone *did*, not what they *can* do |

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  staffing.load_schedule(date) → who worked where              │
│  leaderboard(client, stations, date) → what each WC produced  │
└────────────────┬─────────────────────────────────────────────┘
                 │
                 ▼
       ┌──────────────────────────┐
       │  production_history.py    │  ← new module
       │                           │
       │  attribution_for(date)    │  ← {person → {wc → totals}}
       │  attribution_range(start, │
       │                    end)   │  ← summed over a range
       │  rank_by_category(cat,    │
       │             window)       │  ← sorted leaderboard
       └────┬──────────────────────┘
            │
   ┌────────┼─────────┬────────────────┐
   ▼        ▼         ▼                ▼
[VS dash]  [Player    [Leaderboard    [JSON
 widget    cards]      page]           endpoint
 labels]                               for future use]
```

A single new module (`production_history.py`) owns attribution. The three UI
features each call into it; they don't reach into schedules + Zira directly.

## Data model — attribution layer

### `production_history.attribution_for(d: date) -> dict`

Returns:
```python
{
    "Iban":   {"Trim Saw 1": {"units": 100, "downtime": 5, "hours": 8}},
    "Porfirio": {"Trim Saw 1": {"units": 100, "downtime": 5, "hours": 8}},
    "Christian": {"Repair 1": {"units": 80, "downtime": 12, "hours": 8}},
    ...
}
```

How it's built:

1. Load the schedule for `d` via `staffing.load_schedule(d)`. For Player
   Cards and Leaderboards, **require `sched.published == True`** — drafts
   don't count toward history. The VS dashboard rename relaxes this and
   accepts current/draft assignments too (see Feature 1).
2. For each WC in `sched.assignments`, get the assigned operator list
   (excluding the `__time_off` pseudo-location). Skip if list is empty.
3. Query Zira `leaderboard(client, [WC's station], d)` for that WC's units
   and downtime.
4. Divide units / `len(operators)`, downtime / `len(operators)`. Hours = the
   day's productive shift minutes / 60 (same for everyone — `shift_elapsed`
   is per-day, not per-person).
5. Add the slice to each operator's entry under that WC.

### `attribution_range(start: date, end: date) -> dict`

Same shape, but values are summed across the range. Plus an extra
`days_worked` count per (person, wc) pair so we can enforce the 3-day
threshold.

```python
{
    "Iban": {
        "Trim Saw 1": {"units": 1100, "downtime": 55, "hours": 88, "days_worked": 11},
        "Repair 4":   {"units": 220,  "downtime": 8,  "hours": 16, "days_worked": 2},
    },
    ...
}
```

Iterates the date range, calls `attribution_for(d)` per day, accumulates.

### Caching

First pass: compute on every page load. Each request reads the relevant
schedule files (~few KB each) and a small number of Zira API calls. For a
year of leaderboards (~250 days × 22 WCs) this is too slow to be live.

If responsiveness becomes an issue, add a **per-day attribution cache**
keyed by date: pickled to `cache/attribution/YYYY-MM-DD.json` on first
compute, served from disk thereafter. Days in the past never change once
published, so the cache is permanent. Today's day is always recomputed.

The cache is **not** in the spec for v1 — ship without it, add it if needed.

## Feature 1 — VS dashboard widget labels

Both `/recycling` and `/new-vs` show widgets like
`"Pallets by Work Center — Dismantlers"` with bars labeled `Dismantler 1`,
`Dismantler 2`, etc. We replace each WC label with the names of the
people scheduled there for the displayed `day`.

- Multiple operators → join with `" + "`. `Iban + Porfirio`.
- No operators (unscheduled day or empty list) → fall back to the WC's
  static name as today.
- Day query param drives the lookup, same as the existing dashboard data.

Implementation: `app.py /recycling` and `/new-vs` already build a `bars` list
with a `name` field per entry. Add a `who` field that defaults to the WC name
but is overwritten with the operator list if the schedule for `d` has them.
The template renders `b.who` instead of `b.name`.

If no published schedule exists for the day (e.g., today's draft), use the
**current** assignments (the file as-is, not the snapshot) so live-day
visibility still works.

## Feature 2 — Player Cards

New tab in the existing staffing subnav: **People** (between People Matrix
and Past Schedules).

URL: `/staffing/people` lists all active operators with thumbnails. Click
one → `/staffing/people/<name>` shows their card.

### Card layout

```
┌───────────────────────────────────────────────────────────────┐
│  Iban                                  [date range picker]    │
│  Trim Saw 1 (lvl 3) · Forklift: Tablets (lvl 3) · ...         │
│                                                               │
│  Days worked: 47 (across 4 WCs)                              │
│  Total units: 4,850   Avg % of target: 108%   Downtime: 3.2h │
│                                                               │
│  ┌─── Per-WC breakdown ──────────────────────────────────┐   │
│  │ WC            Days  Units  Downtime  % of target       │   │
│  │ Trim Saw 1    32    3,200  1.8h      112%               │   │
│  │ Repair 4      9     820    0.9h      98%                │   │
│  │ ...                                                     │   │
│  └────────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─── Daily trend ─────────────────────────────────────────┐  │
│  │  bar chart of daily units, color-coded by WC            │  │
│  └─────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

- **Date range picker** defaults to last 30 days; presets for week / month /
  quarter / year-to-date / all time.
- **Per-WC breakdown** is sortable; clicking a WC row optionally filters
  the chart and totals to that WC alone.
- Skill chips at the top are the person's roster skills (lvl ≥ 1), rendered
  in the same color scale used elsewhere.

### Data flow

The route calls `attribution_range(start, end)` once and pulls the entry
for the requested person. Aggregates per-WC totals from there.

## Feature 3 — Leaderboards

New top-nav entry under Staffing: **Leaderboards** (or as a sub-tab —
finalize during plan).

### Page structure

One section per WC category, in `staffing.SKILLS` order: Dismantler, Repair,
Trim Saw, Woodpecker, Junior, Master Recycler, Hand Build, Chop/Notch, Forklift:
Load/Jockey, Forklift: Tablets, Mechanic.

Each section has a window selector (Week, Month, Quarter, Year — defaults
to Week) and a metric toggle (% of target / raw units), and renders:

```
Best Dismantler — This week
─────────────────────────────────
1.  Christian   118%  (480 units, 5 days)
2.  Adrian      109%  (445 units, 5 days)
3.  Jose O      102%  (412 units, 4 days)
4.  Eulogio      96%  (385 units, 5 days)
─────────────────────────────────
        4 qualified · min 3 days to rank
```

- Window dates are computed off "today" (current date in `SITE_TZ`):
  - Week: Monday–Sunday containing today
  - Month: 1st of current month → today (or end-of-month after rollover)
  - Quarter: start of current quarter → today
  - Year: Jan 1 → today
- Eligibility: at least 3 days worked at *any WC in this category* during
  the window.
- Default sort: % of target descending, ties broken by raw units.
- Rows show name, primary metric, secondary stat (raw units), days worked.

### Categorization

`Location.skill` already groups WCs into categories. Walk all `LOCATIONS`,
group by `loc.skill`, and the resulting buckets ARE the leaderboard
sections. WCs that share `Location.skill` (e.g., Repair 1/2/3/4/5) all
contribute to the same leaderboard.

A person's contribution to a category in a window:
- Sum their split-credit units across every WC in that category for every
  day in the window.
- Same for downtime.
- `% of target` = sum(units) / sum(expected) where expected is per-WC,
  per-day target hours × hourly target.
- `days_worked` = count of (date) entries where they were on any WC in this
  category. (Not WC-days — a day with two category WCs counts as 1 day.)

## Edge cases

- **Person scheduled but Zira returns no data for that day**: zero units,
  zero downtime, day still counts toward `days_worked` (they showed up).
  Distinguishes "didn't run anything" from "wasn't scheduled."
- **Person scheduled at a WC with no meter** (e.g., Hand Build, Repair 4):
  no Zira data exists. They get day-credit but zero units. Their `% of
  target` calculation excludes those days (no expected baseline either).
- **Schedule edited after publish** (Re-publish flow): the latest published
  state is what counts. Older snapshots aren't preserved per-day.
- **Time off entries** (`__time_off` pseudo-location): never attributed.
- **Reserves**: included if they hit the 3-day threshold. No special
  exclusion — if a manager spent 4 days on Repair 1, they're a contender.
- **Inactive people**: still appear on leaderboards for windows they were
  active in. Their Player Card stays accessible.
- **Empty windows**: section renders "No qualifying operators yet — needs
  at least 3 days at a [Category] WC this [window]."
- **Today, mid-shift**: today's data is partial. We compute it anyway; the
  page shows a "live, partial" badge when the window includes today.

## Module + file plan

New:
- `src/zira_dashboard/production_history.py` — the attribution functions.
- `src/zira_dashboard/templates/people_index.html` — list of operators.
- `src/zira_dashboard/templates/player_card.html` — one operator's card.
- `src/zira_dashboard/templates/leaderboards.html` — all category sections.

Modified:
- `src/zira_dashboard/app.py`
  - `/recycling` and `/new-vs`: enrich `bars` with operator names.
  - New `GET /staffing/people` and `GET /staffing/people/<name>`.
  - New `GET /staffing/leaderboards`.
- `src/zira_dashboard/templates/_staffing_subnav.html` — add People + Leaderboards links.

Tests:
- `tests/test_production_history.py` — attribution logic against synthetic
  schedules and stubbed leaderboard data. Covers split math, multi-day
  accumulation, day-count semantics, and edge cases (empty schedule,
  meter-less WC, time-off list ignored).

## Open future work (out of scope for v1)

- Filesystem cache of past-day attributions for snappier multi-day pages.
- "Trends over time" chart on the player card (rolling 7-day moving average).
- Compare-two-operators view.
- Export leaderboard to CSV.
- Anonymous mode (hide names on the dashboard for posting publicly without
  embarrassing folks).

## Risks

- **Zira leaderboard call cost**: each historical day requires one
  `leaderboard()` call per metered WC. For a year of leaderboards across all
  metered WCs, that's ~250 × ~7 = ~1,750 calls. May be slow without
  caching. Mitigation: ship without cache, watch real-world response
  times, add the file-based cache only if needed.
- **Wrong-attribution from stale schedules**: if someone manually moves a
  person between WCs mid-day and doesn't update the schedule, attribution
  is wrong. Mitigation is procedural — Dale already pushes for "publish
  the actual schedule"; this feature reinforces that incentive.
- **Small-sample noise in early weeks**: the 3-day threshold helps but
  someone with 3 great days will dominate until others catch up. Live with
  it for v1; we can raise the threshold per-window later if needed.
