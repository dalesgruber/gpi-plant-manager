# Declared Absence Clears Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove a declared-absent employee from that day’s saved work-center assignments.

**Architecture:** `staffing.py` will own one transactional operation that locks the daily schedule, filters the employee from assignments and assignment-source metadata, then persists the unchanged remainder. The late-report mutation will call it after successfully recording the local absence.

**Tech Stack:** Python, FastAPI, PostgreSQL, pytest.

## Global Constraints

- Remove the worker from every work center and `assignment_sources` entry for that date.
- Preserve all other assignments, ordering, metadata, and schedule fields.
- Undo only removes the absence; it never restores a prior assignment.
- Odoo sync stays best-effort; its failure must not prevent the local clear.
- Write and observe failing regression tests before modifying production code.

---

## File Structure

- `src/zira_dashboard/staffing.py`: transaction-safe schedule mutation beside `load_schedule_for_update`.
- `src/zira_dashboard/routes/late_report.py`: invokes the staffing mutation after `late_report.declare_absent`.
- `tests/test_staffing_schedule_metadata.py`: database-backed domain regression tests.
- `tests/test_late_report_absence_odoo.py`: route-order regression test using mocks.

### Task 1: Add a transaction-safe schedule-clearing operation

**Files:**
- Modify: `src/zira_dashboard/staffing.py:610-670`
- Test: `tests/test_staffing_schedule_metadata.py`

**Interfaces:**
- Consumes: `load_schedule_for_update(day: date, *, cur) -> Schedule | None` and `save_schedule(schedule: Schedule, *, cur=None) -> None`.
- Produces: `remove_person_from_schedule(day: date, person_name: str) -> bool`.

- [ ] **Step 1: Write the failing regression test**

```python
def test_remove_person_from_schedule_clears_assignments_and_sources():
    staffing.save_schedule(staffing.Schedule(
        day=DAY,
        assignments={
            "Repair 1": ["Jordan", "Taylor"],
            "Repair 2": ["Taylor", "Morgan"],
        },
        assignment_sources={
            "Repair 1": {"Jordan": "manual", "Taylor": "generated"},
            "Repair 2": {"Taylor": "default", "Morgan": "manual"},
        },
    ))

    changed = staffing.remove_person_from_schedule(DAY, "Taylor")

    saved = staffing.load_schedule(DAY)
    assert changed is True
    assert saved.assignments == {"Repair 1": ["Jordan"], "Repair 2": ["Morgan"]}
    assert saved.assignment_sources == {
        "Repair 1": {"Jordan": "manual"}, "Repair 2": {"Morgan": "manual"},
    }
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_staffing_schedule_metadata.py::test_remove_person_from_schedule_clears_assignments_and_sources -v`

Expected: FAIL with `AttributeError` because the function does not exist.

- [ ] **Step 3: Implement the minimal operation**

Import `replace` from `dataclasses` if needed, then add the following immediately after `load_schedule_for_update`:

```python
def remove_person_from_schedule(day: date, person_name: str) -> bool:
    """Remove one person from every saved assignment for ``day``."""
    from . import db

    with db.cursor() as cur:
        schedule = load_schedule_for_update(day, cur=cur)
        if schedule is None:
            return False
        assignments = {
            wc_name: [name for name in names if name != person_name]
            for wc_name, names in schedule.assignments.items()
        }
        if assignments == schedule.assignments:
            return False
        sources = {
            wc_name: {name: source for name, source in people.items() if name != person_name}
            for wc_name, people in schedule.assignment_sources.items()
        }
        sources = {wc_name: people for wc_name, people in sources.items() if people}
        save_schedule(replace(schedule, assignments=assignments, assignment_sources=sources), cur=cur)
    return True
```

The row lock prevents an absence declaration from overwriting a concurrent schedule edit.

- [ ] **Step 4: Verify green**

Run: `pytest tests/test_staffing_schedule_metadata.py::test_remove_person_from_schedule_clears_assignments_and_sources -v`

Expected: PASS.

- [ ] **Step 5: Add and verify the no-op boundary test**

```python
def test_remove_person_from_schedule_is_noop_when_person_is_not_assigned():
    staffing.save_schedule(staffing.Schedule(
        day=DAY,
        assignments={"Repair 1": ["Jordan"]},
        assignment_sources={"Repair 1": {"Jordan": "manual"}},
    ))

    assert staffing.remove_person_from_schedule(DAY, "Taylor") is False
    assert staffing.load_schedule(DAY).assignments == {"Repair 1": ["Jordan"]}
```

