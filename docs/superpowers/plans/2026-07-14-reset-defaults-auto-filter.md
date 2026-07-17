# Defaults-Only Schedule Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Reset to defaults clear the schedule and restore only exact defaults plus group defaults rotated through Auto-enabled group members, leaving automatic completion to an explicit schedule-goal action.

**Architecture:** Add a small, deterministic defaults-only builder in the rotations route. The `reset_to_defaults` branch will load defaults, absence data, Auto-enabled centers, and rotation history, save the replacement schedule immediately, and return before the automatic-solver path. The existing Optimized, Normal, and Training paths remain unchanged and can subsequently complete the remaining schedule.

**Tech Stack:** Python 3, FastAPI, vanilla JavaScript, pytest.

## Global Constraints

- Reset removes all existing assignments and assignment sources, including manual and generated entries.
- Reset preserves published state and snapshot, notes, work-center notes, testing-day state, custom hours, time-off records, and the current rotation mode.
- Exact defaults place active, non-reserve people who are not on full-day time off at their named center, even when that center is not enabled for Auto.
- Group defaults place active, non-reserve people who are not on full-day time off at a member center only when that member center is enabled for Auto.
- Group default placement uses `rotation_suggestions.choose_center` with bounded history. A group with no Auto-enabled member leaves its default person unassigned without failing the reset.
- Reset does not call the automatic solver and is not blocked by qualifications, capacity, minimum staffing, training, or complete-schedule validation.
- Reset-created sources are `default`; normal goal-based rebuild behavior stays unchanged.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/zira_dashboard/routes/rotations.py` | Build, save, and serialize the dedicated defaults-only reset result. |
| `tests/test_staffing_rotations.py` | Verify reset replacement, Auto-filtered group rotation, metadata preservation, and solver separation. |
| `tests/test_staffing_static.py` | Retain the client contract: Reset sends the dedicated request and explains defaults/group rotation. |

### Task 1: Specify the dedicated reset behavior with failing route tests

**Files:**

- Modify: `tests/test_staffing_rotations.py:552-681`

**Interfaces:**

- Consumes: `POST /api/rotations/rebuild` body `{day, mode, reset_to_defaults: true}`.
- Produces: a successful response containing `assignments`, `sources`, `enabled_work_centers`, and empty placement/coverage issue lists; exactly one persisted replacement `staffing.Schedule`.

- [ ] **Step 1: Replace the current reset test that expects manual work-center assignments to survive**

```python
def test_reset_to_defaults_replaces_assignments_and_never_runs_auto_solver(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    staffing_route = _stub_recommendation_inputs(monkeypatch)
    prior = staffing.Schedule(
        day=TARGET_DAY,
        assignments={"Repair 1": ["Old Auto"], "Truck Driver": ["Manual Driver"]},
        assignment_sources={"Repair 1": {"Old Auto": "generated"}, "Truck Driver": {"Manual Driver": "manual"}},
        notes="keep", wc_notes={"Repair 1": "keep"}, testing_day=True,
        published_snapshot={"assignments": {"Repair 1": ["Old Auto"]}},
        custom_hours={"start": "06:00", "end": "14:30", "breaks": []}, rotation_mode="training",
    )
    saved = []
    monkeypatch.setattr(rotations.staffing, "load_schedule", lambda _day: prior)
    monkeypatch.setattr(rotations.staffing, "save_schedule", saved.append)
    monkeypatch.setattr(rotations.staffing, "load_roster", lambda: [_person("Exact", 1), _person("Rotate", 1), _person("Absent", 1)])
    monkeypatch.setattr(rotations.scheduler_time_off, "time_off_entries_for_day", lambda _day: [{"name": "Absent", "hours": None}])
    monkeypatch.setattr(staffing_route, "_enabled_auto_work_centers", lambda _day: {"Repair 2"})
    monkeypatch.setattr(staffing_route, "_default_inputs", lambda strict=False: ({"Truck Driver": ("Exact",), "Repair 1": ("Absent",)}, {"Repair": ("Rotate",)}, {"Repair": ("Repair 1", "Repair 2")}))
    monkeypatch.setattr(rotations.rotation_suggestions, "_load_recycled_history", lambda *_args, **_kwargs: rotation_suggestions.RecycledHistory())
    monkeypatch.setattr(staffing_route, "_recycled_suggestion_for_day", lambda *_args, **_kwargs: pytest.fail("reset must not run automatic scheduling"))
    monkeypatch.setattr(rotations._http_cache, "invalidate_today_cache", lambda: None)

    response = client.post("/api/rotations/rebuild", json={"day": TARGET_DAY.isoformat(), "mode": "normal", "reset_to_defaults": True})

    assert response.status_code == 200
    assert saved[0].assignments == {"Truck Driver": ["Exact"], "Repair 2": ["Rotate"]}
    assert saved[0].assignment_sources == {"Truck Driver": {"Exact": "default"}, "Repair 2": {"Rotate": "default"}}
    assert saved[0].notes == prior.notes
    assert saved[0].wc_notes == prior.wc_notes
    assert saved[0].testing_day is True
    assert saved[0].published_snapshot == prior.published_snapshot
    assert saved[0].custom_hours == prior.custom_hours
    assert saved[0].rotation_mode == prior.rotation_mode
```

- [ ] **Step 2: Add the no-enabled-group-member case**

```python
def test_reset_to_defaults_skips_group_default_without_enabled_member(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    staffing_route = _stub_recommendation_inputs(monkeypatch)
    saved = []
    monkeypatch.setattr(rotations.staffing, "load_schedule", lambda _day: staffing.Schedule(day=TARGET_DAY))
    monkeypatch.setattr(rotations.staffing, "save_schedule", saved.append)
    monkeypatch.setattr(rotations.staffing, "load_roster", lambda: [_person("Rotate", 1)])
    monkeypatch.setattr(rotations.scheduler_time_off, "time_off_entries_for_day", lambda _day: [])
    monkeypatch.setattr(staffing_route, "_enabled_auto_work_centers", lambda _day: {"Junior #1"})
    monkeypatch.setattr(staffing_route, "_default_inputs", lambda strict=False: ({}, {"Repair": ("Rotate",)}, {"Repair": ("Repair 1", "Repair 2")}))
    monkeypatch.setattr(rotations.rotation_suggestions, "_load_recycled_history", lambda *_args, **_kwargs: rotation_suggestions.RecycledHistory())
    monkeypatch.setattr(rotations._http_cache, "invalidate_today_cache", lambda: None)

    response = client.post("/api/rotations/rebuild", json={"day": TARGET_DAY.isoformat(), "mode": "normal", "reset_to_defaults": True})

    assert response.status_code == 200
    assert saved[0].assignments == {}
    assert saved[0].assignment_sources == {}
```

- [ ] **Step 3: Run the focused tests to establish the failure**

Run: `pytest tests/test_staffing_rotations.py -k "reset_to_defaults" -v`

Expected: FAIL because the route currently enters `_recycled_suggestion_for_day`, merges generated results with old assignments, and labels reset assignments as `generated`.

### Task 2: Implement the defaults-only builder and early route branch

**Files:**

- Modify: `src/zira_dashboard/routes/rotations.py:20-31,459-579`
- Test: `tests/test_staffing_rotations.py:552-681`

**Interfaces:**

- Consumes: `rotation_suggestions._full_day_time_off_names`, `rotation_suggestions._load_recycled_history`, `rotation_suggestions.choose_center`, `staffing_route._default_inputs`, and `staffing_route._enabled_auto_work_centers`.
- Produces: `_defaults_only_assignments(...) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]`.
- Consumed by: `rebuild_rotation` only when `reset_to_defaults` is true.

- [ ] **Step 1: Import the module used for absence and center-selection helpers**

```python
from .. import (
    _http_cache,
    db,
    rotation_store,
    rotation_suggestions,
    schedule_solver,
    scheduler_time_off,
    staffing,
)
```

- [ ] **Step 2: Add the deterministic assignment builder above `rebuild_rotation`**

```python
def _defaults_only_assignments(
    *, roster, full_day_off_names, exact_defaults, group_defaults,
    user_group_centers, enabled_centers, history,
) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]:
    available = {
        person.name for person in roster
        if person.active and not person.reserve and person.name not in full_day_off_names
    }
    assignments: dict[str, list[str]] = {}
    sources: dict[str, dict[str, str]] = {}
    assigned: set[str] = set()

    def place(center: str, name: str) -> None:
        if not center or name not in available or name in assigned:
            return
        assignments.setdefault(center, []).append(name)
        sources.setdefault(center, {})[name] = "default"
        assigned.add(name)

    for center, names in exact_defaults.items():
        for raw_name in names:
            place(str(center).strip(), str(raw_name).strip())

    enabled = set(enabled_centers)
    for group, names in group_defaults.items():
        candidates = tuple(
            center for center in user_group_centers.get(group, ())
            if center in enabled
        )
        for raw_name in names:
            name = str(raw_name).strip()
            if candidates and name in available and name not in assigned:
                place(rotation_suggestions.choose_center(name, str(group), candidates, history), name)
    return assignments, sources
