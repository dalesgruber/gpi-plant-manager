# Floating Schedule Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the sidebar's notes-like Schedule Goal block with a compact, fixed lower-right Schedule tools card that keeps scheduling actions available while scrolling.

**Architecture:** Keep all existing control elements and JavaScript hooks intact. Make one template text change and use CSS to restyle and position the existing `.rotation-controls` container as a fixed desktop utility card; the existing `@media (max-width: 1100px)` breakpoint restores it to document flow.

**Tech Stack:** Jinja2 template, plain CSS, pytest static frontend-contract tests.

## Global Constraints

- Optimized displays exactly one `⚡`; Normal remains `⚖` and Training remains `🎓`.
- Preserve button IDs, `data-rotation-mode` values, ARIA labels, pressed and disabled states, JavaScript hooks, Clear confirmation, and Reset/Clear behavior.
- The card is fixed only above 1100px and returns to normal flow at 1100px and below.
- Place Reset and Clear in the existing card; Clear retains danger styling.
- Do not add client-side requests, scheduling-state changes, or animation that ignores reduced-motion preferences.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/zira_dashboard/templates/staffing.html` | Preserve the control DOM/API while changing Optimized's visual icon to one bolt. |
| `src/zira_dashboard/static/staffing.css` | Define the floating utility-card appearance, fixed desktop placement, and small-screen fallback. |
| `tests/test_staffing_rotations.py` | Assert the schedule-goal template contract and CSS placement remain correct. |
| `tests/test_staffing_static.py` | Assert Reset/Clear remain within the Schedule Tools container and the existing JS contract is untouched. |

### Task 1: Lock down the Schedule Tools markup contract

**Files:**
- Modify: `tests/test_staffing_rotations.py:2018-2081`
- Modify: `tests/test_staffing_static.py:489-520`
- Modify: `src/zira_dashboard/templates/staffing.html:396-414`

**Interfaces:**
- Consumes: existing `#rotation-mode-label`, `.rotation-mode-btn`, `#rotation-auto-summary`, `#reset-schedule-btn`, and `#clear-schedule-btn` DOM contract.
- Produces: Optimized uses one bolt while every existing selector and scheduling JavaScript lookup remains valid.

- [ ] **Step 1: Write failing template-contract tests**

  In `test_staffing_has_rotation_mode_controls_without_automated_person_notes`, replace the triple-bolt expectation with the following exact assertions immediately after the three goal-label assertions:

  ```python
  assert 'title="Optimized: strongest coverage">⚡</button>' in html
  assert '⚡⚡⚡' not in html
  assert '⚖' in html
  assert '🎓' in html
  ```

  In `test_clear_schedule_remains_a_distinct_local_autosave_action`, add this DOM-order contract after the two existing button assertions:

  ```python
  controls = html.split('<div class="rotation-controls"', 1)[1].split('</aside>', 1)[0]
  assert controls.index('id="reset-schedule-btn"') < controls.index('id="clear-schedule-btn"')
  assert controls.index('id="clear-schedule-btn"') < controls.rindex('</div>')
  ```

