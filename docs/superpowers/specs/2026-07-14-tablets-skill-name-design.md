# Tablets Scheduler Skill Name Correction

## Goal

Allow the automatic scheduler to recognize people whose synced `Tablets` skill
is level 1 or higher as qualified for the Tablets work center.

## Problem

The work-center definition currently requires `Forklift: Tablets`, while the
Odoo-synced roster stores the skill as `Tablets`. Qualification compares skill
names exactly, so operators with a real Tablets qualification are treated as
level 0 during automatic scheduling.

## Design

Change only the static Tablets work-center requirement to the canonical synced
skill name, `Tablets`. No person skills, schedule defaults, or database records
are changed. Existing Work Orders validation remains unchanged because that
center correctly requires the distinct `Mechanic` skill.

## Verification

Add a unit test that asserts a person with `Tablets: 1` is eligible for the
Tablets scheduling target. The test must fail while the stale requirement name
is present and pass after the correction. Run the focused test and the staffing
rotation test suite.
