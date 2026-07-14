# Unified training protocol design

## Goal

Replace Staffing's per-work-center **Training** checkboxes with one unified,
scheduler-owned training protocol. A manager sets up a protocol by choosing a
trainee, trainer, exact work center, start date, and number of attended
workdays. The scheduler places the trainee at that work center for every
training day and automatically pairs the named level-3 trainer there on day
one.

## User experience

- Remove every per-row **Training** checkbox from Staffing and remove the old
  picker behavior that exposed untrained people for a checked work center.
- Add a top-level **+ Training** button on Staffing. It opens one shared
  dialog with these required fields: trainee, trainer, work center, start
  date, and attended workdays.
- The dialog lists active training protocols and supports pause, resume, and
  end actions.
- Managers may manually assign the trainer with the trainee after day one. The
  protocol never locks the trainer after the first attended day.

## Protocol rules

- A protocol is for one specific configured work center, not an interchangeable
  rotation group.
- The trainee must be level 0 for the work center's required skill or skills.
- The trainer must be level 3 for every required skill at that work center.
- Each attended workday locks the trainee into the protocol's exact work
  center. The first attended workday also locks the trainer into the same work
  center. Later days lock only the trainee.
- A full-day trainee absence does not count toward the configured attended-day
  limit and naturally extends the protocol. Non-working days do not count.
- When the configured number of attended days is reached, the trainee is
  promoted to level 1 for every required skill at the selected work center and
  the protocol is marked complete.
- Paused and ended protocols do not affect scheduling; ending never promotes a
  skill.

## Scheduler integration and conflicts

- Evolve the current training-block persistence and lifecycle rather than
  creating a separate manual assignment system. Add the exact `work_center`
  value to a training block and remove its Recycled-only target restriction.
- Convert active protocols into explicit work-center effects before automatic
  scheduling. The scheduler must reserve the protocol's stated work center;
  it must not select another center within the same skill group.
- A conflicting manual assignment for the trainee prevents the protocol from
  moving that person and emits a visible warning. On day one, a conflicting
  manual trainer assignment similarly prevents the automatic pair and emits a
  warning.
- If the protocol's work center is unavailable to automatic scheduling or has
  no capacity, leave existing assignments untouched and return a clear warning.
- Existing active records without an exact work-center value retain their
  legacy group-based behavior until they complete, pause, or end. New records
  always carry a work center.

## Data and API

- Extend `rotation_training_blocks` and `TrainingBlock` with the nullable
  `work_center` field needed for backward-compatible migration.
- Update the training-block endpoint to accept `work_center` in place of the
  old group-only request. Resolve the selected center's required skill set,
  validate trainee/trainer levels, and persist the center.
- Return the selected work center in active protocol payloads so Staffing can
  render and manage protocols without a second source of truth.
- Keep the established pause, resume, end, attendance-recording, cache
  invalidation, and reconciliation lifecycle.

## Verification

Focused tests must prove:

1. protocol creation rejects unknown centers, unqualified trainees, and
   trainers below level 3;
2. day one reserves the trainee and trainer at the exact work center;
3. later training days reserve only the trainee at that exact center;
4. absences extend the protocol and completion promotes every required skill;
5. manual conflicts and disabled/full centers produce warnings without moving
   people;
6. Staffing renders the top-level setup entry point and no longer renders or
   uses the per-row Training checkbox behavior.
