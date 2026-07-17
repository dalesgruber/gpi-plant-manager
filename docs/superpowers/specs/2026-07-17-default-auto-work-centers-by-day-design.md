# Default Auto Work Centers by Day — Design

## Goal

Managers can configure the default Auto work-center On/Off set in Settings.
When the app creates a new staffing day, it copies that set into the day's
schedule. Thereafter the day's set is independent: changing a work center on
one day, or changing the Settings defaults, cannot alter another saved day.

## Current behavior

`rotation_auto_enabled_work_centers` is a single global `app_settings` value.
The Staffing page reads it for every date and the Auto work-center endpoint
writes it for every date. Consequently, a toggle made while scheduling one day
changes the appearance and Auto behavior of every other day.

## Chosen approach

Persist the enabled Auto work-center names on `staffing.Schedule`, with a
separate global app setting used solely as the template for a new schedule.

This is preferred over a global setting with date-specific exceptions because
each persisted schedule is self-contained and a Settings update cannot leak
into an existing day. It is also preferred over applying defaults only during
Auto runs because the new day's initial row states must be visible and stable
before the manager invokes Auto.

## Data model and migration

Add `auto_enabled_work_centers JSONB` to `schedules` and add the corresponding
`auto_enabled_work_centers: list[str]` field to `staffing.Schedule`.

The field stores an ordered, de-duplicated list of known work-center names.
The existing location order is canonical; invalid or removed names are ignored
on read and before save.

The existing `rotation_auto_enabled_work_centers` setting changes meaning: it
is the Settings default only. Rename the code constant to make that distinction
clear, while preserving the persisted key to avoid a separate settings-data
migration.

At rollout, schema bootstrap snapshots the current global value into every
existing schedule row whose field is null. If the setting is absent, the
existing recent-history initializer supplies the snapshot value before it is
saved. This preserves the visible behavior of legacy saved days and ensures
later Settings edits cannot rewrite them. New schedules always receive an
explicit list, including an intentionally empty list.

`snapshot_of` and posted-schedule hydration include the field so a posted view
continues to show the On/Off states that were saved with that version.

## Settings experience

Add a compact **Default Auto Work Centers** panel to the Work Centers Settings
section. It lists every configured work center in normal display order with an
On/Off checkbox or switch. Saving the panel writes only the Settings default;
it never creates, updates, or reconfigures a daily schedule.

The control is initialized from the global default. For installations that
have never explicitly set a default, it uses the existing recent-schedule
history initializer once, then persists the result as the default. This keeps
the current first-run behavior while making it explicit and editable.

## Daily behavior and API flow

When a Staffing page loads a day without a persisted schedule, its first
conditional schedule creation copies the Settings default into the new
schedule. That same schedule creation may also seed default people; both
operations use the one conditional insert so concurrent first views cannot
produce competing state.

For a saved schedule, the page reads `schedule.auto_enabled_work_centers` and
never reads the Settings default to choose its row states. A saved blank draft
is therefore authoritative.

`POST /api/rotations/auto-work-centers` validates and orders the submitted
names, copies a posted schedule to a draft if necessary, updates only that
day's `auto_enabled_work_centers`, and saves it in the same transaction as any
assignment removals and Saturday recruiting changes. The response returns the
persisted daily list. Rebuild, coverage, default-placement, Saturday
recruiting, and exception computations use the current schedule's daily list
rather than a global setting.

No endpoint changes the Settings default except the explicit Settings save.

## Error handling

If loading the Settings default while first creating a day fails, do not create
a partial schedule. Render the existing empty in-memory schedule and log the
failure. Existing daily schedules remain readable even if Settings is
temporarily unavailable because their enabled list is stored locally.

## Test coverage

Add focused tests for:

- Schedule round trips, bulk hydration, conditional creation, and posted
  snapshots preserving daily enabled work centers.
- First creation copying the Settings default, including an empty default.
- The Staffing context reading an existing day's list rather than the global
  default.
- Saving On/Off state for one date without changing another date or the
  Settings default.
- Settings rendering and saving the default control.
- Legacy-row migration snapshot semantics and validation that unknown names
  are removed safely.

## Non-goals

- Changing Settings does not retroactively change existing drafts, posted
  schedules, or saved blank days.
- The feature does not change Auto placement rules, default people, minimum
  coverage calculations, or the available work-center catalog.
