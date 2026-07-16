# Suppress Off-Saturday Default-Center Warnings Design

## Purpose

Avoid showing misleading "default work center … is not enabled" staffing warnings when the selected Saturday is an off day and employees are not expected to work.

## Scope

- Suppress only structured placement issues with code `exact_default_center_disabled`.
- Suppress them only when the selected day is Saturday and Saturday is not a configured plant workday.
- Keep all other staffing warnings and all Saturday recruiting/status notices visible.
- Keep the existing validation unchanged for weekdays and for Saturdays configured as working days.

## Architecture

Apply a small, route-local issue filter while constructing the Staffing page context. The filter takes the selected date, configured working weekdays, and scheduler issues. On a non-working Saturday, it removes only `exact_default_center_disabled` issues; otherwise it returns the issues unchanged. The scheduling solver and explicit Auto/rebuild API responses remain untouched, so they retain their current validation contract.

## Error Handling

The filter is intentionally narrow. Unknown issue codes, malformed issues, and non-default-center validation problems continue to render normally. If Saturday becomes a configured workday, default-center-disabled warnings render normally again without data migration or configuration changes.

## Verification

Add focused route/helper tests proving that:

1. off-Saturday page context omits `exact_default_center_disabled` warnings;
2. an off-Saturday context retains unrelated warnings; and
3. weekday and configured-working-Saturday contexts retain default-center-disabled warnings.

## Acceptance Criteria

On an off Saturday, Staffing no longer displays the default-work-center-disabled banner shown in the reported screenshot. Weekday and working-Saturday validation remains unchanged, and Saturday recruiting notices remain visible.
