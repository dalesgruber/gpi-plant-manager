# Current Staffing Minimum Warning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the staffing-page minimum warnings validate the saved schedule displayed in the grid instead of an unrequested hypothetical Auto rebuild.

**Architecture:** Add a pure route-local helper that counts present, active, non-reserve, fully qualified assignees at each enabled work center. During page-context construction, suppress Auto-action-only `person_unplaced` and `center_minimum_unmet` proposal issues and replace them with the helper's current-schedule minimum issues; explicit Auto API responses remain unchanged.

**Tech Stack:** Python 3.12, FastAPI route orchestration, existing `schedule_solver.PlacementIssue`, pytest.

## Global Constraints

- An enabled center at its minimum of present, qualified assignees shows no minimum warning.
- Inactive, reserve, unknown, full-day-absent, or unqualified assignees do not count toward minimum coverage.
- Effective work-center minimum and required-skill settings remain authoritative, with static location values as the read-failure fallback.
- Auto proposal placement failures remain available after explicit Schedule Goal and On/Off actions.
- Do not change assignments, assignment sources, Auto solver decisions, publish validation, or work-center settings.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/zira_dashboard/routes/staffing.py` | Compute current displayed minimum coverage and select the correct issue source for page rendering. |
| `tests/test_staffing_rotations.py` | Prove safe-current coverage, unsafe-assignment exclusions, page-context filtering, and route wiring. |

### Task 1: Calculate current displayed minimum coverage

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:132-190`
- Test: `tests/test_staffing_rotations.py:430-470`

**Interfaces:**
- Consumes: `_current_minimum_coverage_issues(*, roster, assignments, time_off_entries, enabled_centers)`.
- Produces: `tuple[schedule_solver.PlacementIssue, ...]`, with code `center_minimum_unmet` only for enabled centers whose safe current count is below their configured minimum.

- [ ] **Step 1: Write the failing safe-coverage regression**

Add after `_stub_recommendation_inputs` in `tests/test_staffing_rotations.py`:

```python
def test_current_minimum_coverage_uses_displayed_safe_assignments(monkeypatch):
    from zira_dashboard.routes import staffing as staffing_route

    minimums = {"Loading/Jockeying": 1, "Tablets": 4}
    required = {
        "Loading/Jockeying": ("Loading", "CPUs/VDOs", "Trailer Jockeying"),
        "Tablets": ("Tablets",),
    }
    monkeypatch.setattr(
        staffing_route,
        "_effective_minimum",
        lambda loc: minimums.get(loc.name, loc.min_ops),
    )
    monkeypatch.setattr(
        staffing_route.work_centers_store,
        "required_skills",
        lambda loc: list(required.get(loc.name, staffing.required_skills_for(loc))),
    )
    roster = [
        staffing.Person(
            "Jesus Moreno",
            skills={"Loading": 1, "CPUs/VDOs": 1, "Trailer Jockeying": 1},
        ),
        *[
            staffing.Person(name, skills={"Tablets": 1})
            for name in (
                "Trent Iverson",
                "Francisco Ramirez",
                "Iban Penaloza",
                "Isidro Moctezuma",
                "Lauro Benitez",
            )
        ],
    ]

    issues = staffing_route._current_minimum_coverage_issues(
        roster=roster,
        assignments={
            "Loading/Jockeying": ["Jesus Moreno"],
            "Tablets": [
                "Trent Iverson",
                "Francisco Ramirez",
                "Iban Penaloza",
                "Isidro Moctezuma",
                "Lauro Benitez",
            ],
        },
        time_off_entries=[],
        enabled_centers={"Loading/Jockeying", "Tablets"},
    )

    assert issues == ()
```

- [ ] **Step 2: Run the safe-coverage regression to verify RED**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest \
  tests/test_staffing_rotations.py::test_current_minimum_coverage_uses_displayed_safe_assignments -q
```

Expected: FAIL with `AttributeError` because `_current_minimum_coverage_issues` does not exist.

- [ ] **Step 3: Add the failing unsafe-assignment regression**

Add beside the safe-coverage test:

```python
def test_current_minimum_coverage_excludes_people_who_cannot_cover(monkeypatch):
    from zira_dashboard.routes import staffing as staffing_route

    monkeypatch.setattr(
        staffing_route,
        "_effective_minimum",
        lambda loc: 5 if loc.name == "Repair 1" else loc.min_ops,
    )
    monkeypatch.setattr(
        staffing_route.work_centers_store,
        "required_skills",
        lambda loc: ["Repair"] if loc.name == "Repair 1" else list(
            staffing.required_skills_for(loc)
        ),
    )
    roster = [
        staffing.Person("Qualified", skills={"Repair": 1}),
        staffing.Person("Inactive", active=False, skills={"Repair": 3}),
        staffing.Person("Reserve", reserve=True, skills={"Repair": 3}),
        staffing.Person("Unqualified", skills={"Repair": 0}),
        staffing.Person("Absent", skills={"Repair": 3}),
    ]

    issues = staffing_route._current_minimum_coverage_issues(
        roster=roster,
        assignments={
            "Repair 1": [
                "Qualified", "Inactive", "Reserve", "Unqualified", "Absent", "Unknown",
            ],
        },
        time_off_entries=[{"name": "Absent", "hours": None}],
        enabled_centers={"Repair 1"},
    )

    assert len(issues) == 1
    assert issues[0].code == "center_minimum_unmet"
    assert issues[0].centers == ("Repair 1",)
    assert issues[0].message == (
        "Repair 1 is below its minimum staffing level: "
        "1 qualified and present, minimum 5."
    )
