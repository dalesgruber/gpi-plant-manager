# Work-center Row Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Staffing's visible work-center On/Off checkboxes with reliable row-area toggles that visually expand green enabled rows and compact gray disabled rows.

**Architecture:** The template exposes state with each row's `data-on` attribute and a keyboard-focusable station-name target. JavaScript tracks enabled centers from rows rather than checkbox inputs, optimistically applies a toggle, then lets the existing automatic-work-center API authoritatively reconcile the state. CSS renders state without another visible control.

**Tech Stack:** Jinja templates, vanilla JavaScript, CSS, pytest static/template assertions.

## Global Constraints

- A click toggles only unused row areas; schedule pickers, notes, links, buttons, labels, form controls, and subtitles retain their behavior.
- Enabled rows use `--accent-dim`; disabled rows are gray, compact, and show only the name and `min <count>`.
- Remove the visible checkbox and label from both template and client state model.
- Enter and Space toggle the focused station-name target; `role="switch"`, `aria-checked`, and `aria-disabled` remain current.
- `/api/rotations/auto-work-centers` is the sole persistence authority; failures restore `window.AUTO_SCHEDULE_WC_NAMES`.
- Do not modify unrelated untracked plans or `uv.lock`.

---

## File structure

- `src/zira_dashboard/templates/staffing.html`: accessible work-center target and full/minimum staffing labels.
- `src/zira_dashboard/static/staffing.js`: row-state selection, persistence, mouse toggle, and keyboard toggle.
- `src/zira_dashboard/static/staffing.css`: enabled, disabled, hover, focus, compact, and saving presentation.
- `tests/test_staffing_static.py`: interaction, accessibility, and persistence contracts.
- `tests/test_staffing_rotations.py`: template and CSS rendering contracts.

### Task 1: Write failing row-state tests

**Files:**
- Modify: `tests/test_staffing_static.py:333-384`
- Modify: `tests/test_staffing_rotations.py:1570-1620, 2277-2282`

**Interfaces:**
- Consumes: `tr[data-loc][data-on]` and `[data-work-center-toggle]`.
- Produces: regression coverage for row clicks, keyboard use, excluded control regions, API rollback, and compact visual state.

- [ ] **Step 1: Replace checkbox interaction tests**

Replace `test_work_center_row_click_toggles_only_noninteractive_row_space` and `test_auto_center_shared_save_restores_checkbox_when_schedule_is_locked` with:

```python
def test_work_center_row_clicks_and_keyboard_use_the_row_state_model():
    js = _script()

    assert "const workCenterRows = [...document.querySelectorAll('tr[data-loc]')];" in js
    assert "return workCenterRows.filter(row => row.dataset.on === 'true')" in js
    assert "function toggleWorkCenterRow(row) {" in js
    assert "setWorkCenterOnState(name, !enabled);" in js
    assert "saveAutoCenters();" in js
    assert "document.addEventListener('keydown', event => {" in js
    assert "if (!toggle || (event.key !== 'Enter' && event.key !== ' ')) return;" in js


def test_work_center_row_toggle_excludes_controls_and_rolls_back_failures():
    js = _script()

    assert ".sched-cell, .wc-note-cell" in js
    assert "target.closest('a, button, input, select, textarea, label, summary, [contenteditable=\"true\"], .sched-cell, .wc-note-cell, .sub')" in js
    assert "applyEnabledCenters(window.AUTO_SCHEDULE_WC_NAMES || []);" in js
    assert "changedCb.checked = !changedCb.checked;" not in js
    assert "const autoCbs =" not in js
```

- [ ] **Step 2: Update template and styling assertions**

In `test_staffing_rotation_controls_use_compact_icons_and_no_reason_badges`, replace the checkbox assertion with:

```python
    assert 'data-work-center-toggle' in html
    assert 'role="switch"' in html
    assert 'aria-checked="{{ _center_on|tojson }}"' in html
    assert 'class="wc-auto-cb"' not in html
    assert 'class="wc-on-off-label"' not in html
```

In `test_rotation_mode_layout_keeps_goal_and_minimum_crew_action_inline`, replace the label assertion and add:

```python
    assert 'class="ops-range-full"' in html
    assert 'class="ops-range-min"' in html
    assert '.ops-range-min { display: none; }' in css
    assert 'tr.work-center-off .ops-range-full { display: none; }' in css
    assert 'tr.work-center-off .ops-range-min { display: inline; }' in css
    assert 'tr[data-loc][data-on="true"] td { background: var(--accent-dim); }' in css
    assert '.wc-auto-toggle' not in css
```

