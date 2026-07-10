# New Department Recycling-Twin Dashboard — Design

**Date:** 2026-07-10
**Status:** Approved during visual brainstorming

## Goal

Replace the current static `/new` page with a desktop manager dashboard that
uses the same layout, controls, widgets, editing behavior, and visual language
as the Recycling dashboard. The first live source is Junior #2's Zira meter
(`42345`, named `Woodpecker Junior` in Zira). Future Zira-fed Hand Build and
Woodpecker work centers must join the same dashboard without another redesign.

The approved visual reference is the current Recycling layout supplied by the
user:

- two KPI cards stacked at the far left;
- a horizontal production-by-work-center widget in the center;
- a vertical Downtime Report at the right;
- a full-width 15-minute progress chart below;
- a full-width, bar-based Daily Progress chart below that.

## Scope

### In scope

- Bring `/new` onto the same GridStack widget canvas as `/recycling`.
- Add Recycling's named time ranges and custom date range to the top of `/new`.
- Use the same per-widget editing and automatic layout persistence.
- Support single-day and multi-day New-department data.
- Populate the dashboard from all New-department work centers with a configured
  Zira meter. Junior #2 is the only such work center today.
- Keep `/tv/new` as the read-only, auto-refreshing TV form of the same data and
  saved layout, matching Recycling's TV behavior.
- Preserve old `?day=YYYY-MM-DD` links by resolving them as a one-day custom
  range.

### Out of scope

- An Unplanned Stops widget or stop-reason inbox.
- Showing raw Zira Event, People, Product, Length, Width, Shift, or camera
  configuration fields.
- Non-Zira production sources for Hand Builds or Woodpecker. If those work
  centers later use manual entry or another system, that source gets a separate
  design; the dashboard-facing station-total contract remains reusable.
- Changes to Recycling's calculations or saved layout.
- Storing or displaying Zira's full data-source schema.

## User experience

### Range toolbar

The range toolbar sits immediately above the widget canvas, in the same place
and style as Recycling. It contains:

- Today
- Yesterday
- This Week
- Last Week
- This Month
- Last Month
- Custom

Custom opens the same start/end date popover and uses inclusive dates. The
active range is visibly selected. The right end of the toolbar contains
`Drag / resize — layout auto-saves` and `Reset Layout` exactly as Recycling
does. TV mode omits this interactive chrome.

### Default desktop layout

The page uses a 12-column GridStack canvas with the same 60-pixel editor row
height and eight-pixel margin as Recycling.

| Widget ID | Default position | Purpose |
|---|---:|---|
| `kpi-pallets` | `x0 y0 w2 h3` | Total pallets processed |
| `kpi-palletshr` | `x0 y3 w2 h3` | Same pallets/hour/person calculation as Recycling; title remains editable |
| `new-bars` | `x2 y0 w5 h6` | Horizontal bars for each active metered New work center |
| `downtime-report` | `x7 y0 w5 h6` | Vertical uptime/down columns for those work centers |
| `new-progress` | `x0 y6 w12 h5` | All New — 15-minute progress |
| `new-cumulative` | `x0 y11 w12 h5` | All New — Daily Progress, using cumulative bars plus target line |

Junior #2 is the only row/column at launch. When a Hand Build or Woodpecker
location receives a Zira `meter_id` and remains assigned to the New department,
it automatically appears as another production bar and downtime column. The
two progress widgets remain department totals rather than multiplying into a
new chart per work center.

### Widget behavior

All widgets use the shared Recycling controls:

- drag and resize on desktop;
- auto-save on change, drag stop, and resize stop;
- a visible Saved/Save failed indicator;
- Reset Layout to clear only New's saved positions;
- a per-widget overflow menu;
- editable title and applicable color/chart/bar settings;
- horizontal/vertical orientation, number placement, and sorting where the
  existing widget kind supports them;
- target-line and legend toggles on progress widgets.

The New dashboard uses its own persistence namespace, `new`, for both
`widget_layouts` and `widget_customizations`. New edits must never alter the
Recycling or Operator dashboards.

## Data and calculations

### Source discovery

For every requested day, include locations where:

1. the effective work-center department is `New`; and
2. `Location.meter_id` is configured.

At launch this resolves to Junior #2 / Zira meter `42345`.

### Zira fields used

The runtime continues to use the readings endpoint and only consumes the
fields already used by the production pipeline:

- `units`
- `event_date`
- `status`
- `duration`

The live schema inspection performed during design also exposed Event,
Scheduled Event, State Changed, Product, People, Shift, Length, and Width.
Those fields are deliberately not added to this dashboard. In particular,
the camera's People values are detection counts rather than operator
headcount, and Length/Width were unpopulated in the sampled readings.

