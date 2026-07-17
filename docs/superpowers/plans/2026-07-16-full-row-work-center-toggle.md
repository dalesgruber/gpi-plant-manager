# Full-row Work-center Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let managers toggle Auto work centers by clicking any non-interactive part of their Staffing row, on every day of the week.

**Architecture:** Staffing's page context will always mark the auto scheduler available, so the existing template controls render for Saturday and Sunday as well. A delegated table-row click handler will toggle the existing checkbox and dispatch its `change` event, preserving `saveAutoCenters` as the only persistence path and excluding all interactive descendants.

**Tech Stack:** Python/FastAPI route context, Jinja template, vanilla JavaScript, pytest static and route-context tests.

## Global Constraints

- Keep the checkbox and label accessible and keyboard-operable.
- Do not toggle a row when a click begins in a label, form control, link, button, schedule picker, or notes field.
- Reuse `saveAutoCenters(changedCb)` for every persistence request.
- Auto scheduling and its controls must be available Monday through Sunday.
- Preserve the existing server-authoritative success and failure reconciliation.

---

## File structure

- `src/zira_dashboard/routes/staffing.py`: supplies the always-available auto-scheduler context flag.
- `src/zira_dashboard/static/staffing.js`: delegates eligible work-center-row clicks to the existing checkbox change/save flow.
- `tests/test_staffing_rotations.py`: verifies all-day route context and template control rendering.
- `tests/test_staffing_static.py`: verifies the static JavaScript interaction contract.

### Task 1: Expose automatic scheduling every day

**Files:**
- Modify: `tests/test_staffing_rotations.py:2204-2215`
- Modify: `src/zira_dashboard/routes/staffing.py:820-822`

**Interfaces:**
- Produces: `auto_scheduler_available: bool`, always `True` for the Staffing template.
- Consumes: `staffing_page(..., day: str, ...)` test helper and its existing captured template context.

- [ ] **Step 1: Write the failing seven-day context tests**

Replace the Saturday-only test and template-gate test with:

```python
import pytest


@pytest.mark.parametrize("day", [
    date(2026, 7, 13),  # Monday
    date(2026, 7, 18),  # Saturday
    date(2026, 7, 19),  # Sunday
])
def test_staffing_context_enables_auto_scheduler_every_day(monkeypatch, day):
    ctx = _render_staffing_page(monkeypatch, day=day)

    assert ctx["auto_scheduler_available"] is True


def test_staffing_template_renders_auto_controls_from_the_available_context():
    html = (ROOT / "src/zira_dashboard/templates/staffing.html").read_text()

    assert "{% if auto_scheduler_available %}" in html
    assert 'class="rotation-controls"' in html
    assert 'class="wc-auto-cb"' in html
```

- [ ] **Step 2: Run the focused tests and verify the Saturday assertion fails**

Run: `pytest tests/test_staffing_rotations.py -k "auto_scheduler_every_day or template_renders_auto_controls" -v`

Expected: FAIL because Saturday currently supplies `False`.

- [ ] **Step 3: Make the route context available for every day**

Replace the Saturday-specific comment and condition in `staffing_page` with:

```python
    # Automatic scheduling is an explicit manager action and is available for
    # every displayed calendar day.
    auto_scheduler_available = True
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run: `pytest tests/test_staffing_rotations.py -k "auto_scheduler_every_day or template_renders_auto_controls" -v`

Expected: PASS.

- [ ] **Step 5: Commit the all-day availability change**

```bash
git add src/zira_dashboard/routes/staffing.py tests/test_staffing_rotations.py
git commit -m "feat: enable staffing automation every day"
```

### Task 2: Toggle a work center from any eligible row click

**Files:**
- Modify: `tests/test_staffing_static.py` (after `test_auto_center_success_requires_server_enabled_centers`)
- Modify: `src/zira_dashboard/static/staffing.js:1687-1692`

**Interfaces:**
- Consumes: `tr[data-loc]`, `.wc-auto-cb`, `savingAutoCenters`, and the existing checkbox `change` listener that calls `saveAutoCenters(cb)`.
- Produces: `isRowToggleInteractive(target: Element): bool` and one delegated row-click listener; a valid row click changes the real checkbox then dispatches `change`.

- [ ] **Step 1: Write the failing static interaction test**

Add:

```python
def test_work_center_row_click_toggles_only_noninteractive_row_space():
    js = _script()

    assert "function isRowToggleInteractive(target) {" in js
    assert "target.closest('a, button, input, select, textarea, label, summary, [contenteditable=\"true\"]')" in js
    assert "document.addEventListener('click', event => {" in js
    assert "const row = event.target.closest('tr[data-loc]');" in js
    assert "if (!row || isRowToggleInteractive(event.target) || savingAutoCenters) return;" in js
    assert "const cb = row.querySelector('.wc-auto-cb');" in js
    assert "cb.checked = !cb.checked;" in js
    assert "cb.dispatchEvent(new Event('change', { bubbles: true }));" in js
```

- [ ] **Step 2: Run the focused test and verify it fails because the handler is absent**

Run: `pytest tests/test_staffing_static.py::test_work_center_row_click_toggles_only_noninteractive_row_space -v`

Expected: FAIL on the absent `isRowToggleInteractive` function.

- [ ] **Step 3: Add the delegated row-click handler before the existing checkbox listener**

Add immediately before `autoCbs.forEach(cb => {`:

```javascript
    function isRowToggleInteractive(target) {
      return target.closest('a, button, input, select, textarea, label, summary, [contenteditable="true"]');
    }

    document.addEventListener('click', event => {
      const row = event.target.closest('tr[data-loc]');
      if (!row || isRowToggleInteractive(event.target) || savingAutoCenters) return;
      const cb = row.querySelector('.wc-auto-cb');
      if (!cb || cb.disabled) return;
      cb.checked = !cb.checked;
      cb.dispatchEvent(new Event('change', { bubbles: true }));
    });

```

Do not alter the existing `change` listener; the dispatched event must invoke it so checkbox clicks, keyboard activation, and row clicks all share `saveAutoCenters(cb)`.

- [ ] **Step 4: Run the focused static interaction test and its related reconciliation checks**

Run: `pytest tests/test_staffing_static.py -k "work_center_row_click or auto_center_success_requires_server_enabled_centers or auto_toggle_failures_preserve_current_issues" -v`

Expected: PASS.

- [ ] **Step 5: Commit the full-row interaction**

```bash
git add src/zira_dashboard/static/staffing.js tests/test_staffing_static.py
git commit -m "feat: toggle work centers from the full row"
```

### Task 3: Verify the complete Staffing behavior

**Files:**
- Verify: `src/zira_dashboard/routes/staffing.py`
- Verify: `src/zira_dashboard/static/staffing.js`
- Verify: `tests/test_staffing_rotations.py`
- Verify: `tests/test_staffing_static.py`

**Interfaces:**
- Verifies the context flag, rendered controls, and delegated UI interaction contract together.

- [ ] **Step 1: Run the focused Staffing test modules**

Run: `pytest tests/test_staffing_rotations.py tests/test_staffing_static.py -v`

Expected: PASS with no failures.

- [ ] **Step 2: Inspect the final diff for unintended scope**

Run: `git diff --check HEAD~2..HEAD && git status --short`

Expected: no whitespace errors; only the two feature commits and pre-existing unrelated untracked plan files appear.
