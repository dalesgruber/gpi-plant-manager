# Disabled Work Centers Return People Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return people assigned to a work center to the correct scheduler rail when that center is turned off.

**Architecture:** The auto-center endpoint will receive the specific center being switched off, remove that center's assignments from the current schedule draft, and save the changed draft in the same database transaction as the enabled-center setting. It returns the updated assignment map, which the browser uses to reconcile pickers before synchronizing the left rail.

**Tech Stack:** FastAPI, Postgres/psycopg2, vanilla JavaScript, pytest.

## Global Constraints

- Apply removals only to centers named in the explicit `turn_off` payload.
- Preserve assignments at all other centers, including manual assignments.
- Persist schedule and enabled-center state in one transaction.
- Do not auto-assign people when a center is turned on or trigger a rebuild.
- On Saturday, committed volunteers return to Unassigned and non-volunteers remain Off.

---

## File structure

- `src/zira_dashboard/staffing.py` — make the existing schedule writer usable in an enclosing transaction.
- `src/zira_dashboard/routes/rotations.py` — atomically clear explicit off-center assignments and return the map.
- `src/zira_dashboard/static/staffing.js` — send the switched-off name and reconcile returned assignments.
- `tests/test_staffing_rotations.py` — endpoint regression coverage.
- `tests/test_staffing_static.py` — client interaction contract.

### Task 1: Make schedule saves transaction-aware

**Files:**

- Modify: `src/zira_dashboard/staffing.py:678-740`
- Test: `tests/test_staffing_rotations.py`

**Interfaces:**

- Produces: `save_schedule(schedule: Schedule, *, cur=None) -> None`.
- When `cur` is supplied, the schedule upsert, assignment replacement, and notes replacement execute through that cursor. With no cursor, behavior remains unchanged.

- [ ] **Step 1: Write the failing endpoint setup**

Add this to the new endpoint test in Task 2:

```python
saved_schedules = []
monkeypatch.setattr(
    rotations.staffing,
    "save_schedule",
    lambda schedule, *, cur=None: saved_schedules.append((schedule, cur)),
)
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest tests/test_staffing_rotations.py::test_auto_work_centers_turning_off_a_populated_center_clears_its_draft_assignments -v`

Expected: FAIL because the endpoint does not yet save a changed non-posted draft.

- [ ] **Step 3: Extract the existing SQL writer and add the cursor argument**

Move the current `with db.cursor()` body of `save_schedule` to a private helper. Keep validation shared by both paths:

```python
def _save_schedule_with_cursor(cur, schedule: Schedule) -> None:
    assignment_sources = _validate_assignment_sources(schedule.assignment_sources)
    saturday_availability_overrides = _validate_saturday_availability_overrides(
        schedule.saturday_availability_overrides
    )
    # Move the current schedule INSERT/UPDATE, assignment DELETE/INSERT,
    # and note DELETE/INSERT statements here unchanged.


def save_schedule(schedule: Schedule, *, cur=None) -> None:
    """Persist one schedule, optionally inside an existing transaction."""
    _invalidate_schedule_cache(schedule.day)
    if cur is not None:
        _save_schedule_with_cursor(cur, schedule)
        return
    from . import db
    with db.cursor() as own_cur:
        _save_schedule_with_cursor(own_cur, schedule)
```

- [ ] **Step 4: Re-run the focused test**

Run: `pytest tests/test_staffing_rotations.py::test_auto_work_centers_turning_off_a_populated_center_clears_its_draft_assignments -v`

Expected: still FAIL for the missing endpoint behavior, not for an unsupported `cur` keyword.

- [ ] **Step 5: Commit the writer refactor**

```bash
git add src/zira_dashboard/staffing.py tests/test_staffing_rotations.py
git commit -m "refactor: allow transactional schedule saves"
```

### Task 2: Atomically remove assignments from an explicit off-center

**Files:**

