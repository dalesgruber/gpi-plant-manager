# Concurrency follow-up fix report — 2026-07-17

## Scope

Addressed only the final reviewer’s three remaining concurrency/bootstrap
findings for per-day auto-enabled work centers. The pre-existing dirty
`.superpowers/sdd/task-2-report.md` and untracked `uv.lock` were not edited or
staged.

## Root causes

1. `update_auto_enabled_work_centers` treated a missing locked row as an empty
   schedule, then used the full schedule upsert after the narrow update affected
   zero rows. A concurrent ordinary save could create a complete schedule in
   that gap and have its assignments overwritten by the toggle’s stale empty
   schedule.
2. `_save_schedule_with_cursor` assigned the submitted
   `EXCLUDED.auto_enabled_work_centers` on every ordinary save. A tab holding an
   older schedule could therefore restore an earlier toggle selection.
3. The schema bootstrap guarded its insert with `WHERE NOT EXISTS`, but two
   concurrent boots could both pass that check and race on the primary key.

## Changes

- `load_schedule_for_update` now distinguishes an absent schedule row from a
  real empty schedule. The toggle path conditionally inserts a bare row with
  `ON CONFLICT DO NOTHING`, reloads it under `FOR UPDATE` in a loop, and then
  performs only the daily-list update (plus explicit disabled-assignment
  deletion). It no longer invokes the full schedule upsert after a zero-row
  narrow update.
- Ordinary schedule upserts preserve
  `schedules.auto_enabled_work_centers` on the conflict branch. The initial
  insert still takes the supplied list; only the dedicated locked toggle owns
  later changes to that field.
- The schema template bootstrap insert now includes `ON CONFLICT (key) DO
  NOTHING`.

## Test-first evidence

Added regressions before production changes:

- `test_first_day_toggle_reloads_after_losing_schedule_creation_race` simulates
  a first-day toggle losing the conditional insert to a concurrent complete
  schedule creation. It asserts the created schedule assignments survive and
  no full `ON CONFLICT ... DO UPDATE` path is used.
- `test_ordinary_schedule_upsert_preserves_daily_list_owned_by_concurrent_toggle`
  asserts that ordinary save’s conflict clause retains the persisted daily list,
  preventing a stale grid submission from restoring its older value.
- The existing migration test now asserts the bootstrap conflict guard.

The RED run failed as expected with all three missing behaviors:

```text
3 failed, 26 deselected
```

## Verification

Focused regression and lint:

```text
ZIRA_API_KEY=test uv run --extra dev python -m pytest \
  tests/test_rotation_store.py tests/test_staffing_schedule_metadata.py \
  tests/test_staffing_rotations.py -q
173 passed, 1 skipped

ZIRA_API_KEY=test uv run --extra dev ruff check \
  src/zira_dashboard/staffing.py src/zira_dashboard/_schema.py \
  tests/test_rotation_store.py
All checks passed!
```

Relevant complete suite:

```text
ZIRA_API_KEY=test uv run --extra dev python -m pytest \
  tests/test_rotation_store.py tests/test_settings_auto_work_centers.py \
  tests/test_settings_group_defaults.py tests/test_staffing_rotations.py \
  tests/test_saturday_recruiting_manager_routes.py \
  tests/test_exception_inbox.py tests/test_staffing_schedule_metadata.py -q
233 passed, 3 skipped
```

`uv run --extra dev` was necessary because the worktree-local virtual
environment is unavailable; it used the repository’s configured development
environment.

## Residual concern

The protection relies on PostgreSQL’s `INSERT ... ON CONFLICT DO NOTHING`
serialization followed by a fresh `SELECT ... FOR UPDATE`, which is the normal
PostgreSQL row-creation/locking pattern. No application-level retry is needed:
after the conflicting transaction commits, the loop sees and locks its row;
after this transaction creates the row, it already owns it.
