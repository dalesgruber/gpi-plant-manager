# New-Leaderboard Dashboard and TV

**Date:** 2026-07-10
**Status:** Design approved; awaiting written-spec review

## Goal

Add a New-department production leaderboard in both normal dashboard and TV
formats. It should look and behave like the existing Recycling-leaderboard,
while ranking operators from Zira-metered New machines in three independent
families:

1. Juniors
2. Woodpecker
3. Hand Build

Junior #2 is the only metered New machine today. The design must feel complete
with that one source and expand automatically as meters are added to other
Junior, Woodpecker, Hand Build, and Big Build work centers.

## Approved Product Decisions

- Operators compete only inside their production family. The app never ranks
  Junior output directly against Woodpecker or Hand Build output.
- The approved visual direction is **Layout 1: Adaptive lanes**.
- Families without qualifying history are hidden instead of showing empty
  placeholders.
- Hand Build includes every work center whose skill is `Hand Build`, including
  Big Build #1.
- The normal dashboard and TV share one data model and one template structure.
- Scoring, qualification language, recognition, and styling follow the
  Recycling-leaderboard conventions.

## User Surfaces

Add two routes:

```text
GET /new-leaderboard
GET /tv/new-leaderboard
```

The normal route appears in the Dashboards sub-navigation and uses the existing
top navigation. The TV route removes interactive navigation, uses the shared TV
header, honors saved light/dark theme selection, and loads `tv-refresh.js`.

Use the exact display name `New-Leaderboard` in the page title, dashboard picker,
TV header, and default TV display row.

## Visual Design

The screen inherits the Recycling-leaderboard's typography, green live-score
accent, gold recognition accent, panel treatment, table columns, density, and
light/dark TV behavior.

### Header

The header contains:

- Title: `New-Leaderboard`
- Context crumb: `NEW`
- YTD and L30 date ranges
- Current GOAT chips for active families when a winner exists

GOAT chips never reserve empty space. If a family has no winner, that chip is
omitted while the rest of the screen remains unchanged.

### Current State: Junior #2 Only

When Juniors is the only active family:

- The Juniors table occupies the main left area.
- The Juniors Gold Ribbons panel occupies the right rail.
- Woodpecker and Hand Build do not appear as empty or "coming soon" cards.
- The composition fills a 16:9 TV rather than leaving two-thirds of the screen
  blank.

### Future State: Three Active Families

When Juniors, Woodpecker, and Hand Build all have qualifying history:

- Three equal leaderboard columns fill the upper content region.
- A single full-width Gold Ribbons section sits below them.
- The ribbon grid has Month, Juniors, Woodpecker, and Hand Build columns.
- The header shows up to three GOAT chips.

If exactly two families are active, they use two equal columns above the
full-width ribbon section. Layout is based on the active-family count, not on
hard-coded rollout dates.

### Leaderboard Table

Each family table contains:

| Column | Content |
|---|---|
| `#` | Rank within the family |
| `Name` | Operator name |
| `YTD Avg` | Normalized full-day average with qualifying-day count below |
| `L30 Avg` | Normalized full-day average with qualifying-day count below |

Each panel also shows its independent YTD and L30 minimum-day thresholds.
Unqualified cells display `not enough days`, matching Recycling-leaderboard.

Long names use safe single-line truncation on TV. The normal dashboard may use
the same compact table but must preserve the full name through accessible text.

### Empty and Unavailable States

If no family has a qualifying day, render the normal header and a centered
message:

```text
Waiting for qualifying Zira production.
```

If the historical data read fails, render the normal header and:

```text
Production data is temporarily unavailable.
```

The TV keeps its automatic refresh active in both states.

## Family Membership

Family membership is derived from `staffing.LOCATIONS`, not from mutable
leaderboard group settings:

| Family | `Location.skill` | Included work centers today |
|---|---|---|
| Juniors | `Junior` | Junior #1, Junior #2, Junior #3 |
| Woodpecker | `Woodpecker` | Woodpecker #1 |
| Hand Build | `Hand Build` | Hand Build #1, Hand Build #2, Big Build #1 |