- Modify: `src/zira_dashboard/routes/rotations.py:386-450`
- Modify: `tests/test_staffing_rotations.py` after `test_auto_work_centers_endpoint_removes_non_empty_turn_off_selection`

**Interfaces:**

- Consumes: `turn_off: list[str]` and the current day's `Schedule`.
- Produces: JSON `assignments: dict[str, list[str]]` equal to the persisted post-toggle draft.

- [ ] **Step 1: Add the failing route regression test**

```python
def test_auto_work_centers_turning_off_a_populated_center_clears_its_draft_assignments(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    _stub_recommendation_inputs(monkeypatch)
    schedule = staffing.Schedule(
        day=TARGET_DAY,
        assignments={"Repair 1": ["Jordan"], "Work Orders": ["Juan"]},
    )
    saved_schedules = []
    monkeypatch.setattr(rotations.staffing, "load_roster", lambda: [])
    monkeypatch.setattr(rotations.staffing, "load_schedule", lambda _day: schedule)
    monkeypatch.setattr(rotations.scheduler_time_off, "time_off_entries_for_day", lambda _day: [])
    monkeypatch.setattr(
        rotations.staffing,
        "save_schedule",
        lambda changed, *, cur=None: saved_schedules.append((changed, cur)),
    )
    monkeypatch.setattr(
        rotations.staffing_route,
        "_save_enabled_auto_work_centers",
        lambda names, *, cur=None: list(names),
    )
    monkeypatch.setattr(rotations._http_cache, "invalidate_today_cache", lambda: None)
    monkeypatch.setattr(rotations._http_cache, "invalidate_stable_cache", lambda: None)

    response = client.post("/api/rotations/auto-work-centers", json={
        "day": TARGET_DAY.isoformat(),
        "work_centers": ["Repair 1"],
        "turn_off": ["Work Orders"],
    })

    assert response.status_code == 200
    assert response.json()["assignments"] == {"Repair 1": ["Jordan"]}
    assert len(saved_schedules) == 1
    assert saved_schedules[0][0].assignments == {"Repair 1": ["Jordan"]}
    assert saved_schedules[0][1] is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_staffing_rotations.py::test_auto_work_centers_turning_off_a_populated_center_clears_its_draft_assignments -v`

Expected: FAIL because `turn_off` currently changes only the enabled-center setting.

- [ ] **Step 3: Compute a copied assignment map and save it with settings**

Import `replace` from `dataclasses`. In `save_auto_work_centers._work`, after loading the schedule, add:

```python
turn_off_names = set(staffing_route._ordered_work_center_names(turn_off))
assignments = {
    wc_name: list(people)
    for wc_name, people in sched.assignments.items()
    if wc_name not in turn_off_names
}
did_remove_assignments = assignments != sched.assignments
if did_remove_assignments:
    sched = replace(sched, assignments=assignments)
```

Use one `with db.cursor() as cur:` for both weekday and Saturday paths. Within it, retain the current Saturday `update_openings(..., cur=cur)`, then save changed draft and settings together:

```python
if did_remove_assignments or existing.published:
    staffing.save_schedule(sched, cur=cur)
enabled = staffing_route._save_enabled_auto_work_centers(enabled, cur=cur)
```

Add this successful response field:

```python
"assignments": {wc_name: list(people) for wc_name, people in sched.assignments.items()},
```

- [ ] **Step 4: Run the focused endpoint tests**

Run: `pytest tests/test_staffing_rotations.py -k "turning_off_a_populated_center or removes_non_empty_turn_off_selection or persists_posted_schedule_as_draft_first" -v`

Expected: PASS.

- [ ] **Step 5: Commit the endpoint behavior**

```bash
git add src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py
git commit -m "fix: return people when auto centers turn off"
```

### Task 3: Reconcile the changed assignment map in the browser

**Files:**

- Modify: `src/zira_dashboard/static/staffing.js:1688-1745,1841-1855`
- Modify: `tests/test_staffing_static.py` after `test_work_center_row_toggle_excludes_controls_and_rolls_back_failures`