- [ ] **Step 2: Run the focused tests to verify the icon assertion fails**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_without_automated_person_notes \
    tests/test_staffing_static.py::test_clear_schedule_remains_a_distinct_local_autosave_action -q
  ```

  Expected: the rotation test fails because the template still contains `⚡⚡⚡`.

- [ ] **Step 3: Change Optimized to one lightning bolt without changing its interface**

  In `src/zira_dashboard/templates/staffing.html`, change only the text node inside the existing Optimized button:

  ```html
  <button type="button" class="rotation-mode-btn rotation-mode-icon {% if recycled_rotation_mode == 'optimized' %}active{% endif %}"
          data-rotation-mode="optimized" aria-pressed="{{ (recycled_rotation_mode == 'optimized')|tojson }}"
          aria-label="Optimized schedule goal" title="Optimized: strongest coverage">⚡</button>
  ```

  Do not change the button's classes, attributes, `data-rotation-mode`, or the Normal and Training buttons.

- [ ] **Step 4: Run the focused tests to verify the markup contract passes**

  Run the command from Step 2.

  Expected: `2 passed`.

- [ ] **Step 5: Commit the markup contract**

  ```bash
  git add src/zira_dashboard/templates/staffing.html tests/test_staffing_rotations.py tests/test_staffing_static.py
  git commit -m "feat: simplify schedule goal optimized icon"
  ```

### Task 2: Build the fixed Schedule Tools card and responsive fallback

**Files:**
- Modify: `tests/test_staffing_rotations.py:2070-2085`
- Modify: `src/zira_dashboard/static/staffing.css:636-707`
- Modify: `src/zira_dashboard/static/staffing.css:825-830`

**Interfaces:**
- Consumes: `.rotation-controls` containing `.rotation-mode`, `.rotation-mode-label`, `.rotation-mode-btn`, `.rotation-auto-summary`, and `.sidebar-schedule-actions`.
- Produces: a fixed lower-right card above 1100px and normal-flow card at or below 1100px, without changing the controls' DOM or JavaScript behavior.

- [ ] **Step 1: Write failing CSS contract assertions**

  At the end of `test_staffing_has_rotation_mode_controls_without_automated_person_notes`, add:

  ```python
  assert ".day-context .rotation-controls {" in css
  assert "position: fixed; right: 1.25rem; bottom: 1.25rem; z-index: 20;" in css
  assert "box-shadow: 0 16px 36px rgba(31, 41, 55, 0.18);" in css
  assert "background: linear-gradient(135deg, var(--panel), color-mix(in srgb, var(--accent-dim) 32%, var(--panel)));" in css
  assert ".day-context .rotation-mode-label::before" in css
  assert "content: '•';" in css
  assert "@media (max-width: 1100px)" in css
  assert ".day-context .rotation-controls { position: static; width: auto; }" in css
  ```

- [ ] **Step 2: Run the focused CSS contract test to verify it fails**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_without_automated_person_notes -q
  ```

  Expected: FAIL because the card is not fixed and has no floating-card styles.

- [ ] **Step 3: Add the utility-card styling and replace notes-like layout rules**

  Replace the four `.day-context` overrides and the `.sidebar-schedule-actions` rules at `src/zira_dashboard/static/staffing.css:698-703` with:

  ```css
  .day-context .rotation-controls {
    position: fixed; right: 1.25rem; bottom: 1.25rem; z-index: 20;
    display: block; width: min(17.5rem, calc(100vw - 2.5rem)); margin: 0;
    padding: 0.65rem; border: 1px solid color-mix(in srgb, var(--accent) 28%, var(--border));
    border-radius: 16px;
    background: linear-gradient(135deg, var(--panel), color-mix(in srgb, var(--accent-dim) 32%, var(--panel)));
    box-shadow: 0 16px 36px rgba(31, 41, 55, 0.18);
  }
  .day-context .rotation-mode { flex-wrap: wrap; width: auto; padding: 0; border: 0; background: transparent; }
  .day-context .rotation-mode-label { display: flex; align-items: center; gap: 0.35rem; margin-right: auto; }
  .day-context .rotation-mode-label::before { content: '•'; color: var(--accent); font-size: 1rem; line-height: 1; }
  .day-context .rotation-mode-help.hint { display: none; }
  .day-context .minimum-crew-balance { flex: 0 0 100%; margin: 0.55rem 0 0; white-space: normal; }
  .sidebar-schedule-actions { display: flex; gap: 0.45rem; margin-top: 0.6rem; }
  .sidebar-schedule-actions .clear-btn { flex: 1 1 0; }
  ```

  Add the mobile fallback inside the existing `@media (max-width: 1100px)` block:

  ```css
  .day-context .rotation-controls { position: static; width: auto; }
  ```

  Preserve `.rotation-mode-btn`, active, disabled, and `.clear-schedule-btn:hover` rules so keyboard, hover, pending, Reset, and Clear states do not regress.

- [ ] **Step 4: Run the focused CSS and static frontend contracts**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_without_automated_person_notes \
    tests/test_staffing_static.py::test_clear_schedule_remains_a_distinct_local_autosave_action -q
  ```

  Expected: `2 passed`.

- [ ] **Step 5: Run the staffing frontend suite**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_static.py tests/test_staffing_rotations.py -q
  ```

  Expected: all tests pass.

- [ ] **Step 6: Commit the floating card**

  ```bash
  git add src/zira_dashboard/static/staffing.css tests/test_staffing_rotations.py
  git commit -m "feat: float schedule tools controls"
  ```

## Plan Self-Review

- Spec coverage: Task 1 covers one lightning icon and preserves the markup/API. Task 2 covers the fixed lower-right card, its distinct visual treatment, embedded status and actions, and mobile normal-flow fallback.
- Placeholder scan: no deferred or ambiguous tasks remain.
- Type/selector consistency: every CSS selector in Task 2 already exists in the template, and every tested DOM ID is preserved by Task 1. The user-approved status row uses a wrapping flex container so the live action cannot compete with the three mode buttons.
