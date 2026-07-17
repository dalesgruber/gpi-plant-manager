# Schedule Goal Rebuild Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the separate Reset control so every schedule-goal button rebuilds automated staffing with that button's configuration.

**Architecture:** The existing `rebuild(mode)` browser function already posts the chosen mode to the authoritative rebuild API on every mode-button click. Remove only the redundant Reset markup and its JavaScript references; retain the mode-button handler, which preserves all server-side manual-lock, warning, autosave, and published-schedule protections.

**Tech Stack:** Jinja templates, vanilla JavaScript, pytest.

## Global Constraints

- The three schedule-goal buttons are exactly Optimized, Normal, and Training.
- Every click on a schedule-goal button requests a rebuild with that button's mode, including a click on the active mode.
- Generated automated assignments are recalculated; manual assignments and non-Recycled work centers remain protected by the existing rebuild API.
- Do not add randomized scheduling behavior or a separate Reset replacement.
- Do not alter the API, scheduler ranking, warnings, autosave, or published-schedule protections.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/zira_dashboard/templates/staffing.html` | Renders the three schedule-goal controls; will no longer render a separate reset button. |
| `src/zira_dashboard/static/staffing.js` | Sends a rebuild request for each clicked goal button; will drop the unused reset-button references. |
| `tests/test_staffing_rotations.py` | Static UI contract test proving the reset control is absent and each goal button still calls `rebuild`. |

### Task 1: Make schedule-goal buttons the sole rebuild controls

**Files:**
- Modify: `src/zira_dashboard/templates/staffing.html:215-226`
- Modify: `src/zira_dashboard/static/staffing.js:1379-1701`
- Modify: `tests/test_staffing_rotations.py:922-944`

**Interfaces:**
- Consumes: Existing `rebuild(mode)` in `staffing.js`, which POSTs `{ day, mode }` to `/api/rotations/rebuild`.
- Produces: Three mode buttons whose click listener calls `rebuild(btn.dataset.rotationMode)` on every click, with no reset control in the DOM or JavaScript.

- [ ] **Step 1: Write the failing UI contract test**

  In `test_staffing_has_rotation_mode_controls_and_reason_data`, append these assertions after the existing rebuild API assertion:

  ```python
  assert 'rotation-reset-btn' not in html
  assert 'Reset auto assignments' not in html
  assert "const resetBtn" not in js
  assert "modeBtns.forEach(btn => {" in js
  assert "btn.addEventListener('click', () => rebuild(btn.dataset.rotationMode));" in js
  ```

- [ ] **Step 2: Run the focused test to verify it fails**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_and_reason_data -v
  ```

  Expected: FAIL because `staffing.html` still contains `rotation-reset-btn` and `staffing.js` still declares `resetBtn`.

- [ ] **Step 3: Remove the redundant reset control and JavaScript wiring**

  In `src/zira_dashboard/templates/staffing.html`, delete exactly this button:

  ```html
  <button type="button" class="rotation-reset-btn" id="rotation-reset-btn"
          title="Regenerate enabled auto work centers in the current goal, keeping manual and default locks">Reset auto assignments</button>
  ```

  In `src/zira_dashboard/static/staffing.js`:

  1. Change the rotation-controls heading comment to `// ---------- Rotation goal (mode buttons + auto-center toggles) ----------`.
  2. Delete `const resetBtn = document.getElementById('rotation-reset-btn');`.
  3. Delete both guarded reset-button disable/enable statements from `rebuild(mode)`:

     ```javascript
     if (resetBtn) resetBtn.disabled = true;
     ```

     ```javascript
     if (resetBtn) resetBtn.disabled = false;
     ```

  4. Delete the reset click listener:

     ```javascript
     if (resetBtn) {
       resetBtn.addEventListener('click', () => rebuild(currentMode()));
     }
     ```

  Keep the existing mode listener unchanged:

  ```javascript
  modeBtns.forEach(btn => {
    btn.addEventListener('click', () => rebuild(btn.dataset.rotationMode));
  });
  ```

- [ ] **Step 4: Run focused regression tests**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_and_reason_data tests/test_staffing_rotations.py::test_rebuild_preserves_manual_assignment tests/test_staffing_rotations.py::test_rebuild_leaves_non_recycled_center_untouched -v
  ```

  Expected: PASS with 3 passed. The UI contract proves Reset is gone and the active mode handler remains; the API regressions prove manual and non-Recycled assignments retain their protections.

- [ ] **Step 5: Commit the implementation**

  ```bash
  git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js tests/test_staffing_rotations.py
  git commit -m "fix: rebuild from schedule goal buttons"
  ```

## Plan Self-Review

- Spec coverage: Task 1 removes the Reset control, retains every-click goal rebuild behavior, and explicitly preserves the existing API's manual/non-Recycled protections without broadening scheduler scope.
- Placeholder scan: No incomplete markers, deferred work, or unspecified implementation steps are present.
- Type consistency: The plan uses the existing `rebuild(mode)` function and `dataset.rotationMode` property without introducing new types or interfaces.
