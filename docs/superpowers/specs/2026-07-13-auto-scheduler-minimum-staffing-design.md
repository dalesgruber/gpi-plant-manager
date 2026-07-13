# Auto Scheduler Minimum Staffing Design

## Goal

Make automatic scheduling operationally safe: an Auto work center is either
staffed with at least its configured minimum crew or left unscheduled. Never
produce a partial automatic crew.

New schedules begin empty and manual. A manager explicitly chooses an Auto
goal to generate assignments. Saturdays remain completely manual.

## User-visible behavior

### Manual first

- A new, empty schedule has no prefilled default people or automatic
  assignments.
- The manager may assign people manually or click **Optimized**, **Normal**, or
  **Training** to run automation for the selected Auto work centers.
- Existing saved schedules are not changed merely by viewing them.
- Saturday schedules hide the Auto goal controls and Auto work-center
  checkboxes. They retain the existing manual staffing flow.

### Minimum crew rule

- Every enabled Auto work center must receive at least its effective
  `min_ops` value before automation treats it as runnable.
- Automation must never assign a nonzero crew below that minimum. A center is
  either staffed at or above its minimum, or receives no generated people.
- Existing manual assignments remain intact. The automatic feasibility
  calculation excludes people who are inactive, reserve, on full-day time off,
  manually committed elsewhere, or otherwise already scheduled.
- Safety rules such as skill qualification, Trim Saw pairing, center capacity,
  training-block protections, and one-person-per-center remain in force.
- An active training block keeps its trainee at the assigned work center. When
  that center needs additional people to meet its minimum, automation must add
  a level-3 qualified partner; it must not leave the trainee working alone.

### Insufficient staffing

- When the selected Auto centers require more available people than the day
  has, the Staffing page shows an actionable warning. It reports both the
  people short and the minimum number of Auto work centers that must be turned
  off to make a runnable selection.
- The scheduler uses a feasible subset of selected centers and leaves the
  remainder empty; it does not create partial crews or silently alter the
  manager's selected Auto centers.
- The minimum number of centers to turn off is calculated from their minimum
  crew requirements, preferring larger minimum crews when determining the
  smallest count that resolves the shortage.

### Auto-selection guardrail

- Before saving an Auto checkbox change, the server calculates the proposed
  selection against that day's available staffing.
- If enabling a center exceeds capacity, the browser opens a dialog naming the
  shortage and requiring the manager to choose enough currently enabled centers
  to turn off. The requested center is enabled only with a valid replacement
  selection.
- The server validates the replacement independently. A stale page or direct
  API request cannot persist an over-capacity Auto selection.
- Disabling Auto centers remains immediate and does not change existing manual
  assignments.

## Architecture

### Feasibility model

Add a pure, testable scheduling-feasibility helper that consumes:

- enabled Auto work-center names;
- the effective `min_ops` for each center;
- the day-specific roster after availability and manual-lock exclusions; and
- existing safety/capacity constraints needed by the scheduler.

It returns the available headcount, required minimum headcount, shortage,
minimum number of centers to disable, and a deterministic feasible center set.
The automatic suggestion engine uses that feasible set before placing people,
then keeps its existing mode scoring and safety logic within it.

Effective work-center settings, rather than only static location defaults,
provide the minimum crew values so Settings changes apply immediately.

### Staffing route and API

The Staffing route will stop seeding blank schedules from default people or
smart defaults. It will expose whether the viewed day is Saturday so the
template can present manual-only controls.

The Auto work-center settings endpoint will accept the proposed enabled list
and enough replacement centers to disable when necessary. It returns either a
validated selection and its feasibility summary or a structured conflict that
the browser uses to render the replacement dialog.

The rebuild endpoint uses the same feasibility result and returns warnings with
the generated assignments. It never creates an under-minimum center.

### Client behavior

The Staffing script will:

1. preserve the current immediate-save behavior for safe Auto-checkbox
   changes;
2. show a replacement dialog for an unsafe enable attempt;
3. submit the chosen replacement selection to the server;
4. update checkboxes and staffing warnings from the authoritative response;
   and
5. omit all Auto controls on Saturdays.

## Error handling

- A data-read or recommendation failure keeps the existing safe behavior: no
  destructive automatic reassignment.
- Invalid or insufficient replacement choices retain the previous Auto
  selection and provide a clear error message.
- A center that cannot safely meet its minimum because qualifications or
  pairing requirements are unavailable remains empty and is reported as
  unschedulable; the scheduler does not bypass safety rules to fill it.
- If a training-block trainee cannot be paired with a level-3 operator to meet
  the center minimum, the trainee remains protected but the day is reported as
  unschedulable rather than treating the trainee as a runnable crew.

## Verification

Add focused tests for:

- a two-person-minimum center receiving two people or none, never one;
- prioritizing minimum coverage before filling optional capacity;
- effective, settings-backed minimum values;
- absent, reserve, inactive, and manually assigned people reducing capacity;
- an active trainee requiring a level-3 partner to meet the center minimum;
- shortage counts and the calculated number of centers to disable;
- rejected over-capacity enable requests and accepted replacement selections;
- blank weekday schedules remaining empty until a goal is clicked;
- Saturday pages omitting Auto controls; and
- existing manual-lock, Trim Saw pairing, training, and automatic-rebuild
  regressions.
