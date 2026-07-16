# Persistent Bay Cell Color Design

## Goal

Bay cells in the interactive staffing schedule retain the exact same visible
background and text color whether their work center is enabled or disabled.

## Scope

- Apply only to the interactive staffing schedule in `staffing.css`.
- Preserve the existing disabled appearance for non-bay cells.
- Do not change print styles, templates, JavaScript, or scheduling data.

## Design

Disabled work-center rows currently use row opacity to dim their contents. Add
a more-specific bay-cell rule that restores full opacity for `td.bay` within a
disabled row. The existing state-specific background override remains in place,
so the bay background stays on `--panel-3` and its text is not faded.

## Testing

Add a static CSS regression test that verifies the disabled-row selector keeps
bay cells at full opacity and that it appears after the broad disabled-row
opacity rule. Retain the existing test that verifies the bay background
override remains after active and inactive background rules.
