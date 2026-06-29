# Monitor: exclude flexible/salaried people from calendar-conflict detection — Design

Date: 2026-06-29

## Context

The calendar-conflict monitor and its CLI (`calendar_conflicts.gather_rows`)
flag any active, non-reserve person whose Odoo `resource.calendar` would make
Odoo reject an absence. But the absence flow only ever applies to **hourly +
fixed-schedule** people (`late_report.report_eligible_emp_ids`): flexible and
salaried people are never declared absent, so a calendar "gap" can't break an
absence sync for them.

Product decision (2026-06-29): the flagged people are intentionally kept on
**flexible** Odoo schedules and should be fully outside the late/absence flow.
They are already excluded from the Late/Absence report by eligibility — the
only place still surfacing them is this monitor, which must stop.

## Change

In `gather_rows`, when the local roster is available, skip anyone who is not
absence-eligible — mirroring `report_eligible_emp_ids`:

- `reserve` (already skipped), OR
- `wage_type != "hourly"` (salaried/unknown), OR
- `is_flexible` (flexible Odoo "Schedule Type").

The roster-unavailable fallback (laptop `railway run`, no Postgres) is
unchanged: it still lists all active Odoo employees with the existing `NOTE`
(eligibility can't be determined without the roster).

## Effect

- The monitor and CLI flag only hourly, fixed-schedule people whose calendar
  misses a plant workday or has no calendar — exactly the people for whom a
  real absence-declaration would be rejected by Odoo.
- Flexible and salaried people are never flagged.
- The existing open Odoo task **#1925** auto-resolves on the next weekly run
  once those people are flexible (the conflict set empties → the monitor posts
  "resolved" + archives). It can also be archived by hand.

## Non-goals

- No change to `classify_conflict`. Its `flexible` / `no_calendar` verdicts
  still apply to *eligible* (hourly, fixed) people whose synced calendar is
  empty or absent — that remains a real conflict worth flagging.
- No new absence path for flexible people; they are intentionally out of the
  late/absence flow entirely.

## Testing

- `gather_rows` excludes a flexible person and a salaried (`monthly`) person,
  while still flagging an hourly fixed-schedule person missing a plant workday.
- Existing reserve/non-roster and roster-unavailable-fallback tests updated so
  their roster stubs carry `wage_type`/`is_flexible` (the new predicate reads
  them); fallback behavior (no eligibility filter without the roster) unchanged.