Only work centers with attributed production rows affect the result. A future
meter requires the normal meter configuration for that work center, but no
New-Leaderboard-specific machine list or template change.

## Metric and Eligibility Rules

Reuse the normalized full-day metric implemented for Recycling-leaderboard.

For each person, family, and date:

1. Sum units and credited hours across every work center in that family.
2. Disregard the person-family-day if credited family hours are under 4.0.
3. Otherwise normalize production to the configured standard full day:

   ```text
   normalized_units = units / credited_hours * standard_full_day_hours
   ```

4. Count the result as one qualifying day.

The YTD or L30 average is the mean normalized amount across qualifying days in
that span.

YTD and L30 eligibility are calculated independently inside each family:

```text
minimum_days = ceil(family_leader_qualifying_days * 0.10)
```

Rows contain the union of people who qualify in either span. Sort exactly like
Recycling-leaderboard:

1. Eligible YTD average descending
2. YTD qualifying days descending
3. Name ascending
4. L30-only qualifiers after YTD qualifiers, ordered by L30 average, days, and
   name

Because the threshold is family-local, the first operator with one qualifying
day can appear immediately after a new meter launches.

## Recognition

### Gold Ribbons

Show the most recent 12 calendar months, newest first. For each active family
and month, the winner is the best qualifying normalized person-family-day using
the same four-hour cutoff and standard-day normalization as the leaderboard.

Each ribbon cell contains:

- Operator name
- Date
- Rounded normalized amount

A month without a winner displays a dash.

### Current GOATs

GOAT chips preserve the existing awards system's all-time best-day ranking
semantics rather than introducing a second trophy definition. Add or reuse an
awards helper that accepts an explicit set of work-center names, so family
membership remains skill-derived and does not depend on a configured group.

Canonical family labels for override matching are:

- `Juniors`
- `Woodpecker`
- `Hand Build`

Existing matching `award_goat` overrides are honored. This feature does not add
new trophy-management UI.

## Data Flow

```text
Zira meter samples
  -> existing daily work-center totals and staffing attribution
  -> production_daily rows per person/day/work center
  -> family work-center sets derived from staffing.LOCATIONS
  -> shared normalized production helpers
  -> active families, YTD/L30 rows, thresholds, ribbons, and GOAT chips
  -> normal or TV rendering
```

`production_daily` is the source of truth for historical leaderboard data. The
page does not call the Zira API directly during a request.

Production without a valid operator attribution is excluded rather than
credited to the wrong person. The existing missing-attribution and exception
workflows remain responsible for correcting those rows.

## Components and Boundaries

### `production_metrics.py`

Keep `normalized_daily_scores()` and `normalized_average_by_person()` as the
low-level shared primitives. Add a family-oriented builder with explicit
inputs, for example:

```python
build_family_leaderboard(
    records: list[dict],
    *,
    today: date,
    standard_full_day_hours: float,
    family_wc_names: dict[str, set[str]],
) -> dict
```

The returned payload contains:

- `ytd_start`, `ytd_end`, `l30_start`, `l30_end`
- ordered `active_families`
- per-family `rows` and `thresholds`
- 12 monthly ribbon rows keyed by family

The helper is pure and has no database, route, template, or Zira dependencies.
Do not duplicate the normalization or eligibility algorithms in the route.

### `awards.py`

Expose an all-time GOAT helper that accepts explicit work-center names and
preserves the current awards ranking and override behavior. The New-Leaderboard
route provides the canonical family label for override matching.

### `routes/new_leaderboard.py`

The route module:

1. Builds the three work-center sets from `staffing.LOCATIONS` skills.
2. Loads the covering historical range once.
3. Calls the pure family builder.
4. Removes families without current qualifying rows.
5. Resolves GOAT chips for active families.
6. Renders normal or TV mode through one shared function.

The covering range must include the current YTD, L30, and all 12 ribbon months.

