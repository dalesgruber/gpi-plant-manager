# Auto Capacity Warning and Clear Schedule Design

## Goal

Help the scheduler use the available workforce by warning when an automatic
schedule cannot place everyone because too few work centers have Auto enabled,
and let a planner intentionally empty a day's schedule.

## Scope

This change applies to weekday automatic scheduling in the Daily Plant
Scheduler. It preserves the existing manual center assignments, Auto toggles,
time-off data, notes, and published-schedule protections.

## Automatic-schedule capacity warning

After the planner runs an automatic rebuild from a schedule-goal button or
**Reset auto assignments**, the server will compare the available people with
the remaining maximum capacity of the enabled Auto work centers. If people
remain unscheduled solely because those centers do not have enough capacity,
the rebuild response will include a warning in the existing Schedule Goal
warning area:

> Turn on N more Auto work center(s) to schedule all available people.

`N` is the smallest number of currently disabled eligible work centers whose
available slots can accommodate the remaining people. The calculation uses the
existing work-center order to keep the result deterministic. It accounts for
people already assigned to a center and respects each center's maximum
operators; it never turns a center on automatically or changes assignments
outside the normal rebuild result. If all eligible centers are already enabled
or their combined capacity is still insufficient, the warning instead explains
that there is not enough Auto work-center capacity to schedule everyone.

This is advisory only: existing warnings about insufficient staffing, training,
and manual/default locks remain visible. The warning is returned both for a
button-triggered rebuild and on the initial page render, so it remains visible
after a refresh.

## Clear schedule action

The existing **Reset to defaults** control retains its current behavior. A new
clearly labeled **Clear schedule** button sits beside it. On a draft schedule,
pressing it asks for confirmation and then deselects every scheduled person in
the browser. The normal autosave path persists the now-empty assignments.

Clear schedule does not alter work-center Auto or Training settings, work-center
notes, day-level notes, time-off records, shift hours, schedule goal, or the
published snapshot. It updates the unscheduled list and picker summaries the
same way as existing assignment edits. Posted and posted-view schedules follow
the current edit lock: the planner must enter Edit before clearing, and a
posted-view schedule cannot be changed.

## Components and data flow

| Component | Responsibility |
| --- | --- |
| `auto_schedule_capacity.py` | Calculate the additional enabled-center count required to fit the available workforce, plus the no-more-capacity condition. |
| `routes/staffing.py` | Supply work-center capacity and enabled/disabled center state to the schedule warning calculation on initial render. |
| `routes/rotations.py` | Add the authoritative capacity warning to rebuild responses. |
| `static/staffing.js` | Render the returned warning and implement confirmation, deselection, UI reconciliation, and autosave for Clear schedule. |
| `templates/staffing.html` / `static/staffing.css` | Present the distinct Clear schedule button alongside Reset to defaults. |

## Error handling and testing

Capacity advice is best-effort and never prevents a rebuild. Malformed or
missing capacity data results in no added advisory warning rather than an
assignment failure. Tests will cover the count of additional centers, the
already-exhausted-capacity message, warning inclusion in rebuild/page context,
and the browser clear action's confirmation, empty selection state, and
autosave trigger.
