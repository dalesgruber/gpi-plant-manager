# Schedule Goal Action-Only Status Design

## Goal

Simplify the Schedule Goal status row so it shows only the actionable
work-center recommendation while preserving the existing scheduling-goal
buttons.

## Layout

- Keep the `Schedule goal` label and Optimized, Normal, and Training buttons
  on the left in their current order.
- Remove the visible `N people waiting` and `M minimum crew slots open`
  messages from the Schedule Goal row.
- Keep the dynamic action message (for example, `Turn 3 work centers off`) as
  the sole summary message.
- Make the control container occupy the available row width and align that
  message to the far right, without wrapping at normal desktop widths.

## Behavior and accessibility

The action message remains driven by the existing minimum-crew balance data and
DOM update function. No scheduling calculation, button behavior, or status
message content changes. The output remains available as the dynamic status
element.

## Verification

Add focused frontend-contract coverage for the removed capacity messages and
the full-width, right-aligned action status. Run that focused test suite after
the change.
