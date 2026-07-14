# Partial Auto-Schedule Rebuild Design

## Goal

Make the Schedule Goal rebuild flow apply every safe assignment it can make
instead of discarding the entire proposed schedule when one person cannot be
placed or a center remains below minimum staffing.

## Scope and ownership

The rebuild endpoint is authoritative for qualification, preferences, capacity,
and assignment sources. The browser supplies the day, selected goal, and reset
flag, then applies the assignment map returned by the endpoint. Client-side
default maps remain presentation data; they do not participate in scheduling.

Only enabled auto work centers are rebuilt. Assignments outside those centers
remain untouched. Within an enabled center, an existing assignment remains only
when it is qualified for that center and the center is not over capacity.
Assignments that fail either condition are removed before automatic placement.

## Constraint model

Hard constraints block an individual assignment:

- the person must be active, available, and not already scheduled;
- the person must meet every required skill at level 1 or higher;
- the target must be both auto-enabled and an available auto work center;
- the target must have remaining configured capacity;
- coupled safety requirements (such as required trained partners) must not be
  violated.

Soft constraints are reported after assignment and never roll back a safe
placement:

- center minimum staffing remains unmet;
- a default center or group is unavailable or cannot be honored;
- a person has no eligible opening or is left unscheduled;
- preferences require a less desirable but safe placement.

## Scheduling flow

1. Load the schedule, roster, time off, auto-enabled centers, capacities,
   qualifications, preferences, and defaults.
2. Start from the current schedule. Preserve valid assignments in auto-enabled
   centers and remove invalid or over-capacity ones. Preserve non-auto centers.
3. Build eligible edges only for unscheduled available people and enabled
   auto centers with capacity. Rank candidates by skill level first, then the
   existing preference/history rank cost with deterministic name and center
   tie-breakers.
4. Greedily apply the highest safe assignment for each person, never exceeding
   capacity or violating coupled safety rules. Keep the people who have no
   safe opening in `unplaced`.
5. Merge the final auto-center assignments with untouched centers, construct
   matching assignment sources, and validate/report soft coverage/default
   warnings against that final assignment map.
6. Persist the resulting schedule and return it with `ok:true` and
   `applied:true`. A malformed request, unavailable automatic scheduling, or
   zero enabled auto centers remains a 4xx/5xx hard failure and is not saved.

## Response and UI behavior

Successful rebuilds return the full `assignments` map along with `warnings`,
`unplaced`, coverage details, reasons, and enabled work centers. This is true
even when no new person can be placed: applying the sanitized existing schedule
is still a successful rebuild.

The staffing UI reconciles the returned assignment map into Scheduled cells for
enabled centers. It renders warning and unplaced information in the existing
coverage panel as informational/yellow items. Only an actual non-OK HTTP/API
response uses the red "previous schedule kept" failure state.

## Regression coverage

Tests will prove that rebuild:

- saves and returns a partial assignment when another person is unplaceable;
- preserves valid existing assignments while filling available capacity;
- clears invalid or over-capacity assignments in enabled auto centers;
- does not reject a partial result due to minimum/default/everyone-placed
  reporting conditions;
- evaluates default/qualification messages against the final merged map; and
- keeps malformed/no-enabled-center requests as hard failures.

## Review

This design intentionally does not change manual scheduling outside enabled
auto work centers or move scheduling skill/preference logic to the browser.
