# Reset Default-Group Balancing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spread people in a reset default group evenly over that group's enabled Auto work centers without moving exact work-center defaults.

**Architecture:** The reset-only builder places exact defaults first. It receives configured center capacities, then assigns group-default people to the least-loaded eligible center; exact defaults and earlier placements count toward load. Existing per-person history resolves equally loaded choices so rotation remains fair.

**Tech Stack:** Python 3, FastAPI, pytest.

## Global Constraints

- Exact work-center defaults remain pinned to their saved work center.
- Eligible group targets must be enabled Auto centers in that configured group.
- Inactive, reserve, absent, and duplicate people remain excluded.
- Configured maxima are hard limits for group-default placement.
- History resolves equal-load choices; center name resolves any remaining tie.
- Ordinary automatic rebuild and browser behavior are unchanged.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/zira_dashboard/routes/rotations.py` | Capacity-aware reset default builder. |
| `tests/test_staffing_rotations.py` | Reset distribution and capacity regression tests. |

### Task 1: Balance group defaults in the reset builder

**Files:**

- Modify: `src/zira_dashboard/routes/rotations.py:459-500,526-580`
- Test: `tests/test_staffing_rotations.py:630-705`

**Interfaces:**

- Consumes: `staffing_route._configured_center_capacities(centers, strict=True) -> dict[str, int | None]`.
- Consumes: `rotation_suggestions.choose_center(name, group, centers, history) -> str`.
- Produces: `_defaults_only_assignments(..., center_capacities: Mapping[str, int | None], ...) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]`.

- [ ] **Step 1: Write the failing balanced-placement test**

Add this after `test_reset_to_defaults_replaces_assignments_and_never_runs_auto_solver`:

```python
def test_reset_to_defaults_spreads_group_people_across_enabled_auto_centers(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    staffing_route = _stub_recommendation_inputs(monkeypatch)
    saved = []
    monkeypatch.setattr(rotations.staffing, "load_schedule", lambda _day: staffing.Schedule(day=TARGET_DAY))
    monkeypatch.setattr(rotations.staffing, "save_schedule", saved.append)
    monkeypatch.setattr(rotations.staffing, "load_roster", lambda: [_person("Ana", 1), _person("Bob", 1), _person("Cara", 1)])
    monkeypatch.setattr(rotations.scheduler_time_off, "time_off_entries_for_day", lambda _day: [])
    monkeypatch.setattr(staffing_route, "_enabled_auto_work_centers", lambda _day: {"Repair 1", "Repair 2", "Repair 3"})
    monkeypatch.setattr(staffing_route, "_default_inputs", lambda strict=False: ({}, {"Repair": ("Ana", "Bob", "Cara")}, {"Repair": ("Repair 1", "Repair 2", "Repair 3")}))
    monkeypatch.setattr(staffing_route, "_configured_center_capacities", lambda centers, strict=False: {center: 1 for center in centers})
    monkeypatch.setattr(rotations.rotation_suggestions, "_load_recycled_history", lambda *_args, **_kwargs: rotation_suggestions.RecycledHistory())
    monkeypatch.setattr(rotations._http_cache, "invalidate_today_cache", lambda: None)

    response = client.post("/api/rotations/rebuild", json={"day": TARGET_DAY.isoformat(), "mode": "normal", "reset_to_defaults": True})

    assert response.status_code == 200
    assert saved[0].assignments == {"Repair 1": ["Ana"], "Repair 2": ["Bob"], "Repair 3": ["Cara"]}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_staffing_rotations.py::test_reset_to_defaults_spreads_group_people_across_enabled_auto_centers -v`

Expected: FAIL because empty history selects Repair 1 for every person and the current builder does not update group-center load.

- [ ] **Step 3: Write the failing exact-default capacity test**

Add this immediately after the previous test:

```python
def test_reset_to_defaults_counts_exact_defaults_and_skips_full_group_centers(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    staffing_route = _stub_recommendation_inputs(monkeypatch)
    saved = []
    monkeypatch.setattr(rotations.staffing, "load_schedule", lambda _day: staffing.Schedule(day=TARGET_DAY))
    monkeypatch.setattr(rotations.staffing, "save_schedule", saved.append)
    monkeypatch.setattr(rotations.staffing, "load_roster", lambda: [_person("Pinned", 1), _person("Ana", 1), _person("Bob", 1)])
    monkeypatch.setattr(rotations.scheduler_time_off, "time_off_entries_for_day", lambda _day: [])
    monkeypatch.setattr(staffing_route, "_enabled_auto_work_centers", lambda _day: {"Repair 1", "Repair 2"})
    monkeypatch.setattr(staffing_route, "_default_inputs", lambda strict=False: ({"Repair 1": ("Pinned",)}, {"Repair": ("Ana", "Bob")}, {"Repair": ("Repair 1", "Repair 2")}))
    monkeypatch.setattr(staffing_route, "_configured_center_capacities", lambda centers, strict=False: {"Repair 1": 1, "Repair 2": 1})
    monkeypatch.setattr(rotations.rotation_suggestions, "_load_recycled_history", lambda *_args, **_kwargs: rotation_suggestions.RecycledHistory())
    monkeypatch.setattr(rotations._http_cache, "invalidate_today_cache", lambda: None)

    response = client.post("/api/rotations/rebuild", json={"day": TARGET_DAY.isoformat(), "mode": "normal", "reset_to_defaults": True})

    assert response.status_code == 200
    assert saved[0].assignments == {"Repair 1": ["Pinned"], "Repair 2": ["Ana"]}
    assert "Bob" not in {name for names in saved[0].assignments.values() for name in names}
```

- [ ] **Step 4: Run both tests to verify they fail**

Run: `pytest tests/test_staffing_rotations.py -k "spreads_group_people or counts_exact_defaults" -v`

Expected: FAIL because the builder neither filters full centers nor accounts for placements made during this reset.

- [ ] **Step 5: Implement the capacity-aware selector**

Add `center_capacities` to `_defaults_only_assignments` and replace its group-default loop with:

```python
enabled = set(enabled_centers)
for group, names in group_defaults.items():
    group_centers = tuple(center for center in user_group_centers.get(group, ()) if center in enabled)
    for raw_name in names:
        name = str(raw_name).strip()
        available_centers = tuple(
            center for center in group_centers
            if center_capacities.get(center) is None
            or len(assignments.get(center, ())) < center_capacities[center]
        )
        if not available_centers or name not in available or name in assigned:
            continue
        least_load = min(len(assignments.get(center, ())) for center in available_centers)
        tied_centers = tuple(center for center in available_centers if len(assignments.get(center, ())) == least_load)
        place(rotation_suggestions.choose_center(name, str(group), tied_centers, history), name)
```

In `_work`, load capacities after resolving `enabled_centers` and pass them to the reset builder:

```python
center_capacities = staffing_route._configured_center_capacities(enabled_centers, strict=True)
```

```python
assignments, sources = _defaults_only_assignments(
    roster=roster, full_day_off_names=absent, exact_defaults=exact_defaults,
    group_defaults=group_defaults, user_group_centers=user_group_centers,
    enabled_centers=enabled_centers, center_capacities=center_capacities,
    history=history,
)
```

Reuse this already-loaded `center_capacities` in the ordinary rebuild block rather than calling the settings reader a second time.

- [ ] **Step 6: Run focused reset tests**

Run: `pytest tests/test_staffing_rotations.py -k "reset_to_defaults" -v`

Expected: PASS, including disabled-center, absence, distribution, and capacity coverage.

- [ ] **Step 7: Commit the implementation**

Run:

```bash
git add src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py
git commit -m "fix: balance reset default groups"
```

### Task 2: Verify the isolated reset behavior

**Files:**

- Verify: `tests/test_staffing_rotations.py`
- Verify: `src/zira_dashboard/routes/rotations.py`

**Interfaces:**

- Consumes: the capacity-aware reset builder completed in Task 1.
- Produces: evidence that the reset route and ordinary rebuild route both remain valid.

- [ ] **Step 1: Run all rotations route tests**

Run: `pytest tests/test_staffing_rotations.py -v`

Expected: PASS with zero failures.

- [ ] **Step 2: Inspect the final diff and whitespace**

Run: `git show --check --stat HEAD && git diff HEAD~1..HEAD -- src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py`

Expected: no whitespace errors; only reset builder/caller and its regression tests changed.

- [ ] **Step 3: Push the behavior commit**

Run: `git push origin main`

Expected: remote `main` advances with the balanced reset behavior.
