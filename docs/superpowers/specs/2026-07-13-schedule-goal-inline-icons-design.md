# Schedule Goal Inline Icons

## Goal

Show the schedule-goal controls as a compact, single responsive row whose
visual language matches the surrounding staffing scheduler controls.

## Layout

- Replace the fieldset/legend presentation with an accessible labelled group.
- Keep the label, three mode choices, reset action, and live help text in one
  flex row at desktop widths.
- At narrower widths, allow controls to wrap using the scheduler's existing
  responsive behavior rather than introducing scrolling or a custom breakpoint.

## Mode controls

Mode choices become icon-only buttons while retaining their current data
attributes, pressed-state behavior, accessible names, and tooltips:

| Mode | Icon | Meaning |
| --- | --- | --- |
| Optimized | `⚡⚡⚡` | strongest coverage |
| Normal | `⚖` | balanced coverage, preferences, and fair rotation |
| Training | `🎓` | skill development while protecting coverage |

The Reset button, mode-change request flow, warning display, and live help
updates are unchanged.

## Verification

- Update the staffing template test to assert the accessible labels and icon
  content for all three modes.
- Run the relevant staffing rotation test module.
