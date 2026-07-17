# Partial Auto-Schedule Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply a safe partial rebuild of enabled auto work centers and report, rather than reject, people or coverage that cannot be completed.

**Architecture:** Replace the complete-schedule gate in `rotation_suggestions` with a deterministic partial placement result that preserves valid existing assignments, clears invalid auto-center assignments, and adds every safely eligible person it can. The rebuild route persists that result and separates hard validation failures from reporting warnings. The staffing browser applies every successful response and renders warnings/unplaced people as informational coverage items.

**Tech Stack:** Python 3, FastAPI, existing `rotation_suggestions`/`schedule_solver` models, vanilla JavaScript, pytest.

## Global Constraints

- The server remains authoritative for skills, qualifications, preferences, defaults, capacities, and assignment sources.
- Rebuild only changes enabled auto work centers; assignments outside them remain untouched.
- Valid existing enabled-center assignments remain; unqualified or over-capacity enabled-center assignments are cleared before filling.
- Qualification, enabled-center membership, capacity, and coupled crew safety are hard constraints.
- Minimum coverage, defaults, and unplaced people are warnings only and never discard safe assignments.
- A successful rebuild returns `200` with `ok:true`, `applied:true`, `assignments`, `warnings`, and `unplaced`.
- A malformed request or zero enabled centers remains a hard error; the UI only shows red “previous schedule kept” for such a response.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/zira_dashboard/rotation_suggestions.py` | Build, sanitize, partially place, and report automatic assignments. |
| `src/zira_dashboard/routes/rotations.py` | Validate hard rebuild preconditions, persist successful partial results, and serialize warnings/unplaced people. |
| `src/zira_dashboard/static/staffing.js` | Apply successful partial assignment maps and render informational placement notices. |
| `tests/test_rotation_suggestions.py` | Prove pure scheduler partial-fill, retention, sanitation, and default-report behavior. |
| `tests/test_staffing_rotations.py` | Prove endpoint response/persistence contract and client static behavior. |

### Task 1: Add pure partial-placement coverage and result behavior

**Files:**
- Modify: `tests/test_rotation_suggestions.py`
- Modify: `src/zira_dashboard/rotation_suggestions.py:1120-1555`

**Interfaces:**
- Consumes: `suggest_recycled_assignments(...) -> RecycledSuggestion` and existing `CandidateEdge` rank/level/preference metadata.
- Produces: a `RecycledSuggestion` with retained/generated `assignments`, `placed_people`, `unused_people`, `issues`, and `placement_issues` that describe a partial result without requiring `complete=True`.

- [ ] **Step 1: Write failing pure-scheduler tests**

```python
def test_partial_rebuild_keeps_safe_assignment_and_places_qualified_person():
    result = suggest_recycled_assignments(
        day=date(2026, 7, 14), mode="normal", roster=[
            person("Kept", {"Repair": 3}),
            person("Placed", {"Repair": 2}),
            person("Unqualified", {"Dismantle": 3}),
        ],
        base_assignments={"Repair 1": ["Kept"]},
        locked_assignments={"Repair 1": ["Kept"]},
        group_locations={"Repair": ("Repair 1",)},
        group_required_skills={"Repair": ("Repair",)},
        center_minimums={"Repair 1": 1}, center_capacities={"Repair 1": 2},
        runnable_centers={"Repair 1"},
    )
    assert result.assignments["Repair 1"] == ["Kept", "Placed"]
    assert result.unused_people == ("Unqualified",)
    assert result.complete is False

