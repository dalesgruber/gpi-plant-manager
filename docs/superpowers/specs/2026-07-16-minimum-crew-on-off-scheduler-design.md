# Minimum-Crew On/Off Scheduler Design

## Goal

Make staffing automation understandable and controllable from the scheduler:
show whether the enabled work centers have too few or too many minimum-crew
slots for the people waiting to be assigned, let planners toggle centers On or
Off without losing context, and have schedule-goal buttons fill only enabled
minimum crews.

## Live staffing balance

The Schedule Goal area replaces the current raw `Auto On - unassigned` delta
with one direct, live instruction:

- `N people waiting` is the current Unscheduled list count.
- `M minimum crew slots open` is the sum, across enabled centers, of
  `max(minimum staffing - safely assigned people, 0)`.
- When `M == N`, show `Ready to schedule`.
- When `M > N`, show `Turn X work centers off` and the excess minimum slots.
- When `M < N`, show `Turn X work centers on` and the missing minimum slots.

The recommendation selects the smallest practical number of work centers whose
minimum slot contributions move the balance toward equality. It is advisory:
it never changes toggles itself. A center already safely staffed to its minimum
contributes no open slots. Maximum capacity is not used for this balance.

The browser updates the count immediately after a successful On/Off save and
after any scheduler selection changes. The server provides the initial model
and remains authoritative for worker safety, time off, qualifications, and
saved center state.

## Work-center presentation and toggles

Replace user-facing `Auto` terminology with an explicit On/Off toggle.

- An On center keeps the existing full work-center row, including scheduling
  picker, notes, and its enabled toggle.
- An Off center collapses to one thin, muted row showing its bay, work-center
  name, minimum staffing, and Off toggle. It has no picker or notes area.
- Switching On expands the center back into the current full-row presentation;
  switching Off collapses it after the server accepts the setting.
- Existing manual assignments are preserved by the backend safety rules; an
  Off row remains discoverable and can be turned back On in one click.

## Scheduling behavior

The Optimized, Normal, and Training buttons rebuild only enabled On centers.
They place qualified people until each enabled center reaches its configured
minimum, then stop; spare maximum capacity is intentionally not filled.
People beyond those required minimums remain Unscheduled until the planner
turns additional centers On or assigns them manually.

Exact work-center defaults are hard reservations: a qualified, available
default person is placed at that exact enabled center before ordinary candidate
selection. Group defaults remain constrained to enabled, qualified member
centers. Existing safety rules for manual locks, training blocks, absence,
qualification, capacity, and pairing remain in force.

## Warnings and failure handling

Placement and minimum warnings must always describe the schedule currently
shown. Rebuild responses apply their own fresh warnings; manual picker changes
invalidate old Auto-result warnings instead of leaving stale messages on the
page. The initial page render derives warnings from the saved schedule and
current enabled state.

An On/Off save failure restores the previous toggle and retains the prior
visible state. A rebuild may still report genuine shortages or unsafe minimum
crews, but it cannot claim that a visibly assigned person is unplaced or that a
visibly minimum-staffed center is below minimum.

## Verification

Tests will cover minimum-slot balance and advisory direction, live DOM summary
updates, On/Off row presentation contracts, exact-default prioritization,
minimum-only rebuild results, and stale-warning removal. Focused route,
scheduler, and frontend-contract tests will run before the full relevant suite.
