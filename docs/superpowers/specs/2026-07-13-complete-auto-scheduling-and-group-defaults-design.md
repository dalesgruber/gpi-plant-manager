# Complete Auto Scheduling and Group Defaults Design

## Purpose

Change the Staffing page's schedule-goal buttons so a successful automatic
rebuild assigns every available employee, rather than stopping after work-center
minimums are covered. Restore defaults as hard planning anchors and extend them
from exact work centers to user-managed work-center groups. Group defaults must
rotate evenly across eligible member centers whenever the day's operating
constraints allow it.

This design applies when a planner clicks Optimized, Normal, or Training. It
does not automatically build a schedule when a blank day is opened.

## Product Decisions

- Auto may generate assignments only in work centers whose Auto checkbox is on.
- "Everyone" means every active, non-reserve employee who is not on full-day
  time off. Existing partial-day handling remains unchanged.
- Manual assignments remain protected. A protected person already assigned
  outside the enabled Auto set counts as placed and is not assigned again.
- A successful rebuild must assign every remaining available person exactly
  once and satisfy every enabled center's effective minimum.
- Work-center maximums, qualifications, full-day time off, one-person-one-place,
  training-block rules, and Trim Saw pairing remain hard safety constraints.
- If no complete safe schedule exists, Auto saves nothing. The current schedule
  remains unchanged and the planner receives person-specific blockers.
- Exact work-center defaults and group defaults are hard constraints, not soft
  preferences.
- A person may have only one saved default target: one exact work center or one
  user-managed work-center group.
- A group-default person rotates as evenly as possible among enabled, qualified
  member centers. Minimum coverage, capacity, qualifications, and pairing rules
  take precedence over perfect rotation.
- Reserves remain excluded from automatic scheduling unless a planner assigns
  them manually.

## Definitions

### Available person

An available person is active, is not marked reserve, and has no full-day
absence for the target day. Partial-day time off does not remove the person;
the existing UI and assignment-window behavior continue to show and schedule
around the partial absence.

### Exact default

An exact default binds a person to one named work center. That center must be
enabled, the person must be qualified, and the center must have capacity. If
not, the complete rebuild is infeasible and no result is saved.

### Group default

A group default binds a person to one user-managed group from Settings -> Work
Centers. The group's members are the work centers whose `group_name` is that
group. The solver chooses exactly one enabled member center for that person.
Only centers for which the person satisfies all required skills are candidates.

If the group has no enabled member, or the person is not qualified for any
enabled member, the complete rebuild is infeasible. If several member centers
are eligible, historical center counts and the most recent group-center
assignment make the least-used center cheapest. This produces an even cycle,
such as Repair 1 -> Repair 2 -> Repair 3, when other hard constraints are tied.

"Evenly as possible" means the solver may repeat a center when another member
is full, disabled, unsafe for the needed crew, or would make total placement
impossible. The rotation signal never causes someone to remain unscheduled.

## Scheduling Policy

The complete scheduler uses the following priority order:

1. Preserve manual assignments, exact defaults, group-default membership, and
   validated training-block commitments.
2. Enforce all hard availability, qualification, capacity, minimum staffing,
   one-person-one-place, and crew-safety constraints.
3. Require every available non-reserve person to be assigned exactly once.
4. Avoid `never` preferences when a complete solution exists without an
   override. Override `never` only when required for complete safe placement.
5. Rotate group-default people evenly among their eligible group centers.
6. Apply the selected Optimized, Normal, or Training ranking, then ordinary
   group/center rotation fairness and deterministic name/center tie-breakers.

These priorities are evaluated globally. The solver must consider swaps and
multi-person chains before deciding that a person cannot be placed. It may not
use a greedy "fill minimums, then add leftovers" pass that can strand a
cross-trained employee.

## Architecture

### Normalized immutable problem

The route layer builds one immutable problem containing:

- the target date and selected mode;
- enabled Auto work centers only;
- effective required skills and min/max staffing for every enabled center;
- the active roster, reserve flags, and skill levels;
- full-day absences;
- current manual assignments and validated training-block effects;
- exact work-center defaults and group defaults;
- scheduling preferences;
- bounded person/group and person/center history; and
- the current work-center-to-user-group membership map.

