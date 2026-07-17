# Persistent Bay Cell Gray Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep staffing-schedule bay label cells on the darker gray theme surface regardless of the enabled state of their first work center.

**Architecture:** The staffing table already gives bay cells the `--panel-3` background, but later enabled and disabled row-state selectors override it. Add one more specific, screen-only CSS selector after those row-state rules to restore `--panel-3` for a bay cell. A static CSS regression test protects the selector and its source order.

**Tech Stack:** Python 3.11, pytest, Jinja templates, CSS custom properties.

## Global Constraints

- Change only the interactive on-screen staffing schedule.
- Use `--panel-3` for the persistent darker gray so established themes remain intact.
- Preserve existing green state treatment for non-bay cells.
- Do not alter `src/zira_dashboard/static/staffing-print.css`, templates, JavaScript, or scheduling data.

---

### Task 1: Preserve the bay-cell background across work-center states

**Files:**
- Modify: `tests/test_staffing_static.py`
- Modify: `src/zira_dashboard/static/staffing.css:459-474`

**Interfaces:**
- Consumes: `_style() -> str`, which reads `src/zira_dashboard/static/staffing.css`.
- Produces: `test_staffing_bay_cells_keep_panel_background_across_work_center_states() -> None`, validating the CSS rule that preserves bay backgrounds.

- [ ] **Step 1: Write the failing test**

Add this test after the existing static staffing CSS tests in `tests/test_staffing_static.py`:

```python
def test_staffing_bay_cells_keep_panel_background_across_work_center_states():
    css = _style()

    active = 'tr[data-loc][data-on="true"] td { background: var(--accent-dim); }'
    inactive = 'tr.work-center-off td { background: var(--panel-2); }'
    bay_override = (
        'tr[data-loc][data-on="true"] td.bay,\n'
        '  tr.work-center-off td.bay { background: var(--panel-3); }'
    )

    assert bay_override in css
    assert css.index(bay_override) > css.index(active)
    assert css.index(bay_override) > css.index(inactive)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_staffing_static.py::test_staffing_bay_cells_keep_panel_background_across_work_center_states -v`

Expected: FAIL because the `bay_override` CSS rule does not yet exist.

- [ ] **Step 3: Write the minimal implementation**

In `src/zira_dashboard/static/staffing.css`, add the following directly after the `tr.work-center-off td` rule:

```css
  tr[data-loc][data-on="true"] td.bay,
  tr.work-center-off td.bay { background: var(--panel-3); }
```

This selector matches the enabled and disabled row states, but changes only the bay cell. Its placement after the row-state selectors makes the darker gray persistent while leaving every other row cell’s state color unchanged.

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `pytest tests/test_staffing_static.py::test_staffing_bay_cells_keep_panel_background_across_work_center_states -v`

Expected: PASS with `1 passed`.

- [ ] **Step 5: Run the static staffing regression suite**

Run: `pytest tests/test_staffing_static.py -v`

Expected: PASS with no failures.

- [ ] **Step 6: Commit the implementation**

```bash
git add src/zira_dashboard/static/staffing.css tests/test_staffing_static.py
git commit -m "fix: keep staffing bay cells gray"
```