```

- [ ] **Step 4: Run both regressions to verify RED**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest \
  tests/test_staffing_rotations.py::test_current_minimum_coverage_uses_displayed_safe_assignments \
  tests/test_staffing_rotations.py::test_current_minimum_coverage_excludes_people_who_cannot_cover -q
```

Expected: both tests FAIL because the helper does not exist.

- [ ] **Step 5: Implement the pure current-coverage helper**

Add after `_effective_minimum` in `src/zira_dashboard/routes/staffing.py`:

```python
def _current_minimum_coverage_issues(
    *, roster, assignments, time_off_entries, enabled_centers,
) -> tuple[schedule_solver.PlacementIssue, ...]:
    """Report minimum shortages in the saved schedule currently on screen."""
    enabled = set(_ordered_work_center_names(enabled_centers))
    absent = rotation_suggestions._full_day_time_off_names(time_off_entries or [])
    by_name = {person.name: person for person in roster}
    issues = []
    for loc in staffing.LOCATIONS:
        if loc.name not in enabled:
            continue
        try:
            minimum = max(0, _effective_minimum(loc))
        except Exception:
            minimum = max(0, int(loc.min_ops))
        try:
            required = tuple(work_centers_store.required_skills(loc))
        except Exception:
            required = staffing.required_skills_for(loc)
        safe_names = {
            name
            for name in (assignments or {}).get(loc.name, ())
            if (
                (person := by_name.get(name)) is not None
                and person.active
                and not person.reserve
                and name not in absent
                and all(person.level(skill) >= 1 for skill in required)
            )
        }
        if len(safe_names) < minimum:
            issues.append(schedule_solver.PlacementIssue(
                code="center_minimum_unmet",
                centers=(loc.name,),
                message=(
                    f"{loc.name} is below its minimum staffing level: "
                    f"{len(safe_names)} qualified and present, minimum {minimum}."
                ),
            ))
    return tuple(issues)
```

Add `schedule_solver` to the existing import from `..` at the top of the route:

```python
from .. import _http_cache, app_settings, attendance, auto_schedule_capacity, db, late_report, rotation_store, rotation_suggestions, rotation_training, schedule_solver, schedule_store, shift_config, staffing, staffing_view, time_format, work_centers_store
```

- [ ] **Step 6: Run the focused helper tests to verify GREEN**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest \
  tests/test_staffing_rotations.py::test_current_minimum_coverage_uses_displayed_safe_assignments \
  tests/test_staffing_rotations.py::test_current_minimum_coverage_excludes_people_who_cannot_cover -q
```

Expected: `2 passed`.

- [ ] **Step 7: Commit the helper and tests**

```bash
git add src/zira_dashboard/routes/staffing.py tests/test_staffing_rotations.py
git commit -m "fix: calculate displayed minimum coverage"
```

### Task 2: Replace hypothetical page shortages with current shortages

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:606-685,987-1003`
- Test: `tests/test_staffing_rotations.py:1010-1075,2280-2330`

**Interfaces:**
- Consumes: `_recycled_context_for_day(..., current_assignments=None)` and the helper from Task 1.
- Produces: page context where Auto-action-only proposal issues are omitted and current-schedule minimum issues are included.
- Preserves: explicit Auto API response payloads in `src/zira_dashboard/routes/rotations.py` without modification.

- [ ] **Step 1: Write the failing page-context regression**

Add near `test_recycled_context_reports_invalid_minimum_above_maximum`:

```python
def test_recycled_context_uses_current_staffing_instead_of_auto_preview_shortage(
    monkeypatch,
):
    staffing_route = _stub_recommendation_inputs(monkeypatch)
    preview_message = "Repair 1 is below its minimum Auto staffing level."
    monkeypatch.setattr(
        staffing_route,
        "_auto_group_maps",
        lambda _enabled: ({"Repair": ("Repair 1",)}, {"Repair": ("Repair",)}),
    )
    monkeypatch.setattr(
        staffing_route.work_centers_store,
        "required_skills",
        lambda loc: ["Repair"] if loc.name == "Repair 1" else list(
            staffing.required_skills_for(loc)
        ),
    )
    monkeypatch.setattr(
        rotation_suggestions,
        "suggest_recycled_assignments",
        lambda **_kwargs: rotation_suggestions.RecycledSuggestion(
            assignments={},
            sources={},
            reasons={},
            warnings=(preview_message, "Keep this training warning."),
            group_locations={"Repair": ("Repair 1",)},
            placement_issues=(
                schedule_solver.PlacementIssue(
                    code="center_minimum_unmet",
                    centers=("Repair 1",),
                    message=preview_message,
                ),
                schedule_solver.PlacementIssue(
                    code="person_unplaced",
                    person="Preview Person",
                    message=(
                        "Preview Person could not be placed in an enabled Auto work center."
                    ),
                ),
            ),
        ),
    )

    context = staffing_route._recycled_context_for_day(
        TARGET_DAY,
        roster=[_person("Qualified", 3)],
        mode="normal",
        base_assignments={},
        locked_assignments={},
        time_off_entries=[],
        enabled_work_centers={"Repair 1"},
        assignment_sources={},
        current_assignments={"Repair 1": ["Qualified"]},
    )

    assert context["rotation_issues"] == []
    assert context["rotation_warnings"] == ["Keep this training warning."]
```

- [ ] **Step 2: Write the failing route-wiring regression**

Extend `test_staffing_context_does_not_treat_exact_default_as_duplicate_lock` with:

```python
    assert captured["current_assignments"] == {
        "Repair 2": ["Default Green"],
    }
```

Also strengthen the existing explicit-action characterization in
`test_rebuild_applies_safe_partial_assignments_and_reports_unplaced`:

```python
    assert body["placement"]["issues"][0]["code"] == (
        "person_no_enabled_qualified_center"
    )
```

This assertion passes before the production change and protects the separate
Auto-action response contract while the page-rendering path changes.

- [ ] **Step 3: Run both page regressions to verify RED**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest \
  tests/test_staffing_rotations.py::test_recycled_context_uses_current_staffing_instead_of_auto_preview_shortage \
  tests/test_staffing_rotations.py::test_staffing_context_does_not_treat_exact_default_as_duplicate_lock -q
```

Expected: the first test FAILS because `current_assignments` is not accepted; the second FAILS because the route does not pass it.

- [ ] **Step 4: Integrate current assignments and filter Auto-action-only issues**

Extend `_recycled_context_for_day`:

```python
def _recycled_context_for_day(
    d: date, roster, mode: str, base_assignments, locked_assignments, time_off_entries,
    enabled_work_centers=None, assignment_sources=None, current_assignments=None,
):
```

Replace the warning/issue assignment after `suggestion` is built with:

```python
        action_only_codes = {"person_unplaced", "center_minimum_unmet"}
        action_only_messages = {
            issue.message
            for issue in suggestion.placement_issues
            if issue.code in action_only_codes
        }
        current_minimum_issues = (
            _current_minimum_coverage_issues(
                roster=roster,
                assignments=current_assignments,
                time_off_entries=time_off_entries,
                enabled_centers=enabled,
            )
            if current_assignments is not None
            else ()
        )
        page_placement_issues = tuple(
            issue
            for issue in suggestion.placement_issues
            if issue.code not in action_only_codes
        )
        ctx["rotation_warnings"] = [
            warning
            for warning in suggestion.warnings
            if warning not in action_only_messages
        ]
        ctx["rotation_issues"] = [
            issue.to_dict()
            for issue in (
                *suggestion.issues,
                *page_placement_issues,
                *current_minimum_issues,
            )
        ]
```

Leave `rotation_reasons`, `rotation_reason_codes`, and training-block context unchanged.

In `staffing_page`, pass the displayed schedule into the existing context call:

```python
        current_assignments=sched.assignments,
```

- [ ] **Step 5: Run the page regressions to verify GREEN**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest \
  tests/test_staffing_rotations.py::test_recycled_context_uses_current_staffing_instead_of_auto_preview_shortage \
  tests/test_staffing_rotations.py::test_staffing_context_does_not_treat_exact_default_as_duplicate_lock -q
```

Expected: `2 passed`.

- [ ] **Step 6: Verify current configuration and explicit Auto diagnostics remain intact**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest \
  tests/test_staffing_rotations.py::test_recycled_context_reports_invalid_minimum_above_maximum \
  tests/test_staffing_rotations.py::test_rebuild_applies_safe_partial_assignments_and_reports_unplaced -q
```

Expected: `2 passed`; the page still reports invalid configuration and the explicit rebuild still reports its unplaced person.

- [ ] **Step 7: Run the focused regression suites**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest \
  tests/test_staffing_rotations.py tests/test_rotation_suggestions.py -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 8: Run the repository test command**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 9: Review the final diff**

Run:

```bash
git diff --check
git diff -- src/zira_dashboard/routes/staffing.py tests/test_staffing_rotations.py
git status --short
```

Expected: no whitespace errors; only the intended staffing route and regression tests are modified, alongside the user's pre-existing untracked files.

- [ ] **Step 10: Commit the integrated fix**

```bash
git add src/zira_dashboard/routes/staffing.py tests/test_staffing_rotations.py
git commit -m "fix: show current staffing minimum warnings"
```
