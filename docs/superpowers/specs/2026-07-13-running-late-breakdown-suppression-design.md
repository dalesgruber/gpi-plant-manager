# Running-late alerts and breakdown-presence design

**Date:** 2026-07-13  
**Status:** Approved for implementation

## Problem

The Exception Inbox can show a scheduled-but-unpunched employee as an
operator on a machine-breakdown card. That creates a misleading machine
decision alongside the real Late / Absence issue. A person who is not punched
in is not at the work center, so they cannot be the basis for a machine
breakdown alert.

Managers also need to record that a late employee has given a specific expected
arrival time. A generic short snooze does not communicate that commitment or
provide the desired re-check point.

## User experience

1. A Late / Absence row includes a **Running Late** action in addition to the
   existing **Absent** and generic **Snooze** actions.
2. Selecting Running Late exposes a native time picker and a confirmation
   control. The selected local arrival time must be later than the current
   plant-local time.
3. After confirmation, the actionable late row becomes a muted follow-up row:
   `Running Late — expected by 8:45 AM` (using the chosen local time).
4. While the expected-arrival time is active, that employee has no
   machine-breakdown operator row. The running-late follow-up is the only
   inbox item for that missing employee.
5. If the person clocks in, the running-late follow-up disappears. If the
   selected time passes while they remain unpunched, it expires and the normal
   actionable Late / Absence row returns automatically.
6. Generic Snooze remains unchanged: it has its existing short-duration
   behavior and displays as `Snoozed` rather than `Running Late`.

## Presence rule for machine breakdowns

An operator counts as present only when they have an open current punch that
resolves to the work center. Scheduled assignment, stale attribution history,
or a closed/past punch must not make a person an operator for breakdown
detection or for operator decision rows.

- A work center with zero present operators must not open a new machine
  breakdown incident.
- Existing open incidents with zero present operators auto-resolve as
  `handled`; no breakdown header or operator card is shown.
- If a present operator remains at the work center, the incident and that
  person’s breakdown action remain visible even when another scheduled person
  is absent or running late.
- Once a worker later clocks in, a still-silent machine may create a new
  incident through the normal detector rather than reviving a stale one.

## Design

### Persistence

Add a `late_expected_arrivals` table keyed by `(day, emp_id)` with `name`,
`expected_at_utc`, and `created_at`. It is deliberately separate from
`late_snoozes` so that a stated expected-arrival time is explicit, auditable,
and distinguishable from a generic deferral.

The late-report data layer provides narrowly scoped CRUD/query helpers:

- save or replace an expected arrival;
- list active expected arrivals;
- clear expired rows as best-effort housekeeping;
- remove the row when the person has punched in, or omit it from the snapshot
  immediately when live attendance shows a punch.

### Endpoint and validation

Add `POST /api/late-report/running-late` with `{emp_id, name, expected_time}`.
The route parses `expected_time` as a plant-local `HH:MM` value for the
current plant day and stores its UTC equivalent. Missing, malformed, and
non-future values return a clear 400 response. A successful mutation busts the
existing staffing/late/inbox caches.

### Snapshot composition

`late_report_payload()` gathers both generic snoozes and expected arrivals.
Both temporarily suppress normal late/absence and late-reason actions, but
only expected arrivals emit a `running_late` payload section. The section
includes the employee identity, UTC timestamp, local display label, and
minutes remaining. The inbox maps it to a muted `Running Late` follow-up row
and counts it with other follow-ups, not as an urgent/actionable alert.

At timestamp expiry the active query no longer returns the expected arrival;
the ordinary late calculation therefore produces the normal actionable row
again if attendance is still `no_punch`.

### Breakdown presence and lifecycle

Replace the current breakdown-operator lookup’s assignment/history-based
inference with a current-presence helper that requires an open punch for the
work center. Use that helper consistently for station signals, new incident
attributions, current breakdown rows, auto-resolution, and manual reports.

Before refreshing an existing open incident, resolve it as `handled` when its
present-operator list is empty. `current_rows()` independently skips incidents
without present operators as a defensive display guard, so a stale incident
cannot surface during a partial failure.

## Frontend

The existing server-rendered row gets a hidden time input and confirmation
button beside **Running Late**. Clicking the action reveals and focuses the
time input. Confirmation posts the employee identity and selected time, shows
an inline validation or network error when needed, and otherwise resolves the
row so the next snapshot displays the muted follow-up. Existing buttons and
the polling/refresh safeguards remain unchanged.

## Tests

Tests must be written first and cover:

1. expected-arrival storage, active/expired behavior, and replacement;
2. endpoint validation for missing, malformed, and non-future time plus cache
   invalidation on success;
3. late snapshot/inbox mapping: a running-late row is muted, has the expected
   time, is the only late follow-up, and turns back into a normal late row on
   expiry while still unpunched;
4. template and JavaScript hooks for the time picker and endpoint;
5. a scheduled but unpunched person does not count as a present breakdown
   operator and has no breakdown operator row;
6. an open incident with its final present operator gone auto-resolves and is
   absent from the inbox; and
7. an incident remains for another truly punched-in operator at the same work
   center.

## Scope boundaries

This change does not alter the attendance provider, create timeclock punches,
change production-output thresholds, or alter the business rules for ordinary
generic snoozes and declared full-day absences.