def test_partial_rebuild_clears_unqualified_enabled_assignment_before_fill():
    result = suggest_recycled_assignments(
        day=date(2026, 7, 14), mode="normal", roster=[
            person("Invalid", {"Dismantle": 3}), person("Qualified", {"Repair": 3}),
        ], base_assignments={"Repair 1": ["Invalid"]},
        group_locations={"Repair": ("Repair 1",)},
        group_required_skills={"Repair": ("Repair",)},
        center_capacities={"Repair 1": 1}, runnable_centers={"Repair 1"},
    )
    assert result.assignments["Repair 1"] == ["Qualified"]
    assert "Invalid" in result.unused_people
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_rotation_suggestions.py -k 'partial_rebuild' -v`

Expected: FAIL because the current complete-schedule path returns an all-or-nothing failure.

- [ ] **Step 3: Implement a deterministic partial placement helper**

In `rotation_suggestions.py`, replace the `solve_complete_schedule` gate with a helper that:

```python
def _partial_assignment_result(
    *, people, centers, candidates
) -> tuple[tuple[AssignmentDecision, ...], tuple[str, ...]]:
    """Return every individually safe capacity-respecting assignment."""
    remaining = {center.center: center.capacity for center in centers}
    selected = []
    for person in sorted(people, key=str.lower):
        choices = [edge for edge in candidates if edge.person == person and remaining[edge.center] > 0]
        if not choices:
            continue
        choice = min(choices, key=lambda edge: (-edge.level, edge.rank_cost, edge.center.lower()))
        selected.append(AssignmentDecision(choice.center, choice.person, choice.level, choice.preference, choice.rank_cost))
        remaining[choice.center] -= 1
    placed = {decision.person for decision in selected}
    return tuple(selected), tuple(name for name in sorted(people, key=str.lower) if name not in placed)
```

Sanitize enabled-center existing assignments first: retain a name once only when it is present in the available roster, qualifies for the center, and the center still has capacity. Emit an informational placement issue for each removed invalid/over-capacity assignment. Preserve coupled center handling by only accepting safe complete crew options; an unavailable coupled crew becomes a warning rather than a failure.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_rotation_suggestions.py -k 'partial_rebuild' -v`

Expected: PASS with both tests green.

- [ ] **Step 5: Commit the pure scheduler change**

```bash
git add src/zira_dashboard/rotation_suggestions.py tests/test_rotation_suggestions.py
git commit -m "fix: produce partial auto schedule assignments"
```

### Task 2: Make endpoint validation and persistence partial-aware

**Files:**
- Modify: `tests/test_staffing_rotations.py:437-915`
- Modify: `src/zira_dashboard/routes/rotations.py:40-180,459-592`

**Interfaces:**
- Consumes: a partial `RecycledSuggestion` from Task 1 and final merged `new_assignments`.
- Produces: HTTP 200 `{ok:true, applied:true, assignments, warnings, unplaced}` for every valid rebuild, including partial outcomes; HTTP 422 only for malformed/zero-enabled-center requests.

- [ ] **Step 1: Replace the all-or-nothing endpoint regression test with a partial success test**

```python
def test_rebuild_applies_safe_partial_assignments_and_reports_unplaced(monkeypatch):
    # Configure a suggestion containing Qualified at Repair 1 and Missing unplaced.
    response = client.post("/api/rotations/rebuild", json={"day": TARGET_DAY.isoformat(), "mode": "normal"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["applied"] is True
    assert response.json()["assignments"]["Repair 1"] == ["Qualified"]
    assert response.json()["unplaced"] == ["Missing"]
    assert saved[0].assignments["Repair 1"] == ["Qualified"]
```

Add a test with a defaulted qualified person already in the final assignment map and assert there is no `exact_default_violation` or `exact_default_unqualified` warning.

- [ ] **Step 2: Run the endpoint tests to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py -k 'rebuild and (partial or default)' -v`

Expected: FAIL because the route currently returns 422 when `suggestion.complete` is false or placement issues are present.

- [ ] **Step 3: Split hard validation from reporting validation in the route**

Replace `_validate_complete_rebuild` with two explicit paths:

```python
def _hard_rebuild_issues(*, enabled_centers, center_capacities, proposed_assignments, roster, required_skills):
    """Return only duplicate, unqualified generated, and capacity violations."""

def _rebuild_warnings(*, final_assignments, center_minimums, exact_defaults, group_defaults, ...):
    """Return minimum/default/unplaced reporting issues for the final map."""
```

Reject only non-empty `_hard_rebuild_issues`, unknown/malformed input, or an empty `enabled_centers` list. Persist the final map for all other results. Build warnings from `new_assignments` (not a pre-merge or empty proposal), append `suggestion` reporting issues, and return:

```python
{
    "ok": True, "applied": True,
    "assignments": new_assignments,
    "warnings": warning_messages,
    "unplaced": list(suggestion.unused_people),
}
```

- [ ] **Step 4: Run focused endpoint tests to verify they pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py -k 'rebuild and (partial or default or preserves_manual)' -v`

