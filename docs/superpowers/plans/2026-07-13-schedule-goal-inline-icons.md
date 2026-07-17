# Schedule Goal Inline Icons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present the staffing schedule-goal controls in one responsive row with the approved icon-only mode buttons.

**Architecture:** The Jinja template continues to expose the existing mode button classes, data attributes, and pressed-state values consumed by `staffing.js`. It replaces the fieldset layout with a labelled flex group and icon markup. CSS changes only govern layout and icon sizing; JavaScript and scheduler API behavior remain unchanged.

**Tech Stack:** Jinja2 template, CSS, pytest.

## Global Constraints

- Preserve `.rotation-mode-btn`, `data-rotation-mode`, `aria-pressed`, `#rotation-reset-btn`, and `#rotation-mode-help` because `src/zira_dashboard/static/staffing.js` consumes them.
- The icon buttons must retain accessible names and explanatory tooltips.
- Use `⚡⚡⚡` for Optimized, `⚖` for Normal, and `🎓` for Training.
- Use the existing flex-wrap behavior for narrow widths; do not add horizontal scrolling or a separate breakpoint.
- Do not change rotation-mode scheduling, reset, warning, or help-update behavior.

---

### Task 1: Implement and verify the inline accessible icon control

**Files:**
- Modify: `src/zira_dashboard/templates/staffing.html:211-220`
- Modify: `src/zira_dashboard/static/staffing.css:638-677`
- Test: `tests/test_staffing_rotations.py:611-624`

**Interfaces:**
- Consumes: `recycled_rotation_mode` and `rotation_mode_help` supplied by the staffing route; existing client-side selectors in `staffing.js`.
- Produces: A labelled, responsive `.rotation-mode` control group whose buttons retain the selectors and state data required by `staffing.js`.

- [ ] **Step 1: Extend the static-template test with the approved icon contract**

  In `test_staffing_has_rotation_mode_controls_and_reason_data`, keep the existing `data-rotation-mode` assertions and add these assertions:

  ```python
  assert 'aria-label="Optimized schedule goal"' in html
  assert 'aria-label="Normal schedule goal"' in html
  assert 'aria-label="Training schedule goal"' in html
  assert 'title="Optimized: strongest coverage"' in html
  assert 'title="Normal: balanced coverage and fair rotation"' in html
  assert 'title="Training: develop operator skills"' in html
  assert '⚡⚡⚡' in html
  assert '⚖' in html
  assert '🎓' in html
  ```

- [ ] **Step 2: Run the focused test to verify it fails before the template is changed**

  Run: `pytest tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_and_reason_data -v`

  Expected: FAIL because the current button text and attributes do not yet match the approved icon contract.

- [ ] **Step 3: Replace the fieldset with a labelled control group**

  In `src/zira_dashboard/templates/staffing.html`, replace the `fieldset` and `legend` with this structure while preserving the existing conditional `active` class and `aria-pressed` Jinja expressions:

  ```html
  <div class="rotation-mode" role="group" aria-labelledby="rotation-mode-label">
    <span class="rotation-mode-label" id="rotation-mode-label">Schedule goal</span>
    <button type="button" class="rotation-mode-btn rotation-mode-icon {% if recycled_rotation_mode == 'optimized' %}active{% endif %}"
            data-rotation-mode="optimized" aria-pressed="{{ (recycled_rotation_mode == 'optimized')|tojson }}"
            aria-label="Optimized schedule goal" title="Optimized: strongest coverage">⚡⚡⚡</button>
    <button type="button" class="rotation-mode-btn rotation-mode-icon {% if recycled_rotation_mode == 'normal' %}active{% endif %}"
            data-rotation-mode="normal" aria-pressed="{{ (recycled_rotation_mode == 'normal')|tojson }}"
            aria-label="Normal schedule goal" title="Normal: balanced coverage and fair rotation">⚖</button>
    <button type="button" class="rotation-mode-btn rotation-mode-icon {% if recycled_rotation_mode == 'training' %}active{% endif %}"
            data-rotation-mode="training" aria-pressed="{{ (recycled_rotation_mode == 'training')|tojson }}"
            aria-label="Training schedule goal" title="Training: develop operator skills">🎓</button>
  ```

  Keep the reset button and `p#rotation-mode-help` immediately after these buttons inside the same `.rotation-mode` element.

- [ ] **Step 4: Update the control CSS for the inline row**

  In `src/zira_dashboard/static/staffing.css`, make `.rotation-controls` align items centrally and remove the fieldset-specific `legend` rule. Keep `.rotation-mode` as a wrapping flex container with the existing panel, border, and spacing language. Add these rules:

  ```css
  .rotation-mode-label {
    color: var(--muted); font-size: 0.68rem; font-weight: 700;
    letter-spacing: 0.6px; text-transform: uppercase; white-space: nowrap;
  }
  .rotation-mode-icon {
    min-width: 2.4rem; padding: 0.3rem 0.45rem;
    font-size: 1rem; line-height: 1;
  }
  .rotation-mode-help.hint {
    flex: 1 1 15rem; margin: 0; min-width: 0; padding: 0;
    text-align: left; font-style: italic;
  }
  ```

  Do not set `flex-basis: 100%` on the help text; that is the rule currently forcing the second row. The existing `flex-wrap: wrap` will preserve the scheduler's responsive behavior at narrow widths.

- [ ] **Step 5: Run the focused test to verify the contract passes**

  Run: `pytest tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_and_reason_data -v`

  Expected: PASS.

- [ ] **Step 6: Run the staffing rotation regression module**

  Run: `pytest tests/test_staffing_rotations.py -v`

  Expected: PASS with no rotation behavior regressions.

- [ ] **Step 7: Inspect the diff and commit the implementation**

  Run: `git diff --check && git diff -- src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.css tests/test_staffing_rotations.py`

  Expected: no whitespace errors; diff is limited to the control markup, its CSS, and the focused static contract test.

  ```bash
  git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.css tests/test_staffing_rotations.py
  git commit -m "feat: streamline schedule goal controls"
  ```