Update `test_staffing_template_renders_auto_controls_from_the_available_context` to assert `'data-work-center-toggle' in html` instead of the checkbox class.

- [ ] **Step 3: Run the focused tests and observe expected failures**

Run:

```bash
pytest tests/test_staffing_static.py -k 'work_center_row' -v
pytest tests/test_staffing_rotations.py -k 'rotation_controls_use_compact or rotation_mode_layout or template_renders_auto_controls' -v
```

Expected: FAIL because row state, keyboard logic, template marker, and CSS markers do not yet exist.

### Task 2: Replace checkbox markup and render approved visual states

**Files:**
- Modify: `src/zira_dashboard/templates/staffing.html:310-322`
- Modify: `src/zira_dashboard/static/staffing.css:451-472`

**Interfaces:**
- Consumes: `_center_on`, `row.loc.name`, `row.min_ops`, and `row.max_ops_label`.
- Produces: `[data-work-center-toggle]`, `.ops-range-full`, `.ops-range-min`, and CSS state driven by `tr[data-on]`.

- [ ] **Step 1: Replace the checkbox and label with the keyboard-focusable station target**

Replace the current contents of the station-name `div` with:

```html
              <div class="name work-center-toggle"
                   data-work-center-toggle
                   tabindex="0"
                   role="switch"
                   aria-checked="{{ _center_on|tojson }}"
                   aria-disabled="false"
                   aria-label="Toggle automatic scheduling for {{ row.loc.name }}">
                <span>{{ row.loc.name }}</span>
                <span class="ops-range">
                  <span class="ops-range-full">min {{ row.min_ops }} · max {{ row.max_ops_label }}</span>
                  <span class="ops-range-min">min {{ row.min_ops }}</span>
                </span>
              </div>
```

Keep the parent row's `data-loc`, `data-on`, `data-minimum`, and `work-center-off` attributes. Do not alter picker, note, or subtitle markup.

- [ ] **Step 2: Replace checkbox styles with visual-state styles**

Replace the checkbox-specific CSS block with:

```css
  /* A work center is toggled from unused row space, not from a checkbox. */
  .work-center-toggle { cursor: pointer; }
  .work-center-toggle:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 3px;
    border-radius: 3px;
  }
  tr[data-loc] td.station,
  tr[data-loc] td.dept { cursor: pointer; }
  tr[data-loc][data-on="true"] td { background: var(--accent-dim); }
  tr.work-center-saving { cursor: progress; }
  tr.work-center-saving .work-center-toggle { cursor: progress; }
  .ops-range { margin-left: auto; color: var(--muted); font-size: 0.66rem; font-style: italic; font-weight: 500; }
  .ops-range-min { display: none; }
  tr.work-center-off { opacity: 0.58; }
  tr.work-center-off td { background: var(--panel-2); }
  tr.work-center-off .dept { padding: 0; color: transparent; font-size: 0; }
  tr.work-center-off .sched-cell,
  tr.work-center-off .wc-note-cell { padding: 0; }
  tr.work-center-off .sched-cell > *,
  tr.work-center-off .wc-note-cell > * { display: none; }
  tr.work-center-off .station { padding-top: 0.32rem; padding-bottom: 0.32rem; }
  tr.work-center-off .ops-range-full { display: none; }
  tr.work-center-off .ops-range-min { display: inline; }
```

- [ ] **Step 3: Run the presentation test**

Run:

```bash
pytest tests/test_staffing_rotations.py -k 'rotation_mode_layout' -v
```

Expected: PASS.

### Task 3: Move state, persistence, and events from checkboxes to rows

**Files:**
- Modify: `src/zira_dashboard/static/staffing.js:1357-1710`

**Interfaces:**
- Consumes: `tr[data-loc][data-on]`, `[data-work-center-toggle]`, `window.AUTO_SCHEDULE_WC_NAMES`, and `/api/rotations/auto-work-centers`.
- Produces: row-based selection, reconciliation, busy state, click toggling, and keyboard toggling.

- [ ] **Step 1: Replace the checkbox collection and selected-center helper**

Replace the `autoCbs` declaration with:

```javascript
    const workCenterRows = [...document.querySelectorAll('tr[data-loc]')];
```

Replace `selectedAutoCenters()` with:

```javascript
    function selectedAutoCenters() {
      return workCenterRows
        .filter(row => row.dataset.on === 'true')
        .map(row => row.dataset.loc)
        .filter(Boolean);
    }
```

- [ ] **Step 2: Reconcile row state and announce saving state**

Replace `setWorkCenterOnState`, `applyEnabledCenters`, and `setAutoCentersSaving` with:

