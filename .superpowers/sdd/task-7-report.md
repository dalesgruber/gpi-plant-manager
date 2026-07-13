# Task 7 Report: Global Auto Scheduling Invariants and Repository Verification

## Status and commit

Task 7 is complete and committed.

- Implementation/documentation commit: `a138be4d5ffd2f80bada53181bb0abca1d9b76a6`
- Subject: `docs: explain global scheduler coverage behavior`
- The required tracked verification report was committed separately so the worktree could remain clean.
- Final tracked worktree status: clean

## Changes

- Added the 2026-07-13 planner-facing Fixes entry to `CHANGELOG.md`, using the brief's exact coverage, cross-skill swap, qualified Never override, partial-save, unresolved-Auto, and training-required language.
- Added `### Global Auto scheduling` to `CLAUDE.md` near the existing scheduler invariants, recording the pure solver authority, objective ordering, atomic crew rule, level-0 rule, structured-issue parity, and focused check command.
- Reworded the solver boundary error from the misleading `levels must be positive` to the exact accepted domain: `candidate and crew member levels must be 1, 2, or 3`.
- Updated all exact boundary assertions for both level-0 and level-4 candidate/crew cases.
- Added focused route coverage proving that a non-empty `turn_off` request removes the named center before both advisory calculation and persistence.
- Extended the safe-partial-rebuild route test to prove preservation of published state, published snapshot, daily notes, work-center notes, testing-day state, and custom hours.

## Minor review note resolutions

1. **Solver boundary wording — resolved.** The production error now states that candidate and crew levels must be 1, 2, or 3. Four parameterized/exact cases cover invalid level 0 and level 4 values for both single and crew edges.
2. **Focused route coverage — resolved.** A non-empty `turn_off` route test now verifies filtered advisory and saved selections. The partial-rebuild test now verifies every requested metadata field survives the persisted rebuild.
3. **Task 1 determinism note — no duplicate test added.** Task 6's deterministic/property coverage remains the authority, per the brief.

## TDD evidence

### RED

After changing only the expected boundary message, ran:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_schedule_solver.py::test_level_zero_single_edge_is_rejected_at_solver_boundary tests/test_schedule_solver.py::test_level_zero_crew_edge_is_rejected_at_solver_boundary tests/test_schedule_solver.py::test_level_four_edge_is_rejected_at_solver_boundary -q
```

Result: `4 failed in 0.06s`. Every failure was the expected mismatch between `levels must be 1, 2, or 3` and the old production text `levels must be positive`.

### GREEN

After the minimum production wording change and route characterization coverage, ran:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_schedule_solver.py::test_level_zero_single_edge_is_rejected_at_solver_boundary tests/test_schedule_solver.py::test_level_zero_crew_edge_is_rejected_at_solver_boundary tests/test_schedule_solver.py::test_level_four_edge_is_rejected_at_solver_boundary tests/test_staffing_rotations.py::test_rebuild_persists_safe_partial_assignments_and_reports_coverage tests/test_staffing_rotations.py::test_auto_work_centers_endpoint_removes_non_empty_turn_off_selection -q
```

Result: `6 passed in 0.27s`.

The isolated worktree had no local `.venv`; an ignored temporary symlink to the repository virtualenv was used so all prescribed commands could be run verbatim. The symlink was removed before staging, and the final status was clean.

## Required verification

### Full focused scheduler command

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_schedule_solver.py tests/test_schedule_solver_properties.py tests/test_rotation_suggestions.py tests/test_rotation_training.py tests/test_auto_schedule_capacity.py tests/test_staffing_rotations.py tests/test_staffing_static.py tests/test_staffing_trim_saw_defaults.py -q
```

Result: `228 passed in 0.55s`.

### Full pytest suite

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest -q
```

Sandbox result: `1 failed, 1759 passed, 303 skipped in 3.79s`.

The sole failure was the known host-permission baseline:

