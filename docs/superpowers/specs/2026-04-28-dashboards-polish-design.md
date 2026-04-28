# Dashboards Polish — Design

**Date:** 2026-04-28
**Status:** Approved (brainstorming → implementation planning)

## Context

Four small UI improvements to the value-stream dashboards, agreed during a
brainstorming session. Each is independent of the others; they're bundled
because they touch the same templates and ship together cleanly. A separate
spec (`2026-04-28-dashboards-range-design.md`) covers the larger
date/time-range selector feature and is intentionally not part of this bundle.

## Goals

1. Make bar widget rows show *who's on the WC* as the primary label, with the
   WC name as a small grayed subtitle. Apply the same treatment to the
   downtime widget.
2. Change the headline throughput from `pallets/hr` to `pallets/hr/person`
   so the number reflects per-operator productivity, not just total volume.
3. Increase the label density on the 15-min progress chart axis from every
   60 min to every 30 min.
4. Rename the "Value Streams" top-nav header to "Dashboards" and promote the
   "Work Centers" page from its own top-level link into a third subnav tab
   to the right of "New VS".

## Non-goals

- Date/time range selection — separate spec.
- Any change to bar widget math, color logic, or sort order.
- Any change to the staffing/scheduler page.
- Renaming the route paths (`/recycling`, `/new-vs`, `/`). Only the visible
  labels change.

## Design

### 1. Per-row label change (bars + downtime)

Today each bar row label is the operator name(s) when scheduled, otherwise
the WC name, on a single line.

After:

- **Person row:** name(s) on top in the existing weight, WC name on a second
  line in a muted color (`var(--muted)`), smaller font (~0.75em).
- **Multi-person:** keep `Alice + Bob` on one line on top; WC grayed below.
- **No assignment fallback:** WC name in bold on top, italic
  `(no assignment)` in muted color on the second line.

Applies identically to the downtime widget rows.

The current bar template puts `{{ b.who if b.who else b.name }}` in
`<div class="name">`. Change that single block in the `bar_chart` macro to
emit two stacked spans, with class names `name-primary` and `name-secondary`
plus a fallback variant when there's no operator. Same edit in the downtime
row block. CSS adds the muted secondary line.

### 2. `pallets/hr` → `pallets/hr/person`

The current headline (`pallets_per_hour` in `routes/value_streams.py:56`) is
`total_units / (elapsed / 60)`. Change to:

```python
people_count = sum(
    len(ops) for wc, ops in sched_for_labels.assignments.items()
    if wc != staffing.TIME_OFF_KEY and ops and wc in active_wc_names
)
pph_per_person = (
    total_units / (elapsed / 60.0) / people_count
    if elapsed > 0 and people_count > 0 else 0.0
)
```

Denominator = sum of scheduled headcount across **active** WCs (so split
shifts count Alice on Repair-1 and Repair-2 as 2 person-slots). If no one is
scheduled but units are produced, the metric reads 0 — that's fine, it
mirrors the bar widget's "(no assignment)" state.

Template label changes from `pallets/hr` to `pallets/hr/person`. Same change
needs to apply to both the recycling page and the New VS page that share the
same metric block.

### 3. Progress chart axis ticks: every 30 min

The 15-min progress chart currently labels every 4th bucket (every hour) on
the x-axis. Change to every 2nd bucket (every 30 min). This is a small
template-level change in whatever loop renders the axis tick — typically a
modulo check on the bucket index.

No backend change. Bucket data already exists at 15-min resolution.

### 4. Nav rename + Work Centers tab promotion

Current state (in `templates/_staffing_base.html`):

```html
<a href="/">Work Centers</a>
<a href="/recycling">Value Streams</a>
```

Subnav (in `templates/_value_streams_subnav.html`):

```html
<a href="/recycling">Recycling VS</a>
<a href="/new-vs">New VS</a>
```

Target state. Top nav drops "Work Centers" and renames "Value Streams":

```html
<a href="/recycling">Dashboards</a>
```

Subnav adds a third entry to the right:

```html
<a href="/recycling" class="...">Recycling VS</a>
<a href="/new-vs"    class="...">New VS</a>
<a href="/"          class="...">Work Centers</a>
```

The Work Centers page (`/`, rendered by `routes/dashboard.py::index`) needs
to start including `_value_streams_subnav.html` so the subnav appears on it
too. Pass `active_vs="work_centers"` in the template context, and add the
matching `class="active"` branch in the subnav partial.

## Acceptance criteria

- All bar widget rows on `/recycling`, `/new-vs`, and `/` show person on top
  + WC grayed below, with the multi-person and no-assignment fallbacks
  rendering as specified.
- The downtime widget on `/recycling` and `/new-vs` uses the same row format.
- The headline reads `pallets/hr/person` with the per-person calculation.
- The progress chart axis shows a label every 30 min instead of every hour.
- Top nav shows a single "Dashboards" entry; "Work Centers" is gone from
  the top nav.
- The subnav (visible on all three dashboard pages) has tabs in order:
  Recycling VS, New VS, Work Centers, with active styling working on each.

## Out of scope (deferred to range spec)

- Multi-day collapse of the person label (handled by the range spec since
  it's the only context where multi-person-across-days arises).
- Daily aggregation on the progress chart for multi-day ranges.
- The `(N)` operator-count suffix for multi-day labels.

## Risks

- **Subnav on Work Centers page:** the index page may not currently inherit
  from the same base template that owns the subnav. If it doesn't, a small
  template restructure is needed (extract the subnav include into the page
  itself, or move the index page under the same base).
- **`active_wc_names` denominator for `pph_per_person`:** an unscheduled WC
  that produced > 5 units becomes "active" but contributes 0 to the
  denominator. The headline will read higher than it would if we counted
  one implicit person per unscheduled-but-active WC. This matches the
  bar-widget grouping logic and was Dale's choice; flagged so it's explicit.

## File touch list

- `src/zira_dashboard/templates/recycling.html` (bar_chart macro, downtime
  rows, headline label)
- `src/zira_dashboard/templates/new_vs.html` (same surfaces)
- `src/zira_dashboard/templates/index.html` (subnav include)
- `src/zira_dashboard/templates/_staffing_base.html` (top nav: drop Work
  Centers, rename Value Streams)
- `src/zira_dashboard/templates/_value_streams_subnav.html` (add Work Centers
  tab, support `active_vs="work_centers"`)
- `src/zira_dashboard/routes/value_streams.py` (compute `pph_per_person` for
  both `recycling` and `new_vs` handlers)
- `src/zira_dashboard/routes/dashboard.py` (pass `active_vs="work_centers"`)
- CSS for `.name-primary` / `.name-secondary` (in the same templates' style
  blocks)