Run: `pytest tests/test_staffing_schedule_metadata.py -v`

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

Run: `git add src/zira_dashboard/staffing.py tests/test_staffing_schedule_metadata.py && git commit -m "feat: clear schedule assignments for absences"`

### Task 2: Clear persisted assignments in the absence route

**Files:**
- Modify: `src/zira_dashboard/routes/late_report.py:20-30,105-125`
- Test: `tests/test_late_report_absence_odoo.py`

**Interfaces:**
- Consumes: `staffing.remove_person_from_schedule(day: date, person_name: str) -> bool`.
- Produces: HTTP 200 declared-absence processing that has already cleared the saved assignment.

- [ ] **Step 1: Write the failing route test**

Add `staffing` to the route module import list. Then add this test:

```python
def test_declare_absent_sync_clears_saved_schedule_after_local_absence(monkeypatch):
    clear_schedule = MagicMock(return_value=True)
    declare_absent = MagicMock()
    monkeypatch.setattr(late_report_routes, "plant_today", lambda: FIXED_DAY)
    monkeypatch.setattr(
        late_report_routes.absence_sync, "create_absence_for_day",
        MagicMock(return_value={"holiday_status_id": 42, "leave_id": 777, "state": "validate"}),
    )
    monkeypatch.setattr(late_report_routes.absence_sync, "mirror_approved_absence", MagicMock())
    monkeypatch.setattr(late_report_routes.late_report, "declare_absent", declare_absent)
    monkeypatch.setattr(late_report_routes.staffing, "remove_person_from_schedule", clear_schedule)
    monkeypatch.setattr(late_report_routes.db, "execute", MagicMock())
    monkeypatch.setattr(late_report_routes.inbox_log, "log_event_safe", lambda **_kwargs: 123)
    monkeypatch.setattr(late_report_routes, "_bust_caches", lambda: None)

    response = late_report_routes._declare_absent_sync({
        "emp_id": "5", "name": "Test Person", "reason": "No call no show",
    })

    assert response.status_code == 200
    declare_absent.assert_called_once()
    clear_schedule.assert_called_once_with(FIXED_DAY, "Test Person")
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_late_report_absence_odoo.py::test_declare_absent_sync_clears_saved_schedule_after_local_absence -v`

Expected: FAIL because `late_report_routes.staffing` does not exist yet.

- [ ] **Step 3: Wire the operation after the local absence write**

Include `staffing` in the route’s existing `from .. import (...)` block. In `_declare_absent_sync`, directly after `late_report.declare_absent(...)` and before deleting late snoozes, add:

```python
        staffing.remove_person_from_schedule(today, name)
```

Do not invoke it before the local absence record succeeds, and do not add it to `_undo_absent_sync`. Keep it inside the existing `try` block: a schedule write error must return HTTP 500 rather than a false-success absence response with a hidden persisted assignment.

- [ ] **Step 4: Verify green and the affected modules**

Run: `pytest tests/test_late_report_absence_odoo.py::test_declare_absent_sync_clears_saved_schedule_after_local_absence -v`

Expected: PASS.

Run: `pytest tests/test_late_report_absence_odoo.py tests/test_staffing_schedule_metadata.py -v`

Expected: PASS with zero failures.

- [ ] **Step 5: Commit Task 2**

Run: `git add src/zira_dashboard/routes/late_report.py tests/test_late_report_absence_odoo.py && git commit -m "fix: remove absent workers from daily schedule"`

### Task 3: Final verification and delivery

**Files:**
- Verify: `src/zira_dashboard/staffing.py`
- Verify: `src/zira_dashboard/routes/late_report.py`
- Verify: `tests/test_staffing_schedule_metadata.py`
- Verify: `tests/test_late_report_absence_odoo.py`

**Interfaces:**
- Consumes: the completed Tasks 1 and 2.
- Produces: verified persisted schedule removal for declared absences.

- [ ] **Step 1: Run the exact regression suite**

Run: `pytest tests/test_late_report_absence_odoo.py tests/test_staffing_schedule_metadata.py -v`

Expected: PASS with zero failures.

- [ ] **Step 2: Review the runtime diff**

Run: `git diff origin/main...HEAD -- src/zira_dashboard/staffing.py src/zira_dashboard/routes/late_report.py tests/test_staffing_schedule_metadata.py tests/test_late_report_absence_odoo.py`

Confirm that the runtime change clears only the declared employee’s assignments and source metadata, and undo never restores an assignment.

- [ ] **Step 3: Push the implementation commits**

Run: `git push origin main`

Expected: `main -> main` succeeds.