All database, settings, time-off, and history reads occur before solving. The
pure solver performs no I/O, and identical normalized inputs return identical
results.

### Protected commitments

Manual assignments are applied first and never moved or deleted. Available
people already protected anywhere in the schedule are removed from the
generated-person pool.

Exact defaults become fixed center commitments. Group defaults become
"exactly one of these eligible group centers" constraints rather than being
converted to a center before the global solve. This lets rotation fairness and
whole-schedule feasibility choose the group center together.

An absent or inactive saved default does not create a daily placement
requirement because that person is not available that day. An available
default with a disabled, full, or unqualified target is a blocking
configuration error and prevents the rebuild.

### Complete-assignment solve

Extend the global coverage solver so each remaining available person must have
one assignment edge and every enabled center has a lower bound (effective
minimum) and upper bound (effective maximum, or available-person count when
unlimited). Edges exist only for level-1-or-higher people satisfying every
required skill. Level 0 remains eligible only through a validated training
block.

Coupled crew rules continue to expose complete crew options. A result is valid
only if it satisfies all center lower/upper bounds, chooses a complete safe
crew for coupled centers, honors every default constraint, and consumes every
available person. The solver returns either one complete result or a structured
infeasibility result; there is no successful partial result.

Among complete results, min-cost ranking encodes preference overrides,
group-default center imbalance, mode scoring, rotation history, and stable
tie-breakers in the approved policy order.

### Validation and persistence

Before saving, validate the solver output independently:

- every available person appears exactly once, including people already
  manually protected;
- no absent or reserve person was generated;
- every generated assignment is in an enabled center;
- every generated person meets all required skills or has an explicit active
  training-block authorization;
- all enabled minimums and maximums are satisfied;
- every exact and group default is honored;
- every coupled crew is safe; and
- assignments outside the Auto-managed set, notes, schedule metadata,
  published snapshots, hours, and manual sources are unchanged.

Only a validated complete result is persisted. The rebuild uses the existing
single schedule-save boundary, so a solver exception, input-read error,
infeasible result, or validation failure leaves the stored schedule unchanged.

## Default Persistence

Keep the existing `work_center_default_people` table for exact defaults. Add:

```sql
CREATE TABLE IF NOT EXISTS group_default_people (
  group_name  TEXT NOT NULL REFERENCES groups(name) ON DELETE CASCADE,
  person_id   INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (group_name, person_id)
);
```

Cross-table uniqueness cannot be expressed with an ordinary SQL unique
constraint, so the store service validates that a person has at most one row
across all exact and group defaults before committing settings changes.

Existing exact defaults are not silently deleted or reassigned. If legacy data
already gives one person multiple default targets, Settings highlights the
conflict and Auto refuses a rebuild with the conflicting target names until the
planner resolves it. New saves reject conflicts immediately.

Group rename moves its default rows to the new name in the same transaction as
work-center membership and goal data. Group deletion cascades its defaults.

## Settings Experience

The existing Default People picker remains on every work-center row. The
user-managed Groups table gains a Default People column using the same compact
multi-select pattern.

The group picker shows active, non-reserve people qualified for at least one
member center. A selected person may rotate only among the member centers for
which they satisfy every required skill. The summary identifies when a selected
default is stale, unqualified for all members, or already defaulted elsewhere.

Saving Settings is atomic for default targets. A conflicting selection is not
partially applied; the page displays a concise message naming the person and
both targets. Existing stale/conflicting records remain visible so they can be
corrected rather than silently discarded.

Update the explanatory copy below the work-center table. Defaults are no
longer described as preloading a blank day. They are protected anchors used
when a planner clicks a schedule-goal button or Reset to defaults.

## Rebuild Response and Failure UX

On success, the rebuild response retains assignments, sources, reasons, and
coverage details and additionally confirms:

- total available people;
- total placed people;
- exact defaults honored; and
- group defaults honored with their selected member centers.

On infeasibility, return a non-success response without calling
`save_schedule`. The response contains structured blockers with stable codes:

| Code | Meaning |
| --- | --- |
| `person_no_enabled_qualified_center` | The person has no skill-valid edge to an enabled center. |
| `person_all_qualified_centers_full` | Qualified enabled centers exist, but total/coupled capacity cannot place the person. |
| `exact_default_center_disabled` | An available exact default targets a center Auto has turned off. |
| `exact_default_unqualified` | The exact-default person no longer meets the center's skills. |
| `group_default_no_enabled_center` | No member of the person's default group is enabled. |
| `group_default_no_qualified_center` | Group members are enabled, but none are skill-valid for the person. |
| `default_conflict` | The person has more than one saved default target. |
| `minimum_coverage_impossible` | Everyone cannot be placed while also satisfying all enabled minimums. |
| `no_safe_pair` | A coupled crew such as Trim Saw cannot be formed safely. |

The Staffing warning area names every affected person and explains the relevant
centers and rejection reasons. It also states that the previous schedule was
kept. Auto does not enable a disabled center, exceed a maximum, assign an
untrained person, use a reserve, or silently save everyone except the blockers.

## Reset to Defaults

Reset to defaults uses the same server-side complete solver rather than a
client-only per-center map. This is required because group defaults do not have
a fixed center until the target day, enabled-center set, availability, and
history are known. Reset preserves the selected mode and follows the same
all-people-or-no-save contract as the three schedule-goal buttons.

## Testing

### Pure solver

- Assign every available person while meeting all enabled minimums.
- Find two-person and three-person cross-trained swap chains that a greedy pass
  misses.
- Return infeasible rather than a partial result when one person has no edge or
  capacity is insufficient.
- Preserve one-person-one-place, min/max bounds, qualifications, and complete
  coupled crews across generated compact fixtures.
- Compare small fixtures with an exhaustive reference oracle for feasibility
  and objective ordering.
- Override `never` only when every complete solution requires it.

### Defaults and rotation

- Preserve existing exact defaults and reject disabled, unqualified, full, or
  conflicting exact targets.
- Keep a group-default person inside their configured user-managed group.
- Rotate a group default evenly across three qualified enabled centers over
  multiple days.
- Rotate only across the person's qualified subset of group centers.
- Allow capacity, minimum coverage, or crew safety to override the ideal next
  center while still placing the person.
- Ignore absent/inactive defaults for that day and continue to exclude reserves.
- Migrate group defaults through rename and delete them with the group.

### Route, persistence, and UI

- A successful goal-button rebuild places every available person and persists
  once.
- Every failure class performs zero schedule writes and leaves the previous
  assignments and metadata unchanged.
- Generated assignments never touch disabled centers.
- Manual assignments outside the enabled set survive and count as placed.
- Exact/group defaults and reason/source metadata survive render, rebuild,
  publish, posted/draft view, discard-draft, and notes-only save paths.
- Settings renders and saves group default pickers, rejects cross-target
  conflicts atomically, and exposes legacy conflicts for correction.
- The browser keeps the visible schedule unchanged after a failed rebuild and
  renders person-specific blocker details.

### Performance

Extend the plant-sized scheduler fixture with all active employees, exact and
group defaults, multi-skill swaps, and coupled crews. Preserve the existing
sub-second focused regression guard and record the complete-solve timing in the
read-only replay tool before rollout.

## Rollout

1. Add and verify additive schema and store behavior.
2. Replay representative recent schedules through the complete solver without
   writing, recording feasibility and blockers.
3. Resolve unexpected legacy default conflicts or stale skills exposed by the
   replay.
4. Switch goal-button rebuild and Reset to defaults to the complete transactional
   path together.
5. Smoke-test Settings default editing and a live multi-day group rotation.

## Acceptance Criteria

- Clicking any schedule-goal button either assigns every available non-reserve
  person safely across enabled work centers or saves nothing.
- The scheduler never reports success with a person left in Unscheduled.
- Disabled work centers are never activated or populated automatically.
- Exact defaults remain fixed and all other assignments are solved around them.
- Group defaults remain within their user-managed group and rotate evenly among
  qualified enabled member centers when possible.
- An infeasible rebuild preserves the previous schedule and names every blocker
  with actionable reasons.
- Existing safety, manual-lock, time-off, training-block, Trim Saw, published
  schedule, and metadata guarantees remain intact.
