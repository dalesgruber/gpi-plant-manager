# Work-center Row Toggle Design

## Goal

Replace the visible On/Off checkbox with a dependable row-area interaction that lets managers enable or disable a Staffing work center without interfering with its other controls.

## Interaction

- Clicking a non-interactive part of a work-center row toggles that work center.
- Schedule pickers, notes, buttons, links, labels, form controls, and their descendants retain their existing behavior and never toggle the row.
- The toggle uses the existing server save and reconciliation path, so a failed save restores the authoritative enabled-center state.
- A row cannot be toggled while its change is being saved or while its schedule is read-only/locked.

## Presentation

- Enabled rows remain expanded and receive a subtle green visual treatment that indicates they are active.
- Disabled rows are gray and compact. They display only the work-center name and its minimum staffing value beside the name.
- The visible checkbox and its On/Off text are removed.

## Accessibility

- The row toggle target is keyboard-operable, exposes its on/off state to assistive technology, and does not make interactive descendants nested controls.
- Visual state is not the sole indication of whether a work center is enabled.

## Testing

- Static UI tests verify the row toggle excludes interactive descendants and calls the existing persistence path.
- Template/style tests verify no checkbox or On/Off label is rendered and that enabled/disabled state classes expose the intended compact and active presentation.
- Focused Staffing tests verify existing save/reconciliation behavior remains the state authority.