### Daily data shape

Create a New-department daily-data function with the same aggregate-ready
shape used by Recycling:

- total units, downtime, elapsed and available minutes;
- uptime minutes;
- total effective man-hours and scheduled people;
- units, downtime, expected output, operator label, state, category, and
  station object per work center;
- active work-center names;
- 15-minute progress buckets;
- present-only schedule assignments and shift-start label.

Reuse the existing leaderboard cache, attribution/timeclock segment rules,
break-aware productive-minute calculations, target settings, range
aggregation helpers, progress color rules, and downtime calculation. Do not
create a second interpretation of Zira's backward-looking stop durations.

### KPI and chart rules

- **Total Pallets Processed:** sum of active New work-center Zira units.
- **Pallets/hr/person:** total units divided by effective scheduled man-hours,
  matching Recycling. The widget title is editable, so a saved layout may call
  it `Pallets/hr` as in the supplied screenshot.
- **New Work Centers:** actual units against prorated expected units, with the
  same target marker and red/green progress coloring as Recycling.
- **15-minute progress:** actual units per 15-minute bucket against the
  break-aware department target for that bucket.
- **Daily Progress:** cumulative bar for every elapsed bucket with the same
  target line and current-bucket treatment as Recycling. It is not an area or
  line-only chart.
- **Downtime Report:** the same shift-scoped vertical working/down columns and
  aggregate up/down label as Recycling. No separate unplanned-stop panel.

If a work center has no configured goal, actual production still renders, but
expected totals and target lines are omitted rather than shown as zero-percent
performance.

## Presentation architecture

The New page imports the same GridStack vendor assets, `recycling.css`,
`dashboard-grid.js`, `_widget_edit_controls.html`, and cumulative-progress
renderer used by Recycling.

Shared bar, progress, cumulative-progress, and downtime markup should live in
a focused department-dashboard partial or macros consumed by both pages. This
keeps the approved visual behavior identical without coupling New's saved
widget IDs or layout namespace to Recycling. The route remains responsible for
preparing New-specific data; the shared presentation layer only renders the
normalized widget inputs.

## Caching and error states

- Use the same per-station today/past Zira caches and HTML response-cache rules
  as Recycling.
- Range cache keys include start date, end date, TV mode, and theme.
- Today-inclusive responses retain short caching and auto-refresh behavior;
  historical ranges remain longer lived.
- A New department with no metered work centers shows a calm configuration
  empty state rather than zero-valued red performance cards.
- A metered work center with no readings renders as no activity/offline using
  the same semantics as Recycling.
- Network or layout-save failures surface through the existing Recycling
  save/error indicators; no new alert system is introduced.

## Security

The Zira API key remains in `.env` and is never sent to the browser. The live
data-source schema endpoint returns sensitive camera/device settings, so the
dashboard must not call it at runtime, log its raw payload, or persist it. Only
the allowlisted reading fields above enter dashboard calculations.

## Testing

Add focused tests for:

1. `/new` renders GridStack, `dashboard-grid.js`, widget edit controls, and
   `data-layout-page="new"`.
2. The default layout matches the approved stacked-KPI / bars / downtime /
   progress composition.
3. New layout and customization saves use the `new` namespace and cannot
   change `recycling` rows.
4. Every named range resolves to the same inclusive dates as Recycling;
   Custom and legacy `day` behavior are covered.
5. Junior #2 Zira data populates total pallets, New work-center bars,
   15-minute bars, cumulative Daily Progress bars, and downtime.
6. Daily Progress renders bars plus a target line, not an area chart.
7. No Unplanned Stops widget is present.
8. Scheduled operators and attributions label the correct meter-mapped work
   center (`Junior #2`, not Zira's `Woodpecker Junior` name).
9. A future New location with a meter automatically becomes a bar and downtime
   column and contributes to the two All New charts.
10. A zero-goal work center omits target/percent semantics cleanly.
11. Screen mode shows the range/editor toolbar; TV mode is static and omits it.
12. Existing Recycling route/template tests remain green, proving the shared
    presentation extraction did not change Recycling behavior.

## Acceptance criteria

- A manager can open `/new` and recognize the supplied Recycling layout
  immediately.
- The full range toolbar is at the top of the desktop dashboard.
- Every widget can be moved, resized, edited, reset, and auto-saved exactly as
  on Recycling.
- Junior #2's Zira production and downtime fill all approved widgets.
- Daily Progress uses cumulative bars and a target line.
- No Unplanned Stops widget is shown.
- Adding another New Zira meter requires no dashboard-layout redesign.
