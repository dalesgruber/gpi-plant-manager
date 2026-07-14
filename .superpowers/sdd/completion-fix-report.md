# Unified training protocol completion fixes

## Root cause

`reconcile_blocks()` only counted already-persisted training-day records, but
the scheduler never persisted elapsed attended/absent days. It also performed
the external skill-level promotion before changing the block status, allowing
two concurrent requests to promote the same block.

## Red

Added focused regression tests for:

- recording elapsed working days as `attended` or `absent`, then completing
  once the planned attended-day count is reached;
- two concurrent reconciliation calls, where only the caller that wins the
  completion claim may invoke the skill writer.

Before implementation, the focused test file failed at setup because the new
claim API did not exist.

## Green

- Reconciliation now owns elapsed-day recording for prior workdays. Existing
  day records are preserved; an attendance-source error records nothing.
- `rotation_training_blocks.status` now has a durable `completing` state.
  `claim_completion()` uses one conditional `UPDATE ... RETURNING` statement,
  so exactly one concurrent reconciler can perform the external promotion.
- A failed promotion releases the claim back to `active` for a later retry.

## Verification

| Command | Result |
| --- | --- |
| `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_rotation_training.py -q` | 25 passed |
| `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_rotation_training.py tests/test_rotation_store.py tests/test_staffing_rotations.py -q` | 131 passed, 1 skipped |
| `ZIRA_API_KEY=test .venv/bin/python -m pytest -q` | 1803 passed, 304 skipped, 22 pre-existing/unrelated failures |

The full-suite failures are in `test_preview_new_leaderboard.py` (sandboxed
Playwright launch) plus baseline rotation-suggestions/template expectations;
the focused protocol suites are green.