```

- [ ] **Step 3: Branch immediately after loading the reset inputs**

```python
if reset_to_defaults:
    absent = rotation_suggestions._full_day_time_off_names(time_off)
    history = rotation_suggestions._load_recycled_history(
        d,
        group_locations=staffing_route._auto_history_group_locations(),
        user_group_centers=user_group_centers,
    )
    assignments, sources = _defaults_only_assignments(
        roster=roster,
        full_day_off_names=absent,
        exact_defaults=exact_defaults,
        group_defaults=group_defaults,
        user_group_centers=user_group_centers,
        enabled_centers=enabled_centers,
        history=history,
    )
    staffing.save_schedule(staffing.Schedule(
        day=d, published=sched.published, assignments=assignments,
        notes=sched.notes, wc_notes=dict(sched.wc_notes), testing_day=sched.testing_day,
        published_snapshot=sched.published_snapshot, custom_hours=sched.custom_hours,
        rotation_mode=sched.rotation_mode, assignment_sources=sources,
    ))
    _http_cache.invalidate_today_cache()
    return JSONResponse({
        "ok": True, "applied": True, "assignments": assignments, "sources": sources,
        "reasons": {}, "warnings": [], "unplaced": [],
        "coverage": {"staffed_centers": [], "unresolved_centers": [], "issues": []},
        "enabled_work_centers": enabled_centers,
        "placement": {"available_people": [], "placed_people": [], "unplaced_people": [], "defaults": {}, "issues": []},
    })