**Interfaces:**

- Consumes: `data.assignments` from a successful auto-center response.
- Produces: picker checkboxes that equal the server map and a synchronized Unassigned/Off/Reserves rail.

- [ ] **Step 1: Add the failing static test**

```python
def test_turning_off_a_work_center_sends_it_and_reconciles_returned_assignments():
    js = _script()

    assert "async function saveAutoCenters(turnOff = []) {" in js
    assert "postAutoCenters(requestedWorkCenters, turnOff)" in js
    assert "saveAutoCenters(enabled ? [name] : []);" in js
    assert "function applyAutoCenterAssignments(assignments) {" in js
    assert "applyAutoCenterAssignments(data.assignments);" in js
    assert js.index("applyAutoCenterAssignments(data.assignments);") < js.index(
        "syncLeftRailWithSchedule();"
    )
```

- [ ] **Step 2: Run the static test to verify it fails**

Run: `pytest tests/test_staffing_static.py::test_turning_off_a_work_center_sends_it_and_reconciles_returned_assignments -v`

Expected: FAIL because the request always sends an empty `turn_off` list and ignores assignments.

- [ ] **Step 3: Add the reconciliation helper and send the actual off-center**

Add this helper inside the rotation-controls closure:

```javascript
function applyAutoCenterAssignments(assignments) {
  const wantedByCenter = assignments || {};
  workCenterRows.forEach(row => {
    const picker = row.querySelector('details.sched-dd');
    if (!picker) return;
    const wanted = new Set(wantedByCenter[row.dataset.loc] || []);
    picker.querySelectorAll('.dd-item').forEach(item => {
      const cb = item.querySelector('input[type=checkbox]');
      if (!cb) return;
      cb.checked = wanted.has(item.dataset.name);
      item.classList.toggle('selected', cb.checked);
    });
    updateDdSummary(picker);
  });
  syncLeftRailWithSchedule();
  refreshPickerVisibility();
}
```

Change the save signature and request:

```javascript
async function saveAutoCenters(turnOff = []) {
  // Retain the existing response validation and error handling.
  const resp = await postAutoCenters(requestedWorkCenters, turnOff);
  // After applyEnabledCenters(data.enabled_work_centers):
  applyAutoCenterAssignments(data.assignments);
}
```

In `toggleWorkCenterRow`, replace `saveAutoCenters();` with:

```javascript
saveAutoCenters(enabled ? [name] : []);
```

Do not call `kickAutosave()`; the endpoint has already persisted the intentional removal.

- [ ] **Step 4: Run the client regression tests**

Run: `pytest tests/test_staffing_static.py -k "turning_off_a_work_center or work_center_row_toggle or auto_center_success_requires_server_enabled_centers" -v`

Expected: PASS.

- [ ] **Step 5: Commit the client behavior**

```bash
git add src/zira_dashboard/static/staffing.js tests/test_staffing_static.py
git commit -m "fix: reconcile scheduler after center toggle"
```

### Task 4: Verify the entire change

**Files:**

- Verify: `src/zira_dashboard/staffing.py`
- Verify: `src/zira_dashboard/routes/rotations.py`
- Verify: `src/zira_dashboard/static/staffing.js`
- Verify: `tests/test_staffing_rotations.py`
- Verify: `tests/test_staffing_static.py`

- [ ] **Step 1: Run both affected test modules**

Run: `pytest tests/test_staffing_rotations.py tests/test_staffing_static.py -v`

Expected: PASS with no failures.

- [ ] **Step 2: Run the linter**

Run: `ruff check src/zira_dashboard/staffing.py src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py tests/test_staffing_static.py`

Expected: `All checks passed!`.

- [ ] **Step 3: Inspect the final change set**

Run: `git diff --check HEAD~3..HEAD && git status --short`

Expected: no whitespace errors; only implementation files and pre-existing workspace changes are present.
