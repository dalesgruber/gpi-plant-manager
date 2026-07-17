# Suppress Off-Saturday Default-Center Warnings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide default-work-center-disabled Staffing warnings only on Saturdays that are not configured plant workdays.

**Architecture:** Keep solver and rebuild validation unchanged. Add a pure Staffing route helper that filters page-only placement issues using the selected date and configured weekdays, then use it while building passive Recycled Staffing context.

**Tech Stack:** Python 3.11, FastAPI, pytest.

## Global Constraints

- Suppress only `exact_default_center_disabled` placement issues.
- Suppress only when the selected date is Saturday and Saturday is not a configured plant workday.
- Keep unrelated Staffing warnings, Saturday recruiting/status notices, solver output, and explicit Auto/rebuild API responses unchanged.
- Weekday and configured-working-Saturday behavior must remain unchanged.

---

## File Structure

- Modify `src/zira_dashboard/routes/staffing.py`: pure filter and passive-context wiring.
- Modify `tests/test_staffing_rotations.py`: focused unit coverage.

### Task 1: Filter passive Staffing placement issues on off Saturdays

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:672-770`
- Modify: `tests/test_staffing_rotations.py`

**Interfaces:**
- Produces: `_page_placement_issues_for_day(d: date, work_weekdays: frozenset[int], issues: tuple[schedule_solver.PlacementIssue, ...]) -> tuple[schedule_solver.PlacementIssue, ...]`.
- Consumes: `ScheduleStore.current().work_weekdays`, using the existing Monday-through-Friday fallback when no weekdays are configured.
- Preserves: `rotation_suggestions.suggest_recycled_assignments` output and explicit rebuild API payloads.

- [ ] **Step 1: Write the failing off-Saturday test**

Add near direct Staffing-route helper tests in `tests/test_staffing_rotations.py`:

```python
def test_page_placement_issues_hide_only_disabled_defaults_on_off_saturday():
    from zira_dashboard.routes import staffing as staffing_route

    disabled_default = schedule_solver.PlacementIssue(
        code="exact_default_center_disabled", person="Ana", centers=("Repair 1",),
        message="Ana's default work center Repair 1 is not enabled.",
    )
    unrelated = schedule_solver.PlacementIssue(
        code="exact_default_unqualified", person="Ben", centers=("Repair 2",),
        message="Ben is not qualified for default work center Repair 2.",
    )

    assert staffing_route._page_placement_issues_for_day(
        date(2026, 7, 18), frozenset({0, 1, 2, 3, 4}),
        (disabled_default, unrelated),
    ) == (unrelated,)
```

- [ ] **Step 2: Run it to verify RED**

Run `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py::test_page_placement_issues_hide_only_disabled_defaults_on_off_saturday -v`.

Expected: FAIL with `AttributeError` because `_page_placement_issues_for_day` does not exist.

- [ ] **Step 3: Add failing weekday and working-Saturday retention coverage**

Add directly after Step 1's test:

```python
@pytest.mark.parametrize(
    ("day", "work_weekdays"),
    [
        (date(2026, 7, 17), frozenset({0, 1, 2, 3, 4})),
        (date(2026, 7, 18), frozenset({0, 1, 2, 3, 4, 5})),
    ],
)
def test_page_placement_issues_keep_disabled_defaults_on_working_days(
    day, work_weekdays,
):
    from zira_dashboard.routes import staffing as staffing_route

    issue = schedule_solver.PlacementIssue(
        code="exact_default_center_disabled", person="Ana", centers=("Repair 1",),
        message="Ana's default work center Repair 1 is not enabled.",
    )

    assert staffing_route._page_placement_issues_for_day(
        day, work_weekdays, (issue,),
    ) == (issue,)
```

- [ ] **Step 4: Verify all new tests are RED**

Run `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py -k page_placement_issues -v`.

Expected: all three cases FAIL only because the helper is undefined.

- [ ] **Step 5: Implement the pure filter and page-context wiring**

In `src/zira_dashboard/routes/staffing.py`, directly above `_recycled_context_for_day`, add:

```python
def _page_placement_issues_for_day(
    d: date,
    work_weekdays: frozenset[int],
    issues: tuple[schedule_solver.PlacementIssue, ...],
) -> tuple[schedule_solver.PlacementIssue, ...]:
    """Return page-visible placement issues for the selected Staffing day."""
    if d.weekday() != 5 or 5 in work_weekdays:
        return tuple(issues)
    return tuple(
        issue for issue in issues
        if issue.code != "exact_default_center_disabled"
    )
```

Add required keyword-only argument `work_weekdays: frozenset[int]` to `_recycled_context_for_day`. Replace its existing `page_placement_issues = tuple(...)` assignment with:

```python
page_placement_issues = tuple(
    issue for issue in suggestion.placement_issues
    if issue.code not in action_only_codes
)
page_placement_issues = _page_placement_issues_for_day(
    d, work_weekdays, page_placement_issues,
)
```

Immediately before the `recycled_ctx = _recycled_context_for_day(...)` call in `staffing_page`, add:

```python
work_weekdays = (
    schedule_store.current().work_weekdays or frozenset({0, 1, 2, 3, 4})
)
```

Pass `work_weekdays=work_weekdays` to that call. Update every direct test call to `_recycled_context_for_day` with `work_weekdays=frozenset({0, 1, 2, 3, 4})`, except tests intentionally checking working Saturday behavior.

- [ ] **Step 6: Verify GREEN and regressions**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py -k 'page_placement_issues or recycled_context' -v
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py tests/test_rotation_suggestions.py -q
ZIRA_API_KEY=test .venv/bin/python -m ruff check src/zira_dashboard/routes/staffing.py tests/test_staffing_rotations.py
```

Expected: focused and regression tests pass, and Ruff reports no violations.

- [ ] **Step 7: Commit the implementation**

```bash
git add src/zira_dashboard/routes/staffing.py tests/test_staffing_rotations.py
git commit -m "fix: hide default-center warnings on off Saturdays"
```

## Plan Self-Review

- **Spec coverage:** Task 1 filters only `exact_default_center_disabled` in passive page context, retains unrelated warnings, and tests both weekday and configured-working-Saturday behavior. Solver and rebuild code are untouched.
- **Placeholder scan:** No TBDs, deferred work, or unspecified assertions remain.
- **Type consistency:** The helper accepts a `date`, configured weekday set, and `PlacementIssue` tuple; the Staffing page supplies the defaulted schedule configuration and the page-only issue tuple.
