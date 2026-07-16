# Full-row work-center toggle design

## Goal

Make a Staffing work-center row's On/Off setting easy to change by clicking
anywhere in the row, and make automatic-scheduling controls and behavior
available on all seven days of the week.

## Interaction

- Clicking a work-center row toggles its Auto On/Off checkbox and saves using
  the existing `saveAutoCenters` path.
- The checkbox remains a real, labeled checkbox for keyboard and assistive
  technology users. Its existing change handler remains the single save path.
- Row clicks originating from interactive controls do not toggle the center:
  the checkbox and label, schedule picker, notes field, links, buttons,
  selects, text inputs, and textareas keep their current behavior.
- While an Auto-center save is in progress, row activation does nothing, the
  same as the disabled checkbox state.
- After a successful save, the server-authoritative enabled-center list
  updates the checkbox, row styling, On/Off text, and capacity advisory. On
  failure, the existing rollback leaves the row in its prior state.

## Seven-day availability

- The Staffing route always exposes automatic-scheduling controls; it no
  longer disables them on Saturday.
- Consequently, the automatic scheduling controls and work-center toggles are
  present and usable Monday through Sunday. There is no weekday-only special
  case in the Staffing page context or template.

## Tests

- Replace the Saturday manual-only context expectation with a seven-day
  availability expectation.
- Add static JavaScript coverage proving a work-center-row click toggles the
  related checkbox, ignores interactive descendants, and routes the resulting
  checkbox change through the existing save handler.
- Retain existing static checks that server responses are authoritative and
  existing tests for the On/Off state update helper.

## Scope

This is limited to Staffing's Auto work-center settings and automatic
scheduling availability. It does not change unrelated plant attendance,
calendar, or work-week configuration.