```

Place that branch after `time_off`, `exact_defaults`, `group_defaults`, `user_group_centers`, and `enabled_centers` are loaded, but before the empty-enabled-centers error, lock construction, capacity/minimum reads, automatic suggestion, merge, and complete-schedule validation. Thus reset succeeds even with zero Auto-enabled centers; exact defaults still apply and group defaults safely skip.

- [ ] **Step 4: Run the reset tests**

Run: `pytest tests/test_staffing_rotations.py -k "reset_to_defaults or reset_rebuild" -v`

Expected: PASS.

- [ ] **Step 5: Commit the server behavior**

Run: `git add src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py && git commit -m "fix: reset schedules to defaults only"`

### Task 3: Retain the client contract and guard normal auto completion

**Files:**

- Modify: `tests/test_staffing_static.py:242-267`
- Test: `tests/test_staffing_static.py:242-267`

**Interfaces:**

- Consumes: `rebuild(currentMode(), { resetToDefaults: true })` in `src/zira_dashboard/static/staffing.js`.
- Produces: test coverage proving Reset remains a separate action and its confirmation promises defaults and group rotation, rather than automatic completion.

- [ ] **Step 1: Add the client contract assertion**

```python
def test_reset_to_defaults_uses_default_only_endpoint_mode():
    js = _script()
    rotation = js.split("// ---------- Rotation goal", 1)[1].split("// Assignments to Do modal", 1)[0]
    reset = rotation.split("const resetScheduleBtn", 1)[1].split("modeBtns.forEach", 1)[0]
    assert "await rebuild(currentMode(), { resetToDefaults: true })" in reset
    assert "Replace every assignment with saved defaults and next group rotations?" in reset
    assert "This removes manual and automated assignments." in reset
```

- [ ] **Step 2: Run the static contract test**

Run: `pytest tests/test_staffing_static.py::test_reset_to_defaults_uses_default_only_endpoint_mode -v`

Expected: PASS. The current confirmation already accurately describes the approved reset behavior, so do not change `staffing.js` unless this assertion reveals a regression.

- [ ] **Step 3: Run ordinary rebuild tests to prove automatic completion is unchanged**

Run: `pytest tests/test_staffing_rotations.py -k "rebuild and not reset" -v`

Expected: PASS.

- [ ] **Step 4: Commit the test-only contract guard if it changed**

Run: `git add tests/test_staffing_static.py && git commit -m "test: guard defaults-only reset contract"`

### Task 4: Final verification

- [ ] **Step 1: Run the complete relevant suite**

Run: `pytest tests/test_staffing_rotations.py tests/test_staffing_static.py -v`

Expected: PASS with zero failures.

- [ ] **Step 2: Inspect the implementation diff**

Run: `git diff HEAD~2..HEAD -- src/zira_dashboard/routes/rotations.py src/zira_dashboard/static/staffing.js tests/test_staffing_rotations.py tests/test_staffing_static.py`

Expected: Reset has a dedicated default-only path; exact defaults bypass the Auto toggle; group defaults use only Auto-enabled members; normal schedule-goal rebuild logic is unchanged.
