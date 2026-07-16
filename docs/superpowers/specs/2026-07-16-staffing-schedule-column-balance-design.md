# Staffing schedule column balance

## Goal

Make the staffing schedule table easier to scan by reducing unused space between
the work-center name and its minimum/maximum crew label, then using that reclaimed
space for the Scheduled and Notes fields. Scheduled operator labels should be
compact while remaining identifiable.

## Approved design

- Narrow the horizontal gap between each work-center name and its crew label.
- Redistribute the recovered width: 80% to Scheduled and 20% to Notes.
- In the Scheduled field, render each operator as first name plus last initial and
  a period (for example, `Jose O.`). This applies to existing assignments and
  choices in the operator picker; internal values and saved schedule data remain
  full names.

## Implementation boundaries

The change is presentation-only. It will update the staffing table template/CSS
and the display-label helper used by the picker without changing assignment,
autosave, or scheduling behavior.

## Verification

Add or adjust focused tests for the name-label helper and run the relevant test
suite. Verify the rendered table preserves all full-name form values while showing
abbreviated labels, and confirm the Scheduled and Notes columns receive the agreed
width allocation.