- `tests/test_preview_new_leaderboard.py::test_preview_three_family_tv_ribbon_geometry_fits_target_viewports`
- Chromium launch failed at `MachPortRendezvousServer` with `Permission denied (1100)`.

The exact blocked node was rerun with host permission:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_preview_new_leaderboard.py::test_preview_three_family_tv_ribbon_geometry_fits_target_viewports -q
```

Result: `1 passed in 0.82s`.

Therefore all 1,760 collected non-skipped tests passed when the Chromium baseline received the required host permission; 303 tests were skipped by their normal environment gates.

### Ruff

```bash
.venv/bin/python -m ruff check src tests scripts
```

Result: `All checks passed!` (exit 0).

### JavaScript syntax

```bash
node --check src/zira_dashboard/static/staffing.js
```

Result: exit 0 with no output.

### Diff and status checks before commit

```bash
git diff --check
git diff --stat
git status --short
```

Results:

- `git diff --check`: exit 0, no output.
- Diff stat: 5 files changed, 84 insertions, 4 deletions.
- Status contained only the five intentional Task 7 tracked files after removing the temporary virtualenv symlink.

### Final commit/status checks

```bash
git show --stat --oneline --decorate --summary HEAD
git diff --check
git diff --stat
git status --short
```

Results:

- The feature commit is `a138be4 docs: explain global scheduler coverage behavior`; the required report is a separate follow-up documentation commit.
- Commit stat: 5 files changed, 84 insertions, 4 deletions.
- `git diff --check`: exit 0, no output.
- `git diff --stat`: no output.
- `git status --short`: no output; tracked worktree clean.

## Binding-policy self-review

- **Global cross-skill swap:** `test_global_matching_moves_cross_trained_person_and_backfills_old_center` proves Jose Luis moves from Repair to Dismantler while Ana backfills Repair.
- **Qualified Never override only for more coverage:** `test_never_is_used_only_when_it_increases_staffed_center_count` and `test_equal_coverage_prefers_no_never_override_before_rank_or_canonical_name` prove coverage cardinality wins and equal coverage rejects unnecessary Never overrides. Suggestion/route tests preserve the structured `preference_override` reason and suppress invalid expansion advice.
- **Level 0:** solver and suggestion regressions prove no generated level-0 placement outside a validated training block, with `training_required` and “Training is required” output that does not select a trainee or trainer. Training-block tests separately prove valid trainee/partner behavior.
- **Safe partial schedules and unresolved Auto centers:** solver and rebuild route tests prove the best safe partial result is persisted, stale generated rows disappear, coverage issues are returned, and explicit `turn_off` is the only route removal mechanism. The new non-empty `turn_off` test covers that mechanism directly.
- **Metadata preservation:** the partial route rebuild now explicitly preserves published state, published snapshot, daily notes, work-center notes, testing-day flag, and custom hours.
- **Protected locks and training blocks:** manual-lock, default-lock, training-effect, conflict, and completion tests passed. Rebuilds preserve protected assignments while regenerating only owned placements.
- **Trim Saw and training safety:** focused Trim Saw pairing/default tests passed, including safe partner selection, warning without a safe partner, no third generated operator, and no invalid pair.
- **Structured issue parity:** initial page, Auto selection, rebuild, and static-rendering tests passed with the same structured issue semantics and client-side Why-details support.
- **Atomic crews:** `test_unresolved_multi_person_center_has_no_generated_partial_crew` plus coupled-center solver/property tests prove generated multi-person crews are complete or absent.
- **Determinism and performance:** exhaustive-oracle, cross-mode invariant, stable ordering, synthetic plant fixture, and actual configured plant-minimum tests passed. Both plant-sized performance guards remain below one second.

## Concerns

None. The only environmental exception was the expected sandbox-blocked Chromium launch; the exact baseline passed with host permission. No progress-ledger files were touched, and no unrelated files were staged or committed.
