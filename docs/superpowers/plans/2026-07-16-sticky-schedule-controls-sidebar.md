# Sticky Schedule Controls Sidebar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep schedule automation controls and the live work-center balance visible in the Notes sidebar while relocating delivery actions into the scheduler's top row.

**Architecture:** Move only existing template markup; preserve every element ID, class, handler, and data attribute so the current staffing JavaScript continues to own behavior. Add CSS that makes the existing sidebar sticky on desktop and restores normal flow at the single-column breakpoint, without moving rotation warnings from the main panel.

**Tech Stack:** Jinja2 templates, vanilla CSS/JavaScript, pytest static-template tests.

## Global Constraints

- Do not alter the reset, clear, auto-scheduler, print, Slack, or publish JavaScript behavior.
- Keep `rotation-warnings` in the main scheduler column.
- Preserve current accessible labels, disabled states, and form submit actions.
- Use the existing 1100px single-column breakpoint to disable sticky positioning.

---

### Task 1: Relocate existing scheduler controls in the template

**Files:**
- Modify: `tests/test_staffing_rotations.py:1794-1855`
- Modify: `src/zira_dashboard/templates/staffing.html:170-249,404-418`

**Interfaces:**
- Consumes: existing `#reset-schedule-btn`, `#clear-schedule-btn`, `.rotation-controls`, `#rotation-auto-summary`, and `#rotation-warnings` JavaScript hooks.
- Produces: `.day-context` contains `.day-notes` followed by the schedule controls and reset/clear actions; `.title-actions` retains the draft/posted, print, Slack, and publish controls.

- [ ] **Step 1: Write the failing static template test**

  Add this test after `test_staffing_has_rotation_mode_controls_without_automated_person_notes` in `tests/test_staffing_rotations.py`:

  ```python
  def test_staffing_keeps_automation_controls_in_the_notes_sidebar():
      html = (ROOT / "src/zira_dashboard/templates/staffing.html").read_text()

      sidebar_start = html.index('<aside class="day-context">')
      sidebar_end = html.index('</aside>', sidebar_start)
      sidebar = html[sidebar_start:sidebar_end]
      main_start = html.index('<main class="panel">')
      main_end = html.index('</main>', main_start)
      main = html[main_start:main_end]

      assert 'class="day-notes"' in sidebar
      assert 'class="rotation-controls" data-day="{{ day }}"' in sidebar
      assert 'id="rotation-auto-summary"' in sidebar
      assert 'id="reset-schedule-btn"' in sidebar
      assert 'id="clear-schedule-btn"' in sidebar
      assert 'id="rotation-warnings"' in main
      assert 'class="rotation-controls" data-day="{{ day }}"' not in main
      assert 'id="reset-schedule-btn"' not in main
      assert 'id="clear-schedule-btn"' not in main
  ```

- [ ] **Step 2: Run the test to verify it fails**

  Run: `uv run pytest tests/test_staffing_rotations.py::test_staffing_keeps_automation_controls_in_the_notes_sidebar -q`

  Expected: FAIL because the rotation controls and reset/clear buttons are still in the main panel.

- [ ] **Step 3: Move only the template markup**

  In `src/zira_dashboard/templates/staffing.html`:

  1. Remove the existing Reset to defaults and Clear schedule buttons from directly after `#hours-editor` in the top row.
  2. Keep the existing `{% if has_snapshot %}` view toggle and `.title-actions` block in place, so Draft/Posted state, Print, Slack, and Publish/Re-publish remain in the top row.
  3. Change the auto-scheduler block in the main panel so it retains only:

  ```jinja2
  <div class="rotation-warning" id="rotation-warnings" role="alert"
       {% if not rotation_warnings and not rotation_issues %}hidden{% endif %}>
    <ul id="rotation-warning-list">
      {% for issue in rotation_issues %}
        <li class="coverage-issue" data-issue-code="{{ issue.code }}">
          <span>{{ issue.message }}</span>
          {% if issue.rejections %}
            <details class="coverage-why">
              <summary>Why?</summary>
              <ul>
                {% for rejection in issue.rejections %}
                  <li><strong>{{ rejection.person }}</strong>: {{ rejection.detail }}</li>
                {% endfor %}
              </ul>
            </details>
          {% endif %}
        </li>
      {% endfor %}
      {% set issue_messages = rotation_issues | map(attribute='message') | list %}
      {% for warning in rotation_warnings %}
        {% if warning not in issue_messages %}<li>{{ warning }}</li>{% endif %}
      {% endfor %}
    </ul>
  </div>
  ```

  4. Immediately after the closing `</div>` for `.day-notes` in `.day-context`, add the existing auto-scheduler wrapper and mode content, then the two existing buttons:

  ```jinja2
  {% if auto_scheduler_available %}
  <div class="rotation-controls" data-day="{{ day }}">
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
      <p class="hint rotation-mode-help" id="rotation-mode-help">{{ rotation_mode_help }}</p>
      <output class="rotation-auto-summary minimum-crew-balance" id="rotation-auto-summary"
              data-minimum-crew-balance='{{ minimum_crew_balance|default({}, true)|tojson }}'>
        <span id="minimum-crew-action"></span>
      </output>
    </div>
    <div class="sidebar-schedule-actions">
      <button type="button" id="reset-schedule-btn" class="clear-btn">Reset to defaults</button>
      <button type="button" id="clear-schedule-btn" class="clear-btn clear-schedule-btn">Clear schedule</button>
    </div>
  </div>
  {% endif %}
  ```

  Keep `#rotation-warnings` outside that sidebar wrapper and do not duplicate IDs.

