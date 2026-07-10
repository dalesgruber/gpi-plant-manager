# Claude Handoff — Recycled Smart Rotations

## Start here

Read these two approved documents before changing code:

1. `docs/superpowers/specs/2026-07-10-recycled-rotations-design.md`
2. `docs/superpowers/plans/2026-07-10-recycled-smart-rotations.md`

The work is intentionally being done directly on `main`.

## Current status

Task 1 of the implementation plan is complete, committed, and passed a task-scoped review. Task 2 was interrupted before it made any changes. Continue at **Task 2: Build pure group scoring and fair work-center selection** in the implementation plan.

Do not redo Task 1. Its commits, newest first, are:

- `97cfe8c fix: validate rotation schedule metadata`
- `e45a4fd fix: retain metadata when seeding next schedule`
- `5ca596e fix: preserve rotation metadata on schedule saves`
- `f1ef5e7 feat: persist recycled rotation settings`

The approved design and plan are committed as:

- `8096a7e docs: define recycled rotation scheduling`
- `0e2c3d2 docs: plan recycled smart rotations`

## Binding product decisions

- Scope automatic rotation to the Recycled groups `Dismantler`, `Repair`, and `Trim Saw`.
- Per-person/group soft preferences are exactly `primary`, `regular`, `occasional`, and `never`; missing means `regular`.
- Daily modes are exactly `optimized`, `normal` (default), and `training`.
- Rotate people within their group across individual centers fairly. For example, Repair 1 → Repair 2 → Repair 3 rather than repeatedly choosing Repair 1.
- Optimized maximizes level-3 coverage; Normal balances coverage, preference, and history; Training develops a capped number of level-1/2 operators while preserving level-3 pairing.
- Level 0 is only eligible through a training block: day one pairs the trainee with a chosen level-3 trainer; later attended workdays reserve only the trainee; full-day absences extend the block; completion promotes the target skill to level 1.
- Manual assignment locks must survive rebuilds. Generated assignment sources are exactly `generated` or `manual`.
- Preserve existing Trim Saw pairing guarantees, next-day default seeding, and safe fallbacks.

## What Task 1 added

- Additive schema for rotation preferences, training blocks, completed/absent block days, schedule mode, and assignment sources.
- `src/zira_dashboard/rotation_store.py` for preference/block persistence and validation.
- `Schedule.rotation_mode` and `Schedule.assignment_sources`, including hydration, snapshots, save/load, posted view isolation, and all known pass-through save paths.
- Validation that training blocks are only for the three Recycled groups and assignment sources have the exact `generated`/`manual` vocabulary.

Task 1 review coverage included metadata preservation through notes-only saves, regular saves, discard-draft, clear-testing-day, object API saves, posted-to-draft cache usage, and next-day smart-default seeding.

## Test baseline and environment note

Before implementation, the suite was clean except that the Playwright browser test could not launch inside the restricted sandbox. The focused browser test passed once Chromium was allowed to run outside the sandbox. Baseline result is therefore **1,499 passed, 300 skipped** with normal local browser permissions.

Use:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest -q
```

The following database-backed tests are expected to skip when `DATABASE_URL` is not configured:

```bash
tests/test_staffing_schedules_bulk.py
tests/test_staffing_custom_hours.py
```

## Workspace notes

- The pre-existing untracked `.claude/` directory is not part of this work. Do not add, remove, or modify it.
- `.superpowers/` is gitignored scratch state. It contains Codex task briefs/reports and is not required to continue; the committed plan is authoritative.
- Current branch: `main`.

## Recommended continuation

1. Run the Task 2 red tests from the implementation plan.
2. Implement only the pure recommendation engine changes in `rotation_suggestions.py` and its tests.
3. Keep the existing Trim Saw public functions/regressions intact.
4. Review Task 2 before starting Task 3.
