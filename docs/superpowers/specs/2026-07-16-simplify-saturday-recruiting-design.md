# Simplify Saturday Recruiting in the Scheduler

## Goal

Make Saturday recruiting a direct Scheduler action instead of a separate
configuration panel. A manager turns on the work centers needed for Saturday,
then launches recruitment immediately from the Scheduler.

## Manager experience

On an unpublished Saturday:

1. The manager turns on the work centers they intend to run.
2. The Scheduler shows a blue action before Publish: `Recruit for X work
   centers`, where X is the number of enabled centers.
3. Clicking it immediately activates recruitment. There is no confirmation
   dialog and no separate list of positions to configure.
4. Activation snapshots every currently enabled work center with that center's
   configured minimum crew requirement, uses the persisted Saturday shift and
   deadline, and makes the Timeclock offer live.
5. The blue action is replaced by a compact status line in the normal Scheduler
   controls: `Saturday: X yes · X no · X deciding`.

The green Publish action remains separate. Managers assign accepted volunteers
from Unassigned and publish only after the crew is built.

## Employee and staffing behavior

The existing Timeclock offer, eligibility, partial-shift, cancellation,
Spanish-primary, and reminder behavior stays unchanged. Activating recruiting
causes the existing Timeclock banner and eligible employee offers to become
available immediately.

Accepted employees populate Saturday Unassigned. Standard Saturday staffing
rules continue to require committed volunteers and requested coverage before
publication.

## Response status

The compact Scheduler line shows the current counts for committed (`yes`),
declined (`no`), and unresolved (`deciding`) eligible responses. Hovering or
focusing a count exposes the corresponding current employee-name list. The
line does not replace the existing staffing grid or Unassigned roster.

## Data and safety

The action derives requests exclusively from the enabled work centers and
their configured minimum crew counts at the moment the manager clicks Recruit.
Those values are persisted as the recruiting snapshot; later center toggles do
not change a live request. Existing atomic commitment, deadline, qualification,
time-off, cancellation, and publish validation remain authoritative.

## Scope

Remove the large Saturday Work panel and its manual position/count controls
from the Scheduler. Keep the existing underlying recruiting lifecycle and
Timeclock mechanics, adapting only the manager activation path and Scheduler
status presentation.

## Acceptance criteria

- No separate Saturday recruiting panel is rendered.
- The Recruit action is blue, appears before Publish, and uses enabled-center
  count in its label.
- One click activates recruiting and posts the offer to Timeclock.
- Each enabled center is requested at its configured minimum crew count.
- The Scheduler shows Yes, No, and Deciding response counts with accessible
  hover/focus name lists.
- Accepted people continue to appear in Unassigned and normal Publish remains
  the final manager action.