Expected: PASS.

- [ ] **Step 5: Commit the route contract change**

```bash
git add src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py
git commit -m "fix: apply partial rotation rebuilds"
```

### Task 3: Render successful partial rebuilds as informational coverage notices

**Files:**
- Modify: `tests/test_staffing_rotations.py:1390-1410`
- Modify: `src/zira_dashboard/static/staffing.js:1390-1595`

**Interfaces:**
- Consumes: successful rebuild JSON containing `warnings`, `unplaced`, `coverage`, and `assignments`.
- Produces: reconciled Scheduled picker cells and yellow informational list entries; red failure only for a non-OK response.

- [ ] **Step 1: Write a static UI contract test**

```python
def test_rebuild_ui_renders_successful_unplaced_people_as_information():
    js = Path("src/zira_dashboard/static/staffing.js").read_text()
    assert "data.unplaced" in js
    assert "could not be placed" in js
    assert "if (!resp.ok || !data.ok)" in js
    assert "applyRebuild(data, options);" in js
```

- [ ] **Step 2: Run the static test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py::test_rebuild_ui_renders_successful_unplaced_people_as_information -v`

Expected: FAIL because `data.unplaced` is not rendered by the current client.

- [ ] **Step 3: Extend successful rebuild rendering**

Add a helper and invoke it from `applyRebuild` before `renderCoverageIssues`:

```javascript
function placementWarnings(data) {
  const unplaced = Array.isArray(data.unplaced) ? data.unplaced : [];
  return unplaced.map(name => ({
    code: 'person_unplaced',
    message: `${name} could not be placed in an enabled Auto work center.`,
  }));
}
```

Merge these informational issues with coverage issues in `renderCoverageIssues`. Keep `renderPlacementFailure` unchanged for non-OK responses, so only hard failures display the red error toast/state.

- [ ] **Step 4: Run the UI contract test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py::test_rebuild_ui_renders_successful_unplaced_people_as_information -v`

Expected: PASS.

- [ ] **Step 5: Commit the UI behavior change**

```bash
git add src/zira_dashboard/static/staffing.js tests/test_staffing_rotations.py
git commit -m "fix: show partial rebuild notices"
```

### Task 4: Verify the full scheduler rebuild contract

**Files:**
- Verify: `tests/test_rotation_suggestions.py`
- Verify: `tests/test_staffing_rotations.py`
- Verify: `src/zira_dashboard/rotation_suggestions.py`
- Verify: `src/zira_dashboard/routes/rotations.py`
- Verify: `src/zira_dashboard/static/staffing.js`

- [ ] **Step 1: Run all targeted scheduler and endpoint tests**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_rotation_suggestions.py tests/test_staffing_rotations.py -v`

Expected: PASS with no regressions.

- [ ] **Step 2: Check the completed diff against the global constraints**

Run: `git diff HEAD~3..HEAD -- src/zira_dashboard/rotation_suggestions.py src/zira_dashboard/routes/rotations.py src/zira_dashboard/static/staffing.js tests/test_rotation_suggestions.py tests/test_staffing_rotations.py`

Expected: the diff shows only partial-fill behavior, final-map warning validation, successful-response rendering, and regression coverage.

- [ ] **Step 3: Commit any verification-only correction if required**

```bash
git add src/zira_dashboard/rotation_suggestions.py src/zira_dashboard/routes/rotations.py src/zira_dashboard/static/staffing.js tests/test_rotation_suggestions.py tests/test_staffing_rotations.py
git commit -m "test: cover partial scheduler rebuild"
```

Only create this commit if verification required a correction; otherwise leave the preceding task commits unchanged.

## Plan Self-Review

- Spec coverage: Task 1 implements safe partial placement, valid assignment retention, invalid assignment removal, skill/preference ranking, capacities, and soft reporting. Task 2 implements the successful partial API contract and final-assignment default validation. Task 3 applies the result and presents informational UI notices. Task 4 verifies the acceptance path end to end.
- Placeholder scan: no deferred behavior, unspecified validation, or incomplete test commands remain.
- Type consistency: Task 1 continues to return `RecycledSuggestion`; Task 2 serializes `unused_people` as `unplaced`; Task 3 consumes `data.unplaced` from that documented response.
