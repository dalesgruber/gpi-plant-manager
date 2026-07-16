# Live Saturday Recruiting Demand Design

**Date:** 2026-07-16  
**Status:** Approved

## Goal

Keep Saturday recruiting demand synchronized with the Staffing scheduler's
currently enabled work centers. The displayed number and the persisted
recruiting openings must update immediately after a manager turns a work
center on or off, including after recruiting has already been opened.

## Behavior

- Before recruiting starts, the Recruit action shows the current number of
  enabled work centers and refreshes after each successful row toggle.
- When recruiting starts, its requested openings are the minimum crew for
  every enabled work center that has eligible recruiting qualifications.
- While the recruitment remains open, every successful On/Off save recalculates
  that same set of requested openings and persists it through the recruiting
  lifecycle store.
- Turning a center on adds its minimum-crew demand. Turning one off removes its
  unfilled demand.
- A change may never remove or reduce an opening beneath existing committed
  volunteers. In that case the server rejects the toggle update and the page
  restores its prior enabled-center state with an actionable error.
- The active recruiting summary exposes the live requested total so managers
  can see the number of people still needed; it refreshes without a page load.
- After the response deadline, the existing lifecycle rule remains in force:
  requested openings may only be reduced when that does not displace a
  commitment.

## Architecture

The existing Auto work-center save endpoint is the transaction boundary for a
row toggle. For Saturday requests it will derive recruiting counts from the
server-authoritative enabled-center list and configured minimum crews. If an
open recruiting round exists, it will call the existing recruiting-opening
update logic in the same request, then return the updated recruiting summary
alongside the enabled centers.

The browser will apply that returned summary after the successful save. It will
update the pre-recruit action label or active-recruiting need indicator using
the returned totals. It will not guess recruiting counts locally, preventing a
qualification or lifecycle rule mismatch.

## Error Handling

No state is shown as changed until the server accepts the enabled-center save
and recruitment update. A rejected update restores the authoritative enabled
center list and leaves active recruiting unchanged. Center selections that do
not map to eligible recruiting positions do not contribute demand.

## Verification

- A route-level test proves a Saturday toggle updates active recruiting
  openings to the enabled centers' combined minimum crew.
- Tests cover adding a center, removing an unfilled center, and rejecting a
  removal that would invalidate an existing commitment.
- Static browser tests prove the returned recruiting summary updates the
  visible total immediately after a successful toggle.
