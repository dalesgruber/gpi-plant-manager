# Defer Minimum-Staffing Warnings Design

## Goal

Let a planner turn a work center On while they are still setting up the daily
schedule, without immediately filling the scheduler page with minimum-staffing
warnings.

## Current behavior

The scheduler treats enabled work centers as immediately required to meet their
configured minimum.  That validation runs both when the page is rendered and
when the On/Off selection is saved.  An empty center therefore produces a
banner warning even though the planner has not asked the scheduler to place
anyone there yet.

## Alternatives considered

### 1. Defer minimum-staffing warnings until a scheduling or publish action — selected

Treat On/Off as configuration only.  It updates the enabled-center state and
the minimum-crew balance, but returns no coverage or placement warnings and
does not show current minimum shortages on an ordinary page load.  An explicit
Schedule Goal rebuild still returns its placement failures, and Publish keeps
its existing minimum-staffing validation.

### 2. Suppress only the warning from the On/Off response

This would leave warnings on page refresh, so it would not actually let the
planner set up an enabled, empty center quietly.

### 3. Suppress warnings for newly enabled centers only

This requires tracking when every center was enabled and creates an arbitrary
difference between equally empty work centers.  It also does not match the
planner's intent: warnings are useful after asking to schedule, not while
configuring what may be scheduled.

## Selected behavior

- Turning a work center On or Off persists the selection and updates the live
  minimum-crew balance without rendering staffing warnings.
- Initial scheduler renders do not show `center_minimum_unmet` messages just
  because enabled centers are empty or short.
- Schedule Goal rebuild responses continue to display placement and coverage
  failures for the generated result.
- Publish validation remains unchanged and still blocks an enabled center that
  has fewer than its configured minimum assignments.
- Other non-minimum warnings (such as training and configuration problems)
  retain their existing behavior unless they arise solely from the obsolete
  toggle-time solver preview.

## Implementation outline

The On/Off API is a configuration save, so it should no longer run the solver
solely to manufacture a warning payload.  It returns empty warning, coverage,
and placement issue arrays after saving the enabled list and calculating the
existing balance payload.  The browser clears the old warning banner from a
successful toggle response.  The staffing page context stops adding current
minimum-coverage issues during its passive render; explicit rebuild and publish
paths retain their existing validation.

## Verification

- A successful On/Off request with an unresolved solver suggestion returns no
  warnings, no coverage issues, and no placement issues while retaining the
  persisted center list and balance.
- A current empty enabled center produces no page-load minimum-warning issue.
- An explicit rebuild continues to return a real minimum placement issue.
- Existing publish-minimum tests continue to prove that publishing an
  understaffed enabled center is rejected.