```javascript
    function setWorkCenterOnState(name, enabled) {
      const row = document.querySelector(`tr[data-loc="${CSS.escape(name)}"]`);
      if (!row) return;
      row.dataset.on = enabled ? 'true' : 'false';
      row.classList.toggle('work-center-off', !enabled);
      const toggle = row.querySelector('[data-work-center-toggle]');
      if (toggle) toggle.setAttribute('aria-checked', enabled ? 'true' : 'false');
    }

    function applyEnabledCenters(names) {
      const enabled = new Set(names || []);
      window.AUTO_SCHEDULE_WC_NAMES = [...enabled];
      workCenterRows.forEach(row => setWorkCenterOnState(
        row.dataset.loc, enabled.has(row.dataset.loc),
      ));
      renderMinimumCrewBalanceFromGrid();
    }

    function setAutoCentersSaving(saving) {
      savingAutoCenters = saving;
      workCenterRows.forEach(row => {
        row.classList.toggle('work-center-saving', saving);
        row.setAttribute('aria-busy', saving ? 'true' : 'false');
        const toggle = row.querySelector('[data-work-center-toggle]');
        if (toggle) toggle.setAttribute('aria-disabled', saving ? 'true' : 'false');
      });
    }
```

- [ ] **Step 3: Preserve server authority and rollback optimistic state**

Change `async function saveAutoCenters(changedCb)` to `async function saveAutoCenters()`. Remove both checkbox inversions. Keep `applyEnabledCenters(data.enabled_work_centers);` on success. Add this statement immediately before `renderCoverageFailure(message);` in the catch block:

```javascript
        applyEnabledCenters(window.AUTO_SCHEDULE_WC_NAMES || []);
```

The new event handler checks locked schedules before it changes row state, so it does not need the old checkbox rollback guard.

- [ ] **Step 4: Install common mouse and keyboard row toggle routing**

Replace the old `isRowToggleInteractive` function, its click handler, and the checkbox `change` listener with:

```javascript
    function isRowToggleInteractive(target) {
      return target.closest('a, button, input, select, textarea, label, summary, [contenteditable="true"], .sched-cell, .wc-note-cell, .sub');
    }

    function toggleWorkCenterRow(row) {
      if (!row || savingAutoCenters) return;
      if (__viewingPosted || (__isPublished && !__unlocked)) return;
      const name = row.dataset.loc;
      if (!name) return;
      const enabled = row.dataset.on === 'true';
      setWorkCenterOnState(name, !enabled);
      saveAutoCenters();
    }

    document.addEventListener('click', event => {
      const row = event.target.closest('tr[data-loc]');
      if (!row || isRowToggleInteractive(event.target) || savingAutoCenters) return;
      toggleWorkCenterRow(row);
    });

    document.addEventListener('keydown', event => {
      const toggle = event.target.closest('[data-work-center-toggle]');
      if (!toggle || (event.key !== 'Enter' && event.key !== ' ')) return;
      event.preventDefault();
      toggleWorkCenterRow(toggle.closest('tr[data-loc]'));
    });
```

- [ ] **Step 5: Run the JavaScript-focused tests**

Run:

```bash
pytest tests/test_staffing_static.py -k 'work_center_row or auto_center_success_requires_server_enabled_centers' -v
```

Expected: PASS. No checkbox collection remains; controls do not toggle the row; keyboard and rollback contracts pass.

### Task 4: Verify complete Staffing behavior

**Files:**
- Verify: `src/zira_dashboard/templates/staffing.html`
- Verify: `src/zira_dashboard/static/staffing.js`
- Verify: `src/zira_dashboard/static/staffing.css`
- Verify: `tests/test_staffing_static.py`
- Verify: `tests/test_staffing_rotations.py`

**Interfaces:**
- Verifies rendered source, visual state, input routing, and API reconciliation together.

- [ ] **Step 1: Run focused Staffing tests**

Run:

```bash
pytest tests/test_staffing_static.py tests/test_staffing_rotations.py -v
```

Expected: PASS with no warnings or failures.

- [ ] **Step 2: Check diff scope and whitespace**

Run:

```bash
git diff --check
git diff -- src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css tests/test_staffing_static.py tests/test_staffing_rotations.py
git status --short
```

Expected: no whitespace errors; only intended Staffing source and test changes plus pre-existing untracked files.

- [ ] **Step 3: Commit only after verification passes**

Run:

```bash
git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css tests/test_staffing_static.py tests/test_staffing_rotations.py
git commit -m "feat: toggle staffing work centers from row space"
```

Expected: one verified implementation commit.

