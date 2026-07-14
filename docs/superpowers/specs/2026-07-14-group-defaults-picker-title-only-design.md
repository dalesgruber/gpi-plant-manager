# Group Default-People Picker: Title-Only Rows

## Purpose

Simplify the Settings → Work Centers → Groups **Default people** picker by
showing only each person's name. The current secondary text repeats the list of
work centers for which that person is qualified, such as “Repair 1, Repair 2,
Repair 3, Repair 4, Repair 5.”

## Decision

Remove the qualified-work-center hint from each picker row's rendered markup.
The picker will retain its current checkbox behavior, person names, selected
state, conflict styling, sorting, and saved-defaults behavior.

## Scope

- Change the group default-people picker rows in `templates/settings.html`.
- Retain the selected-person summary in the closed picker.
- Retain all server-side eligibility calculations and default-target validation.
- Leave other Settings pickers unchanged.

## Alternatives Considered

1. **Remove the hint markup (chosen).** Keeps the rendered UI concise with no
   unused data or styling.
2. Hide the hint with CSS. This leaves nonessential markup and styling in the
   page without providing a benefit.
3. Add a user preference to show or hide the hint. The single-purpose hint does
   not warrant new persistent configuration.

## Data Flow and Error Handling

The route will continue to calculate `eligible_centers` for each person because
it determines whether the person appears in the group picker. The template will
simply stop rendering that list. Existing conflict messages remain visible and
the save endpoint, validation, and error response behavior do not change.

## Verification

- Add a template-render regression test proving a group picker shows the person
  name but not the qualified-work-center list.
- Run the focused test and the related Settings-context tests.
