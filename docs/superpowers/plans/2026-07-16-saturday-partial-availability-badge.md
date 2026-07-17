# Saturday Partial Availability Badge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide Saturday availability hours for full-shift commitments and show a yellow time-range badge only for partial commitments.

**Architecture:** The staffing route passes the persisted Saturday recruitment shift into the pure staffing view builder. The builder omits commitments whose availability exactly matches that shift; the existing template and client-side roster renderer only create a badge when a display value exists. CSS changes the existing badge from blue to the warning palette.

**Tech Stack:** Python 3.11, pytest, Jinja2, vanilla JavaScript, CSS.

## Global Constraints

- Compare availability against persisted Saturday recruitment hours, not editable scheduler defaults.
- Full-shift means both availability bounds exactly equal the recruitment shift bounds.
- Preserve recruiting, scheduling, and publishing behavior; this is display-only.

---

## File Structure

- `src/zira_dashboard/routes/staffing.py`: supplies the persisted recruitment window to the view model.
- `src/zira_dashboard/staffing_view.py`: filters full-shift commitments from `saturday_availability_by_name`.
- `src/zira_dashboard/static/staffing.css`: uses warning colors for the existing partial-availability badge.
- `tests/test_staffing_saturday_recruiting.py`: verifies model filtering and warning styling.

### Task 1: Filter full-shift Saturday availability from the render model

**Files:**

- Modify: `src/zira_dashboard/staffing_view.py:20-45,145-149`
- Modify: `src/zira_dashboard/routes/staffing.py:1120-1132,1173-1180`
- Test: `tests/test_staffing_saturday_recruiting.py:49-103`

**Interfaces:**

- Consumes: `saturday_commitments: dict[str, dict[str, time]]` and `saturday_shift: tuple[time, time] | None`.
- Produces: `model["saturday_availability_by_name"]: dict[str, str]`, with only partial commitment ranges.

- [ ] **Step 1: Write failing model tests**

Pass `saturday_shift=(time(6), time(12))` to the two existing Saturday commitment tests. In `test_only_commitments_enter_saturday_unassigned`, assert:

```python
assert "Ana" not in model["saturday_availability_by_name"]
assert model["saturday_availability_by_name"]["Bob"] == "7:00 AM–11:30 AM"
```

In `test_partial_commitment_keeps_availability_after_assignment`, retain the Bob range assertion with the same shift argument.

- [ ] **Step 2: Verify the test is red**

Run: `pytest tests/test_staffing_saturday_recruiting.py::test_only_commitments_enter_saturday_unassigned -v`

Expected: FAIL because Ana's full-shift range is still in the map.

- [ ] **Step 3: Implement the smallest filter**

Extend the builder signature:

```python
def build_staffing_bays(
    roster, sched, time_off_entries, publish_blocked, enabled_work_centers=None,
    saturday_commitments=None, saturday_shift=None,
):
```

Filter the existing comprehension:

```python
saturday_availability_by_name = {
    name: f"{start.strftime('%I:%M %p').lstrip('0')}–{end.strftime('%I:%M %p').lstrip('0')}"
    for name, value in (saturday_commitments or {}).items()
    for start, end in [(value["start"], value["end"])]
    if saturday_shift is None or (start, end) != saturday_shift
}
```

When the Saturday route rebuilds this model, pass:

```python
saturday_shift=(
    (saturday_bundle.recruitment.shift_start, saturday_bundle.recruitment.shift_end)
    if saturday_bundle and saturday_bundle.recruitment.status != "cancelled"
    else None
)
```

- [ ] **Step 4: Verify the focused tests are green**

Run: `pytest tests/test_staffing_saturday_recruiting.py::test_only_commitments_enter_saturday_unassigned tests/test_staffing_saturday_recruiting.py::test_partial_commitment_keeps_availability_after_assignment -v`

Expected: PASS with Ana hidden and Bob shown in both unassigned and assigned contexts.

- [ ] **Step 5: Commit the behavior**

```bash
git add src/zira_dashboard/staffing_view.py src/zira_dashboard/routes/staffing.py tests/test_staffing_saturday_recruiting.py
git commit -m "fix: hide full-shift Saturday availability badges"
```

### Task 2: Style partial Saturday availability as a warning

**Files:**

- Modify: `src/zira_dashboard/static/staffing.css:101-113`
- Test: `tests/test_staffing_saturday_recruiting.py:332-344`

**Interfaces:**

- Consumes: the existing Jinja and JavaScript conditional creation of `.saturday-availability-badge`.
- Produces: a yellow, noninteractive warning badge for a partial availability range.

- [ ] **Step 1: Write a failing style test**

Read `src/zira_dashboard/static/staffing.css` in the existing static test, isolate
the availability-badge block, and add:

```python
css = Path("src/zira_dashboard/static/staffing.css").read_text()
badge_css = css.split(".saturday-availability-badge {", 1)[1].split("}", 1)[0]
assert "background: var(--warn-dim);" in badge_css
assert "color: var(--warn);" in badge_css
assert "border: 1px solid var(--warn);" in badge_css
```

- [ ] **Step 2: Verify the test is red**

Run: `pytest tests/test_staffing_saturday_recruiting.py::test_staffing_template_has_saturday_off_availability_and_publish_lock -v`

Expected: FAIL because the badge has hard-coded blue colors.

- [ ] **Step 3: Replace only the badge color declarations**

In `.saturday-availability-badge`, retain layout and typography while replacing its colors with:

```css
border: 1px solid var(--warn);
background: var(--warn-dim);
color: var(--warn);
```

Do not change template or JavaScript rendering conditions: the filtered model ensures dynamic and server-rendered lists only create partial badges.

- [ ] **Step 4: Verify the static test is green**

Run: `pytest tests/test_staffing_saturday_recruiting.py::test_staffing_template_has_saturday_off_availability_and_publish_lock -v`

Expected: PASS.

- [ ] **Step 5: Verify module and commit presentation change**

Run: `pytest tests/test_staffing_saturday_recruiting.py -v`

Expected: PASS with zero failures.

```bash
git add src/zira_dashboard/static/staffing.css tests/test_staffing_saturday_recruiting.py
git commit -m "style: warn on partial Saturday availability"
```

## Final Verification

- [ ] Run `pytest tests/test_staffing_saturday_recruiting.py -v` and confirm zero failures.
- [ ] Run `git diff --check` and confirm no whitespace errors.
- [ ] Confirm full 6:00 AM–12:00 PM availability is absent while 7:00 AM–11:30 AM is retained.
- [ ] Confirm the route sends persisted recruitment hours to the model.
