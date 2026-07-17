# Reset to Defaults-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Reset to defaults replace the entire assignment set with exact defaults and next group rotations, without invoking automatic scheduling.

**Architecture:** Add a deterministic default-only builder in the rotations route. The reset branch uses it before the ordinary automatic rebuild path, saves its result while preserving metadata, and returns the existing response shape. The browser keeps the existing request flag and updates its confirmation copy.

**Tech Stack:** Python 3, FastAPI, vanilla JavaScript, pytest.

## Global Constraints

- Reset removes every prior assignment, including manual and generated assignments.
- Preserve published state/snapshot, notes, work-center notes, testing-day state, custom hours, Auto/Training settings, time off, and schedule goal.
- Exact defaults place active, non-reserve, non-full-day-absent people at their configured work center.
- Group defaults use the existing deterministic per-person `choose_center` rule over group members.
- Non-defaulted people remain unassigned; automatic-scheduler completeness cannot prevent reset.
- Ordinary schedule-goal rebuilds retain current behavior.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/zira_dashboard/routes/rotations.py` | Build and persist default-only reset assignments for `reset_to_defaults`. |
| `src/zira_dashboard/static/staffing.js` | State the defaults-only reset semantics in the confirmation dialog. |
| `tests/test_staffing_rotations.py` | Exercise the reset endpoint without a database. |
| `tests/test_staffing_static.py` | Guard the request flag and confirmation wording. |

### Task 1: Add a default-only reset branch

**Files:**

- Modify: `src/zira_dashboard/routes/rotations.py:25-34,459-579`
- Test: `tests/test_staffing_rotations.py:430-590`

**Interfaces:**

- Consumes: `rotation_suggestions.choose_center(name, group, centers, history)` and `RecycledHistory`.
- Produces: `_defaults_only_assignments(*, roster, full_day_off_names, exact_defaults, group_defaults, user_group_centers, history) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]`.
- Consumed by: the `reset_to_defaults` branch of `rebuild_rotation`.

- [ ] **Step 1: Write the failing endpoint test**

Add this test after the existing reset test:

```python
def test_reset_to_defaults_replaces_assignments_with_exact_and_next_group_defaults(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    staffing_route = _stub_recommendation_inputs(monkeypatch)
    prior = staffing.Schedule(
        day=TARGET_DAY, published=True,
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
    monkeypatch.setattr(rotations.scheduler_time_off, "time_off_entries_for_day", lambda _day: [SimpleNamespace(person_name="Absent", is_full_day=True)])
    monkeypatch.setattr(staffing_route, "_default_inputs", lambda strict=False: ({"Repair 1": ("Exact",), "Truck Driver": ("Absent",)}, {"Repair": ("Rotate",)}, {"Repair": ("Repair 1", "Repair 2")}))
    monkeypatch.setattr(rotations.rotation_suggestions, "_load_recycled_history", lambda *_args, **_kwargs: rotation_suggestions.RecycledHistory(center_counts={("Rotate", "Repair 1"): 1, ("Rotate", "Repair 2"): 1}, last_center_by_person_group={("Rotate", "Repair"): "Repair 1"}))
    monkeypatch.setattr(staffing_route, "_recycled_suggestion_for_day", lambda *_args, **_kwargs: pytest.fail("reset must not run automatic scheduling"))
    monkeypatch.setattr(rotations._http_cache, "invalidate_today_cache", lambda: None)

    response = client.post("/api/rotations/rebuild", json={"day": TARGET_DAY.isoformat(), "mode": "normal", "reset_to_defaults": True})

    assert response.status_code == 200
    assert saved[0].assignments == {"Repair 1": ["Exact"], "Repair 2": ["Rotate"]}
    assert saved[0].assignment_sources == {"Repair 1": {"Exact": "default"}, "Repair 2": {"Rotate": "default"}}
    assert saved[0].notes == prior.notes
    assert saved[0].wc_notes == prior.wc_notes
    assert saved[0].testing_day is True
    assert saved[0].published_snapshot == prior.published_snapshot
    assert saved[0].custom_hours == prior.custom_hours
    assert saved[0].rotation_mode == prior.rotation_mode
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_staffing_rotations.py::test_reset_to_defaults_replaces_assignments_with_exact_and_next_group_defaults -v`

Expected: FAIL because reset calls `_recycled_suggestion_for_day`.

- [ ] **Step 3: Implement the minimal default-only builder**

Import `rotation_suggestions` in `rotations.py`. Add this helper above `rebuild_rotation`:

```python
def _defaults_only_assignments(*, roster, full_day_off_names, exact_defaults, group_defaults, user_group_centers, history):
    available = {person.name for person in roster if person.active and not person.reserve and person.name not in full_day_off_names}
    assignments, sources, assigned = {}, {}, set()

    def place(center, name):
        if name not in available or name in assigned:
            return
        assignments.setdefault(center, []).append(name)
        sources.setdefault(center, {})[name] = "default"
        assigned.add(name)

    for center, names in exact_defaults.items():
        for raw_name in names:
            place(str(center), str(raw_name).strip())
    for group, names in group_defaults.items():
        centers = tuple(sorted(set(user_group_centers.get(group, ())), key=str.lower))
        for raw_name in names:
            name = str(raw_name).strip()
            if centers and name in available and name not in assigned:
                place(rotation_suggestions.choose_center(name, str(group), centers, history), name)
    return assignments, sources
```

After reading defaults and time off in `_work`, load history with `rotation_suggestions._load_recycled_history(d, group_locations=staffing_route._auto_history_group_locations(), user_group_centers=user_group_centers)`. When `reset_to_defaults` is true, derive absent names from `_roster_minus_full_day_off`, call the helper, save `staffing.Schedule` preserving the fields in the normal save block but retaining `sched.rotation_mode`, invalidate cache, and return `ok`, `assignments`, `sources`, empty `reasons`/ `warnings`, and empty coverage/placement objects. Return before locks, capacity/minimum calculations, validation, or `_recycled_suggestion_for_day`.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_staffing_rotations.py -k "reset_to_defaults or reset_rebuild" -v`

Expected: PASS.

- [ ] **Step 5: Commit server behavior**

Run: `git add src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py && git commit -m "fix: reset schedules to defaults only"`

### Task 2: Update the reset confirmation

**Files:**

- Modify: `src/zira_dashboard/static/staffing.js:1595-1612`
- Test: `tests/test_staffing_static.py:242-253`

**Interfaces:**

- Consumes: unchanged `rebuild(currentMode(), { resetToDefaults: true })`.
- Produces: confirmation wording that matches the defaults-only server reset.

- [ ] **Step 1: Write the failing static test**

Replace the existing reset static test with:

```python
def test_reset_to_defaults_uses_default_only_endpoint_mode():
    js = _script()
    rotation = js.split("// ---------- Rotation goal", 1)[1].split("// Assignments to Do modal", 1)[0]
    reset = rotation.split("const resetScheduleBtn", 1)[1].split("modeBtns.forEach", 1)[0]
    assert "await rebuild(currentMode(), { resetToDefaults: true })" in reset
    assert "Replace every assignment with saved defaults and next group rotations?" in reset
    assert "Previous schedule will be kept" not in reset
    assert "Rebuild enabled Auto work centers" not in reset
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_staffing_static.py::test_reset_to_defaults_uses_default_only_endpoint_mode -v`

Expected: FAIL because current confirmation describes automatic rebuilding and retaining schedules.

- [ ] **Step 3: Change only the confirmation argument**

```javascript
'Replace every assignment with saved defaults and next group rotations?\n\n' +
'This removes manual and automated assignments. Notes, time off, and schedule settings stay.'
```

Keep the edit gate, request flag, and post-success UI synchronization unchanged.

- [ ] **Step 4: Run static and route tests**

Run: `pytest tests/test_staffing_static.py::test_reset_to_defaults_uses_default_only_endpoint_mode tests/test_staffing_rotations.py -k "reset_to_defaults or reset_rebuild" -v`

Expected: PASS.

- [ ] **Step 5: Commit client copy**

Run: `git add src/zira_dashboard/static/staffing.js tests/test_staffing_static.py && git commit -m "fix: describe defaults-only schedule reset"`

### Task 3: Verify the completed behavior

- [ ] **Step 1: Run all relevant tests**

Run: `pytest tests/test_staffing_rotations.py tests/test_staffing_static.py -v`

Expected: PASS with zero failures.

- [ ] **Step 2: Inspect the final diff**

Run: `git diff HEAD~2..HEAD -- src/zira_dashboard/routes/rotations.py src/zira_dashboard/static/staffing.js tests/test_staffing_rotations.py tests/test_staffing_static.py`

Expected: reset has a dedicated defaults-only path; ordinary automatic rebuild remains unchanged; client wording matches behavior.

