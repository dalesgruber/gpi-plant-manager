# Task 2 — Reserve exact work centers in the scheduler

## Result

Implemented exact work-center reservations for persisted training protocols. New protocol effects reserve the configured center directly; day one also reserves the trainer there. Legacy records without `work_center` keep the existing scheduling-group effects.

## RED evidence

Command: `.venv/bin/pytest tests/test_rotation_training.py tests/test_rotation_suggestions.py -k exact_center_protocol -v`

Before implementation: `2 failed, 99 deselected`.

- `BlockEffect` did not expose `locked_work_centers`.
- The scheduler ignored direct-center effect maps, leaving `Repair 2` empty.

The manual-lock regression was also written before its implementation with `.venv/bin/pytest tests/test_rotation_suggestions.py -k manually_locked -v`. It initially failed because the trainer was ordinarily placed after the trainee's manual lock blocked the direct reservation.

## GREEN evidence

Focused verification: `.venv/bin/pytest tests/test_rotation_training.py tests/test_rotation_suggestions.py -k 'exact_center_protocol or manually_locked' -v`

Result: `3 passed, 99 deselected`.

Syntax and whitespace verification:

```console
.venv/bin/python -m compileall -q src/zira_dashboard/rotation_training.py src/zira_dashboard/rotation_suggestions.py
git diff --check
```

Result: both commands succeeded.

Full requested suite: `.venv/bin/pytest tests/test_rotation_training.py tests/test_rotation_suggestions.py -v`

Result: `82 passed, 20 failed`. All three exact-center tests passed. The 20 failures are existing broad `rotation_suggestions` behavior failures (minimum staffing, defaults, and manual-lock preservation) outside Task 2's direct-center paths; they were also present before the manual-lock follow-up change.

## Changed files

- `src/zira_dashboard/rotation_training.py`: added direct-center effect fields and emits them only for blocks with `work_center`.
- `src/zira_dashboard/rotation_suggestions.py`: consumes direct-center effects before legacy groups; warns and prevents fallback when disabled, full, or manually owned.
- `tests/test_rotation_training.py`: day-one pairing and later trainee-only direct effects.
- `tests/test_rotation_suggestions.py`: sibling-center prevention and manual-lock protection.

## Self-review

- New fields are additive and use `getattr(..., {})`, preserving legacy duck-typed effects.
- Exact block people are excluded from normal solver placement when their direct reservation cannot be honored, preventing implicit sibling fallback.
- Direct reservations retain existing generated reason metadata and trainee protection.
- The legacy group-effect loop is unchanged.

## Concerns

The full two-file suite is not green because of 20 pre-existing failures in this worktree. Task 2's targeted tests are green; broader scheduler behavior needs separate reconciliation before a fully green branch claim is possible.

## Follow-up: direct day-one pair capacity preflight

### Files changed

- `src/zira_dashboard/rotation_suggestions.py`: preflights the combined direct trainee/trainer reservation at each exact work center before placing either person.
- `tests/test_rotation_suggestions.py`: covers capacity-one centers and centers with exactly one remaining manual-occupied slot.

### RED evidence

Command:

```console
.venv/bin/pytest tests/test_rotation_suggestions.py -k 'exact_center_protocol and (capacity_is_one or only_one_slot_remains)' -v
```

Before the fix: `2 failed, 80 deselected`. Both failures showed `Trainee` placed at `Repair 2` while `Trainer` could not fit, proving the lock and temporary-extra loops reserved capacity independently.

### GREEN evidence

Command:

```console
.venv/bin/pytest tests/test_rotation_training.py tests/test_rotation_suggestions.py -k 'exact_center_protocol or manually_locked' -v
```

Output: `5 passed, 99 deselected in 0.07s`.

Additional verification:

```console
.venv/bin/python -m compileall -q src/zira_dashboard/rotation_suggestions.py
git diff --check
```

Both commands succeeded. The full requested suite remains `84 passed, 20 failed`; its same 20 unrelated scheduler-baseline failures are documented above, and all five exact-center/manual-lock tests pass.

### Self-review

- The combined reservation checks every direct trainee/trainer name and all required slots before calling `_place`, so an insufficient center leaves the pair unplaced and generates one deduplicated warning.
- Direct-only later-day trainee effects still reserve a single slot, and legacy group effects are unchanged.
- Center iteration preserves map insertion order to avoid unnecessary behavior changes in the direct-center path.
