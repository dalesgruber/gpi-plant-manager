# Saturday PDF Scheduled Staff Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Saturday Off and Time Off people from browser-print previews and Slack-generated scheduler PDFs.

**Architecture:** Keep the live scheduler unchanged. Add two selectors to the dedicated print stylesheet, which already governs both the browser print preview and Playwright's Slack-PDF render. A static regression test will lock in those selectors and their print-hidden behavior.

**Tech Stack:** CSS print media, pytest static stylesheet tests.

## Global Constraints

- Do not change the on-screen scheduler.
- Apply the behavior only when printing or rendering the Slack PDF.
- Do not alter work-center assignment data or Saturday availability state.

---

### Task 1: Hide unavailable-person rails in printed schedules

**Files:**
- Modify: `tests/test_staffing_static.py`
- Modify: `src/zira_dashboard/static/staffing-print.css`

**Interfaces:**
- Consumes: The existing print-only CSS rules for the scheduler sidebar.
- Produces: Print/PDF-only omission of `.section.saturday-off` and `.section.timeoff`.

- [ ] **Step 1: Write the failing test**

Add this test to `tests/test_staffing_static.py`:

```python
def test_printed_scheduler_hides_saturday_off_and_time_off_rails():
    css = _print_css()

    hidden_sections = css.split(".section.reserves,", 1)[1].split("{", 1)[0]

    assert ".section.saturday-off," in hidden_sections
    assert ".section.timeoff," in hidden_sections
    assert "display: none !important;" in css
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_staffing_static.py::test_printed_scheduler_hides_saturday_off_and_time_off_rails -q`

Expected: FAIL because the print stylesheet's hidden-section selector list does not yet contain the two sidebar selectors.

- [ ] **Step 3: Add the minimal print-only implementation**

In the existing hidden-selector list near the top of `src/zira_dashboard/static/staffing-print.css`, add these two entries after `.section.unscheduled,`:

```css
.section.saturday-off,
.section.timeoff,
```

Leave the existing declaration unchanged:

```css
display: none !important;
```

- [ ] **Step 4: Run the targeted test to verify it passes**

Run: `uv run pytest tests/test_staffing_static.py::test_printed_scheduler_hides_saturday_off_and_time_off_rails -q`

Expected: PASS.

- [ ] **Step 5: Run the related regression suite**

Run: `uv run pytest tests/test_staffing_static.py tests/test_staffing_saturday_recruiting.py -q`

Expected: PASS with no failures.

- [ ] **Step 6: Commit the implementation**

```bash
git add src/zira_dashboard/static/staffing-print.css tests/test_staffing_static.py
git commit -m "fix: hide unavailable staff from Saturday PDFs"
```
