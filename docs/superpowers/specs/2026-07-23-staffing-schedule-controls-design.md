# Staffing schedule controls design

## Goal

Make schedule-state controls clearer and keep Saturday recruiting sequential:
recruit first, then schedule and publish.

## Interface changes

- Remove the `Past` shortcut from the scheduler title bar. The existing date
  input remains the way to open a prior day.
- Keep the existing hours control and editor. On a day with custom hours, the
  control becomes a blue, clickable badge labelled `CUSTOM 7:00–12:00p` (with
  the meridiem shown on the ending time). Clicking it opens the current hours
  editor; clicking it again closes the editor.
- Non-custom weekday and Saturday-default hour badges retain their current
  wording and behavior.
- The Saturday Recruit button remains blue and uses the existing title action
  geometry. While a Saturday round is actively recruiting, hide Publish. It
  returns only after recruiting has closed and staffing has been prepared.
- On a custom-hours day, enabled work-center rows use a soft-blue highlight
  instead of the normal soft-green highlight. Normal and Saturday-default days
  retain their current colors.

## Data and behavior

No new persisted fields or lifecycle transitions are needed. The view derives
the custom-day state from `sched.custom_hours`; the existing Saturday
recruiting/prepared context controls Publish visibility.

## Validation

Add or update static/template tests for the removed Past link, custom badge
copy and toggle hook, recruit/publish state, and custom-day row class/style.
Run focused staffing and Saturday-recruiting tests.
