# Defer Minimum-Staffing Warnings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow planners to enable empty work centers without warnings until they explicitly rebuild or publish.

**Architecture:** Make the On/Off endpoint a configuration-only save that returns the selected centers and balance without a solver preview. Passive page context also omits current minimum shortages. Explicit rebuild and publish paths keep their current validation.

**Tech Stack:** Python, FastAPI, pytest, vanilla JavaScript.

## Global Constraints

- On/Off saves preserve enabled-center ordering and balance calculation.
- Passive loads and On/Off saves emit no `center_minimum_unmet` messages.
- Explicit rebuild and publish validation remain unchanged.
- Do not modify existing user-owned uncommitted changes.

---

### Task 1: Make the On/Off endpoint configuration-only

**Files:**
- Modify: `src/zira_dashboard/routes/rotations.py:370-442`
- Test: `tests/test_staffing_rotations.py:1459-1525`

**Interfaces:**
- Consumes: `POST /api/rotations/auto-work-centers` with `day`, `work_centers`, and `turn_off`.
- Produces: persisted `enabled_work_centers`, `minimum_crew_balance`, empty warnings, and empty coverage and placement arrays.

- [ ] **Step 1: Write the failing test**

Add `test_auto_center_selection_saves_quietly_without_solver_preview`. Patch `_recycled_suggestion_for_day` to fail if invoked; post two enabled centers; assert a 200 response, persisted selection, `warnings == []`, `coverage["issues"] == []`, and `placement["issues"] == []`.

- [ ] **Step 2: Run the failing test**

Run: `pytest tests/test_staffing_rotations.py::test_auto_center_selection_saves_quietly_without_solver_preview -q`

Expected: FAIL because the endpoint presently invokes the solver preview.

- [ ] **Step 3: Write minimal implementation**

In `save_auto_work_centers`, remove strict default/lock reads and `_recycled_suggestion_for_day`. Retain roster, schedule, and time-off reads needed by `_minimum_crew_balance_for_day`, persist `enabled`, then return `warnings: []`, `coverage: {"staffed_centers": [], "unresolved_centers": [], "issues": []}`, and a placement payload with every list empty and `defaults: {}`.

- [ ] **Step 4: Run focused endpoint tests**

Run: `pytest tests/test_staffing_rotations.py -k 'auto_center or auto_work_centers' -q`

Expected: PASS.

- [ ] **Step 5: Commit the endpoint change**

Run: `git add src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py`

Run: `git commit -m "fix: defer staffing warnings during setup"`

### Task 2: Remove passive minimum-shortage messages

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:653-768`
- Modify: `src/zira_dashboard/static/staffing.js:1580-1605`
- Test: `tests/test_staffing_rotations.py:1251-1289`
- Test: `tests/test_staffing_static.py`

**Interfaces:**
- Consumes: saved assignments during page context construction and successful On/Off responses.
- Produces: no passive minimum issue and stale automatic-staffing UI messages cleared after a successful toggle.

- [ ] **Step 1: Write the failing context test**

Rename the existing preview-failure current-shortage test to `test_recycled_context_defers_current_minimum_shortage_until_an_action`. Keep an empty enabled `Repair 1` with one qualified person, make the preview unavailable, and assert `context["rotation_issues"] == []`.

- [ ] **Step 2: Run the failing test**

Run: `pytest tests/test_staffing_rotations.py::test_recycled_context_defers_current_minimum_shortage_until_an_action -q`

Expected: FAIL because passive context adds `center_minimum_unmet`.

- [ ] **Step 3: Write minimal server implementation**

Delete `_current_minimum_coverage_issues(...)` and the `ctx["rotation_issues"]` assignment from `_recycled_context_for_day`. Retain enabled-center resolution and suggestion-derived non-action warnings. Update its docstring to say passive page context defers minimum coverage until explicit action.

- [ ] **Step 4: Write minimal browser implementation**

In `saveAutoCenters`, replace `renderCoverageIssues(data.warnings, data.coverage?.issues || [])` with `clearStaleAutoWarnings()`. Leave `applyRebuild` unchanged so rebuild responses still render diagnostics.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_staffing_rotations.py -k 'recycled_context' -q && pytest tests/test_staffing_static.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the passive-warning change**

Run: `git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/static/staffing.js tests/test_staffing_rotations.py tests/test_staffing_static.py`

Run: `git commit -m "fix: keep scheduler setup warnings quiet"`

### Task 3: Verify action and publish enforcement

**Files:**
- Test: `tests/test_staffing_rotations.py`
- Test: `tests/test_staffing_schedule_metadata.py`

**Interfaces:**
- Consumes: explicit rebuild and publish workflows.
- Produces: rebuild placement failures and publish blocking unchanged.

- [ ] **Step 1: Run rebuild-warning coverage**

Run: `pytest tests/test_staffing_rotations.py -k 'rebuild and minimum' -q`

Expected: PASS; explicit rebuild still returns `center_minimum_unmet` for a real shortage.

- [ ] **Step 2: Run publish-minimum coverage**

Run: `pytest tests/test_staffing_schedule_metadata.py -k 'publish and minimum' -q`

Expected: PASS; enabled short centers block publishing, Off centers do not.

- [ ] **Step 3: Run the complete relevant suites**

Run: `pytest tests/test_staffing_rotations.py tests/test_staffing_schedule_metadata.py tests/test_staffing_static.py -q`

Expected: PASS with no failures.
