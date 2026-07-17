# Reset Clears Schedule Goal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear Schedule Goal UI state after a successful defaults-only reset.

**Architecture:** Add a small helper beside `setActiveMode` in the staffing client. The reset handler calls it only after the defaults-only rebuild reports success; ordinary goal rebuilds remain unchanged.

**Tech Stack:** Vanilla JavaScript, pytest static-source tests.

## Global Constraints

- Run only after Reset to defaults succeeds.
- Clear active state, `aria-pressed`, goal-help text, and selected client-side rotation mode.
- A failed reset preserves the previous selection and help text.
- The reset API, scheduler assignments, and later goal clicks are unchanged.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/zira_dashboard/static/staffing.js` | Clear selected goal after reset success. |
| `tests/test_staffing_static.py` | Assert reset-only goal-state wiring. |

### Task 1: Clear selected goal after reset success

**Files:**

- Modify: `src/zira_dashboard/static/staffing.js:1393-1410,1607-1626`
- Test: `tests/test_staffing_static.py:246-276`

**Interfaces:**

- Consumes: `modeBtns`, `helpEl`, and `window.RECYCLED_ROTATION_MODE` from the rotation-controls closure.
- Produces: `clearActiveMode() -> void`, called only after `rebuild(..., { resetToDefaults: true })` returns `true`.

- [ ] **Step 1: Write the failing static test**

```python
def test_reset_to_defaults_clears_the_selected_schedule_goal_after_success():
    js = _script()
    rotation = js.split("// ---------- Rotation goal", 1)[1].split("// Assignments to Do modal", 1)[0]
    reset = rotation.split("const resetScheduleBtn", 1)[1].split("modeBtns.forEach", 1)[0]
    assert "function clearActiveMode()" in rotation
    assert "b.classList.remove('active');" in rotation
    assert "b.setAttribute('aria-pressed', 'false');" in rotation
    assert "window.RECYCLED_ROTATION_MODE = null;" in rotation
    assert "helpEl.textContent = '';" in rotation
    assert "if (succeeded) {" in reset
    assert "clearActiveMode();" in reset
```

- [ ] **Step 2: Verify the test fails**

Run: `.venv/bin/pytest tests/test_staffing_static.py::test_reset_to_defaults_clears_the_selected_schedule_goal_after_success -v`

Expected: FAIL because the helper and its success-only call do not yet exist.

- [ ] **Step 3: Implement the helper and success-only call**

Add after `setActiveMode`:

```javascript
function clearActiveMode() {
  modeBtns.forEach(b => {
    b.classList.remove('active');
    b.setAttribute('aria-pressed', 'false');
  });
  window.RECYCLED_ROTATION_MODE = null;
  if (helpEl) helpEl.textContent = '';
}
```

Replace the reset success line with:

```javascript
if (succeeded) {
  clearActiveMode();
  syncLeftRailWithSchedule();
}
```

- [ ] **Step 4: Verify focused and full static tests**

Run: `.venv/bin/pytest tests/test_staffing_static.py::test_reset_to_defaults_clears_the_selected_schedule_goal_after_success -v && .venv/bin/pytest tests/test_staffing_static.py -v`

Expected: PASS with zero failures.

- [ ] **Step 5: Commit**

Run: `git add src/zira_dashboard/static/staffing.js tests/test_staffing_static.py && git commit -m "fix: clear schedule goal after reset"`

### Task 2: Verify reset-only UI behavior

**Files:**

- Verify: `src/zira_dashboard/static/staffing.js`
- Verify: `tests/test_staffing_static.py`

**Interfaces:**

- Consumes: `clearActiveMode` and the successful reset handler from Task 1.
- Produces: evidence reset clears presentation state without changing automatic scheduling.

- [ ] **Step 1: Run UI and reset-route regression tests**

Run: `.venv/bin/pytest tests/test_staffing_static.py tests/test_staffing_rotations.py -v`

Expected: PASS with zero failures.

- [ ] **Step 2: Inspect diff and whitespace**

Run: `git show --check --stat HEAD && git diff HEAD~1..HEAD -- src/zira_dashboard/static/staffing.js tests/test_staffing_static.py`

Expected: no whitespace errors; only the reset goal-state helper, call site, and static test changed.

- [ ] **Step 3: Push behavior commit**

Run: `git push origin main`

Expected: remote `main` advances with the reset UI-state commit.
