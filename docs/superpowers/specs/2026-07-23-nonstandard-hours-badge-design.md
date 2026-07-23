# Nonstandard hours badge design

## Goal

Make the hours badge match the schedule-row color rule: every schedule outside
the normal Monday–Friday default is shown as a compact blue Custom schedule.

## Rule

Reuse the existing `nonstandard_schedule` view flag. When it is true, the
hours pill uses the blue custom class and compact `CUSTOM 7:00–12:00p` copy.
That includes custom-hours days, Saturdays, and Sundays. Only normal
Monday–Friday schedules keep the longer default Hours display.

## Validation

Update static template tests for the nonstandard flag in the hours pill and
the compact custom copy, then run focused staffing tests.