- [ ] **Step 4: Run the static template test to verify it passes**

  Run: `uv run pytest tests/test_staffing_rotations.py::test_staffing_keeps_automation_controls_in_the_notes_sidebar -q`

  Expected: PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add tests/test_staffing_rotations.py src/zira_dashboard/templates/staffing.html
  git commit -m "feat: move schedule controls to notes sidebar"
  ```

### Task 2: Make the sidebar persistent and adapt its controls to the narrow column

**Files:**
- Modify: `tests/test_staffing_rotations.py:1794-1855`
- Modify: `src/zira_dashboard/static/staffing.css:49-50,667-724,809-847`

**Interfaces:**
- Consumes: `.day-context`, `.rotation-controls`, `.rotation-mode`, `.rotation-mode-help`, `.minimum-crew-balance`, and `.clear-btn` generated by the staffing template.
- Produces: a sticky desktop sidebar, full-width goal/status controls, a reset/clear action row, and a non-sticky mobile fallback.

- [ ] **Step 1: Write the failing CSS static test**

  Add this test after the Task 1 test in `tests/test_staffing_rotations.py`:

  ```python
  def test_staffing_notes_sidebar_is_sticky_and_mobile_safe():
      css = (ROOT / "src/zira_dashboard/static/staffing.css").read_text()

      assert ".day-context { min-width: 0; position: sticky;" in css
      assert "top: 1rem; align-self: start;" in css
      assert ".sidebar-schedule-actions { display: flex; gap: 0.45rem; }" in css
      assert ".sidebar-schedule-actions .clear-btn { flex: 1 1 0; }" in css
      assert "@media (max-width: 1100px)" in css
      assert ".day-context { order: 3; position: static; }" in css
  ```

- [ ] **Step 2: Run the test to verify it fails**

  Run: `uv run pytest tests/test_staffing_rotations.py::test_staffing_notes_sidebar_is_sticky_and_mobile_safe -q`

  Expected: FAIL because `.day-context` is not yet sticky and has no mobile position override.

- [ ] **Step 3: Add the minimal responsive CSS**

  Replace the base `.day-context` declaration near the layout grid with:

  ```css
  .day-context { min-width: 0; position: sticky; top: 1rem; align-self: start; }
  ```

  Add these rules adjacent to the existing rotation-control styles:

  ```css
  .day-context .rotation-controls { display: block; margin: 0.85rem 0 0; }
  .day-context .rotation-mode { flex-wrap: wrap; }
  .day-context .rotation-mode-help.hint { flex-basis: 100%; }
  .day-context .minimum-crew-balance { margin-left: 0; }
  .sidebar-schedule-actions { display: flex; gap: 0.45rem; }
  .sidebar-schedule-actions .clear-btn { flex: 1 1 0; }
  ```

  Replace the mobile `.day-context` rule with:

  ```css
  .day-context { order: 3; position: static; }
  ```

- [ ] **Step 4: Run the CSS test to verify it passes**

  Run: `uv run pytest tests/test_staffing_rotations.py::test_staffing_notes_sidebar_is_sticky_and_mobile_safe -q`

  Expected: PASS.

- [ ] **Step 5: Run affected regression tests**

  Run: `uv run pytest tests/test_staffing_rotations.py -q`

  Expected: PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add tests/test_staffing_rotations.py src/zira_dashboard/static/staffing.css
  git commit -m "style: keep schedule controls visible while scrolling"
  ```
