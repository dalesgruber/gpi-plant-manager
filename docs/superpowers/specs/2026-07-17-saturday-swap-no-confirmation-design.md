# Saturday availability swap without confirmation

## Goal

Make the Saturday Unassigned/Off swap button perform its update with one press, without presenting a confirmation dialog.

## Behavior

- Selecting the swap icon sends the existing Saturday availability update request immediately.
- The selected button is disabled while that request is in flight, preventing duplicate updates.
- When the request succeeds, the person moves to the opposite left-rail list and both list counts refresh.
- When the request fails, the person remains in the original list, the button is re-enabled, and the failure is announced to the user.

## Implementation boundary

Remove the confirmation dialog markup, its styling, and its confirmation-specific JavaScript state and event handling. Reuse the existing request and successful in-place row-move behavior directly from the swap-button click handler. The server API and persisted availability mapping are unchanged.

## Verification

Replace the static regression assertion for the dialog and `showModal()` with assertions that the direct save path is wired from the swap control. Run the focused staffing static test and the related Saturday staffing tests.
