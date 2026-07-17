# Schedule Goal Action-Only Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the Schedule Goal buttons on the left while showing only the dynamic work-center action at the right edge of the same full-width row.

**Architecture:** Remove the two informational status nodes from the server-rendered summary and stop updating them in the balance renderer. Retain the action node and its existing balance-driven copy, then make the containing control row full width with the action pushed to its right edge.

**Tech Stack:** Jinja HTML template, vanilla JavaScript, CSS, pytest static frontend-contract tests.

## Global Constraints

- Preserve all existing Schedule Goal buttons, balance calculations, API contracts, and dynamic action wording.
- Do not show the people-waiting or minimum-slots-open messages in the Schedule Goal row.
- Keep the action status on one line at the far right on normal desktop layouts.

---

### Task 1: Render and style only the actionable status

**Files:**
- Modify: `src/zira_dashboard/templates/staffing.html:227-232`
- Modify: `src/zira_dashboard/static/staffing.js:1476-1491`
- Modify: `src/zira_dashboard/static/staffing.css:649-696`
- Test: `tests/test_staffing_rotations.py:1500-1555`

**Interfaces:**
- Consumes: `minimum_crew_balance` in the template and `renderMinimumCrewBalance(balance)` in the browser.
- Produces: `#minimum-crew-action` as the only rendered minimum-crew status node, with its existing `Turn N work center(s) on/off` or `Ready to schedule` text.

- [ ] **Step 1: Write the failing frontend-contract test**

Add these assertions at the end of `test_staffing_has_rotation_mode_controls_without_automated_person_notes`:

```python
    assert 'id="minimum-crew-waiting"' not in html
    assert 'id="minimum-crew-slots"' not in html
    assert 'id="minimum-crew-action"' in html
    assert "const waitingEl = document.getElementById('minimum-crew-waiting');" not in js
    assert "const slotsEl = document.getElementById('minimum-crew-slots');" not in js
    assert ".rotation-mode {\\n    display: flex; flex-wrap: nowrap;" in css
    assert "width: 100%;" in css
    assert "margin-left: auto;" in css
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `pytest tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_without_automated_person_notes -v`

Expected: FAIL because the template still contains the two removed IDs and the layout is still wrapping.

- [ ] **Step 3: Make the minimal template, JavaScript, and CSS changes**

Replace the three summary children in `src/zira_dashboard/templates/staffing.html` with:

```html
          <span id="minimum-crew-action"></span>
```

In `renderMinimumCrewBalance`, remove the `waiting`, `slots`, `waitingEl`, `slotsEl`, and their `textContent` assignments. Retain:

```javascript
      const count = Number(balance?.center_count || 0);
      summary.dataset.minimumCrewBalance = JSON.stringify(balance || {});
      const actionEl = document.getElementById('minimum-crew-action');
      if (actionEl) {
        if (balance?.direction === 'ready') actionEl.textContent = 'Ready to schedule';
        else actionEl.textContent = `Turn ${count} work center${count === 1 ? '' : 's'} ${balance?.direction === 'turn_on' ? 'on' : 'off'}`;
      }
```

Update the relevant CSS so the control panel is full width, does not wrap, and the action output remains at the far right:

```css
  .rotation-mode {
    display: flex; flex-wrap: nowrap; align-items: center; gap: 0.4rem;
    width: 100%;
    border: 1px solid var(--border); border-radius: 8px;
    padding: 0.4rem 0.7rem; margin: 0; min-width: 0;
    background: var(--panel-2);
  }
  .minimum-crew-balance { margin-left: auto; }
```

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `pytest tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_without_automated_person_notes -v`

Expected: PASS.

- [ ] **Step 5: Run the related frontend tests**

Run: `pytest tests/test_staffing_rotations.py tests/test_staffing_static.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

Run: `git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css tests/test_staffing_rotations.py && git commit -m "fix: simplify schedule goal status"`
