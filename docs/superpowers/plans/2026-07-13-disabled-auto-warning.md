# Disabled Auto Work-Center Warning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Remove a stale automatic staffing warning immediately after its work center’s Auto checkbox is successfully turned off.

**Architecture:** Add a small client-side warning filter beside the existing Auto-toggle save flow in \`staffing.js\`. It recognizes the two automatic warning formats that identify a work center, preserves unrelated warnings, and reuses \`renderWarnings\` so the visual banner and \`window.ROTATION_WARNINGS\` remain in sync.

**Tech Stack:** Vanilla JavaScript and pytest static-contract tests.

## Global Constraints

- Do not rebuild the schedule or mutate assignments when an Auto checkbox changes.
- Remove only minimum-staffing and safe-pairing warnings that identify a currently disabled Auto work center.
- Preserve training-block and other non-center-specific warnings.
- Do not change warnings when saving the toggle fails.
- Do not modify the pre-existing untracked \`.claude/\` directory.

---

### Task 1: Filter stale warnings after a successful Auto-toggle save

**Files:**
- Modify: \`src/zira_dashboard/static/staffing.js:1419-1465\`
- Modify: \`tests/test_staffing_static.py\`
- Modify: \`docs/superpowers/plans/2026-07-13-disabled-auto-warning.md\`

**Interfaces:**
- Consumes \`window.ROTATION_WARNINGS\`, \`autoCbs\`, \`selectedAutoCenters()\`, and \`renderWarnings(warnings)\`.
- Produces \`removeDisabledAutoWarnings() -> void\`, called only after \`applyEnabledCenters(...)\` succeeds.

- [x] **Step 1: Write the failing static contract test**

~~~python
def test_auto_toggle_removes_only_disabled_center_warnings():
    js = _script()

    assert "function removeDisabledAutoWarnings()" in js
    assert "warning.startsWith(center + ' is staffed below its minimum')" in js
    assert "warning === 'No safe operator pairing available for ' + center + '.'" in js
    assert "renderWarnings((window.ROTATION_WARNINGS || []).filter" in js
    assert "removeDisabledAutoWarnings();" in js
~~~

- [x] **Step 2: Run the test and confirm it fails**

Run: \`ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_static.py::test_auto_toggle_removes_only_disabled_center_warnings -q\`

Expected: FAIL because the filter function and save-flow call do not exist.

- [x] **Step 3: Add the minimal filter**

Place this function after \`applyEnabledCenters\`:

~~~javascript
function removeDisabledAutoWarnings() {
  const enabled = new Set(selectedAutoCenters());
  renderWarnings((window.ROTATION_WARNINGS || []).filter(warning => {
    const center = autoCbs.map(cb => cb.dataset.loc).find(name =>
      name && (
        warning.startsWith(name + ' is staffed below its minimum')
        || warning === 'No safe operator pairing available for ' + name + '.'
      )
    );
    return !center || enabled.has(center);
  }));
}
~~~

Immediately after the successful \`applyEnabledCenters(...)\` call in \`saveAutoCenters\`, call \`removeDisabledAutoWarnings();\`. Leave the catch path unchanged so failed saves restore only the checkbox and keep warnings as-is.

- [x] **Step 4: Run focused verification**

Run: \`ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_static.py tests/test_staffing_rotations.py -q\`

Expected: PASS.

- [x] **Step 5: Mark this task complete and commit**

~~~bash
git add src/zira_dashboard/static/staffing.js tests/test_staffing_static.py docs/superpowers/plans/2026-07-13-disabled-auto-warning.md
git commit -m "fix: remove warnings for disabled auto centers"
~~~
