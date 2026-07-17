# Live Work-Center Balance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh the work-center balance instantly on an On/Off toggle and show balanced status in green and any imbalance in red.

**Architecture:** Keep the server-issued balance model unchanged. The browser will recalculate the balance immediately after it flips a work-center row, then retain the existing server refresh after save success. `renderMinimumCrewBalance` will assign a semantic state class that CSS colors according to whether the direction is `ready`.

**Tech Stack:** Vanilla JavaScript, CSS, Jinja2, pytest.

## Global Constraints

- Do not change saved work-center state, API payloads, or scheduling behavior.
- The display is green only when the balance is exactly zero (`direction === 'ready'`).
- The display is red for every nonzero balance.
- A failed toggle save retains the existing rollback behavior.

---

### Task 1: Make the balance refresh and state color immediate

**Files:**
- Modify: `src/zira_dashboard/static/staffing.js:1477-1518,1700-1707`
- Modify: `src/zira_dashboard/static/staffing.css:697-700`
- Test: `tests/test_staffing_rotations.py:1790-1862`

**Interfaces:**
- Consumes: the `data-on` state on `tr[data-loc][data-minimum]` work-center rows.
- Produces: `is-balanced` on `#rotation-auto-summary` only for a ready balance and `is-unbalanced` for every other balance.
- Produces: immediate `renderMinimumCrewBalanceFromGrid()` invocation after a local row toggle and before the asynchronous save begins.

- [ ] **Step 1: Write the failing frontend-contract test**

Add the following assertions to `test_staffing_has_rotation_mode_controls_without_automated_person_notes`:

```python
    assert "summary.classList.toggle('is-balanced', balance?.direction === 'ready');" in js
    assert "summary.classList.toggle('is-unbalanced', balance?.direction !== 'ready');" in js
    assert "setWorkCenterOnState(name, !enabled);\n      renderMinimumCrewBalanceFromGrid();\n      saveAutoCenters();" in js
    assert ".minimum-crew-balance.is-balanced #minimum-crew-action { color: var(--accent); }" in css
    assert ".minimum-crew-balance.is-unbalanced #minimum-crew-action { color: var(--bad); }" in css
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_without_automated_person_notes -q
```

Expected: FAIL because the state classes, immediate refresh, and CSS rules are not present.

- [ ] **Step 3: Add the minimal browser implementation**

In `renderMinimumCrewBalance`, immediately after confirming `summary` exists, add:

```javascript
      summary.classList.toggle('is-balanced', balance?.direction === 'ready');
      summary.classList.toggle('is-unbalanced', balance?.direction !== 'ready');
```

In `toggleWorkCenterRow`, make the state update and save sequence exactly:

```javascript
      setWorkCenterOnState(name, !enabled);
      renderMinimumCrewBalanceFromGrid();
      saveAutoCenters();
```

Replace the current generic action color rule with these state-specific rules:

```css
  .minimum-crew-balance #minimum-crew-action { font-weight: 750; }
  .minimum-crew-balance.is-balanced #minimum-crew-action { color: var(--accent); }
  .minimum-crew-balance.is-unbalanced #minimum-crew-action { color: var(--bad); }
```

- [ ] **Step 4: Run the focused test to verify it passes**

Run the Step 2 command.

Expected: PASS.

- [ ] **Step 5: Run the scheduler UI regression suite**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py tests/test_staffing_static.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the implementation**

```bash
git add src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css tests/test_staffing_rotations.py
git commit -m "fix: refresh work center balance live"
```
