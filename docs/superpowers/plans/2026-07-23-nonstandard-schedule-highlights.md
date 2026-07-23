# Nonstandard Schedule Highlights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make enabled work centers soft blue on custom-hour days and every Saturday or Sunday, while preserving green only for regular Monday–Friday schedules.

**Architecture:** Derive one boolean in the staffing route from the existing day and custom-hours data. The Jinja table row receives a semantic `nonstandard-schedule-day` class when that value is true; CSS overrides only enabled non-bay cells, leaving disabled rows and bay cells unchanged.

**Tech Stack:** FastAPI, Jinja, CSS, pytest.

## Global Constraints

- A nonstandard schedule is a custom-hours day, Saturday, or Sunday.
- Only enabled work centers turn soft blue; disabled work centers and bay cells retain their current colors.
- Regular Monday–Friday schedules without custom hours retain soft-green enabled rows.
- Add a plain-language What's New note for the user-facing change.

---

### Task 1: Derive and render the nonstandard schedule state

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:1401-1408,1634-1641`
- Modify: `src/zira_dashboard/templates/staffing.html:286`
- Modify: `src/zira_dashboard/static/staffing.css:520-531`
- Test: `tests/test_staffing_static.py:154-160`

**Interfaces:**
- Consumes: `d.weekday()` and `sched.custom_hours` in `staffing_page`.
- Produces: Boolean template context key `nonstandard_schedule` and optional `nonstandard-schedule-day` table-row class.

- [ ] **Step 1: Write the failing static test**

```python
def test_nonstandard_schedule_rows_use_soft_blue():
    html = _template()
    css = _style()

    assert "nonstandard-schedule-day" in html
    assert 'tr.nonstandard-schedule-day[data-on="true"] td { background: #dbeafe; }' in css
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_staffing_static.py -k nonstandard_schedule_rows`

Expected: FAIL because only the old `custom-hours-day` class and selector exist.

- [ ] **Step 3: Implement the derived state and CSS override**

```python
nonstandard_schedule = sched.custom_hours is not None or d.weekday() in {5, 6}
```

```jinja2
<tr class="{% if nonstandard_schedule %}nonstandard-schedule-day {% endif %}{% if not _center_on %}work-center-off{% endif %}">
```

```css
tr.nonstandard-schedule-day[data-on="true"] td { background: #dbeafe; }
```

Retain the existing bay-cell override and disabled-center styling.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/test_staffing_static.py tests/test_staffing_rotations.py -k 'staffing or work_center'`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.css tests/test_staffing_static.py
git commit -m "feat: highlight nonstandard schedules"
```

### Task 2: Add plain-language What’s New note

**Files:**
- Modify: `CHANGELOG.md:1`

**Interfaces:**
- Consumes: the completed user-facing highlighting behavior.
- Produces: a short 2026-07-23 entry explaining the blue weekend/special-hours rows.

- [ ] **Step 1: Add the user-facing note**

```markdown
## 2026-07-23

### Features

- **Weekend and special-hour schedules are easier to spot.** Work centers that are turned on now show in soft blue on Saturdays, Sundays, and days with special hours. Regular Monday–Friday schedules stay green.
```

- [ ] **Step 2: Verify the note is present**

Run: `rg -n "Weekend and special-hour schedules" CHANGELOG.md`

Expected: one matching entry near the top of the file.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: note nonstandard schedule highlights"
```

## Self-review

- Spec coverage: Task 1 implements the full nonstandard-day definition and preserves unaffected row treatments. Task 2 records the user-facing change in plain language.
- Placeholder scan: no placeholders or deferred work.
- Type consistency: `nonstandard_schedule` is defined in the staffing route and consumed only by the staffing template.