### Template and CSS

Add sibling files rather than adding New-specific branches throughout the
Recycling-leaderboard template:

```text
src/zira_dashboard/templates/new_leaderboard_tv.html
src/zira_dashboard/static/new_leaderboard.css
```

Reuse shared macros such as `_tv_header.html`. Keep the New-specific adaptive
grid rules isolated in `new_leaderboard.css`. Reusing or extracting genuinely
shared leaderboard tokens is allowed, but do not perform unrelated dashboard
CSS refactoring.

## TV Registry and Navigation

Add TV kind:

```text
vs_new_leaderboard
```

Update every TV-kind allowlist and constraint consistently:

- `tv_displays_store.py`
- `routes/tv_displays.py`
- Settings TV picker context and validation
- `_schema.py` TV kind constraint/migration
- route/store/schema tests

Seed a default TV display:

```text
Name: New-Leaderboard
Kind: vs_new_leaderboard
```

Use a dedicated idempotent backfill marker so existing installations receive
the row without re-running or disturbing prior TV configuration.

Add `New-Leaderboard` to `_dashboards_subnav.html` with active key
`vs_new_leaderboard`.

## Error Handling and Performance

- Catch payload-read failures at the route boundary, log the exception, and
  render the unavailable state instead of a blank TV or raw error page.
- A GOAT lookup failure omits only that chip; it does not suppress leaderboard
  or ribbon data.
- Missing ribbon months display dashes.
- Preserve `tv-refresh.js` on empty and error screens so transient failures
  recover automatically.
- Use one covering historical read per request; do not query once per family or
  month.
- Follow existing response-cache and cache-header conventions for dashboards
  containing today's data.

## Testing

### Pure metric tests

Cover:

- Family mapping for Junior, Woodpecker, and Hand Build
- Big Build #1 included in Hand Build
- Multiple work centers combined before the four-hour cutoff
- Family-local YTD and L30 thresholds
- L30-only qualifiers
- Sorting and deterministic tie-breaks
- One-day initial threshold
- Twelve-month ribbons per family
- Independent output scales never compared across families
- Zero and partial-day records

### Awards tests

Cover:

- Explicit work-center-set GOAT ranking
- Canonical family override matching
- Missing winner and lookup failure behavior

### Route and template tests

Cover:

- `/new-leaderboard` normal rendering and active sub-navigation
- `/tv/new-leaderboard` dark and light theme behavior
- Junior-only adaptive layout
- Two-family and three-family layouts
- Hidden inactive families
- No-data and temporary-unavailable states
- GOAT chips omitted independently
- Long-name safety and fixed table columns
- `tv-refresh.js` present on every TV state

### TV registry tests

Cover:

- `vs_new_leaderboard` accepted by store, route dispatcher, settings, and DB
  constraint
- Default display backfilled idempotently without altering existing rows

Run the focused tests first, then the complete test suite. Render representative
Junior-only and three-family pages at desktop width and a 1920×1080 TV viewport
for visual verification in both themes.

## Out of Scope

- Combining the three families into one percent-of-goal ranking
- Adding Zira meters or changing meter identifiers
- Replacing or redesigning the Trophy Case
- New award-override management UI
- Changing Recycling-leaderboard scoring or appearance
- Changing live `/new` operational KPIs

## Acceptance Criteria

The feature is complete when:

1. Junior #2 production produces a useful, full-screen Juniors leaderboard and
   ribbon rail on both routes.
2. No empty future-family cards appear before qualifying history exists.
3. Adding qualifying Woodpecker or Hand Build history expands the layout without
   route or template changes.
4. Big Build #1 is included in Hand Build.
5. Family scoring matches the approved normalized full-day rules and never
   compares unlike machine families.
6. The normal dashboard and TV remain readable at their target sizes in light
   and dark themes.
7. Empty data, missing recognition, and temporary failures degrade visibly and
   recover through refresh.
8. TV registry, routing, navigation, and tests are complete.
