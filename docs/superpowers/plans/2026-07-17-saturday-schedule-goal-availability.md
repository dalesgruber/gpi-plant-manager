# Saturday Schedule Goal Availability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a Saturday schedule with no people in Unassigned display `Ready to schedule`, even when active people appear in Off.

**Architecture:** Extend the route-local minimum-crew balance helper with an optional, explicit set of people available for scheduling. Weekdays retain the existing roster-derived count. After the Saturday roster model has been rebuilt from commitments and availability overrides, recalculate the template balance from its final Unassigned names.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, pytest.

## Global Constraints

- People displayed as Saturday Off never count as unassigned scheduling capacity.
- The Schedule Goal uses the final Saturday roster model, including commitments and manager availability overrides.
- A Saturday with no Unassigned people has balance direction `ready`, zero recommended centers, and renders `Ready to schedule`.
- The weekday balance calculation and browser-side balance rendering remain unchanged.
- The balance remains advisory and continues to use effective minimum crews only.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/zira_dashboard/routes/staffing.py` | Accept the final available names when calculating a balance and supply Saturday's final Unassigned names after the Saturday roster rebuild. |
| `tests/test_staffing_rotations.py` | Prove the pure route helper and Staffing-page context use no capacity when Saturday Unassigned is empty. |

### Task 1: Derive the Saturday balance from final availability

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:198-238`
- Modify: `src/zira_dashboard/routes/staffing.py:1278-1291`
- Test: `tests/test_staffing_rotations.py:2718-2856`

**Interfaces:**
- Consumes: `_minimum_crew_balance_for_day(..., available_names: Collection[str] | None = None)`.
- Produces: the existing `MinimumCrewBalance`; with `available_names` supplied, its `unassigned_people` value is the count of those names that are active, non-reserve, present, and not already assigned.
- Produces: the template context key `minimum_crew_balance`, recalculated after the Saturday `bay_model` is final.

- [ ] **Step 1: Write the failing helper regression test**

Add this test near the Staffing context tests. It demonstrates that an empty Saturday-eligible set produces a ready balance even though the ordinary roster contains a person who is otherwise unassigned:

```python
def test_minimum_crew_balance_uses_explicit_saturday_available_names(monkeypatch):
    from zira_dashboard.routes import staffing as staffing_routes

    location = staffing.Location(
        "Repair 1", "Repair", "Bay 1", "Recycled", None,
        min_ops=1, max_ops=2, required_skills=("Repair",),
    )
    monkeypatch.setattr(staffing, "LOCATIONS", (location,))
    monkeypatch.setattr(staffing_routes, "_effective_minimum", lambda _loc: 1)

    result = staffing_routes._minimum_crew_balance_for_day(
        roster=[_person("Off Person", 3)],
        schedule=staffing.Schedule(day=date(2026, 7, 18), assignments={}),
        time_off_entries=[],
        enabled_centers=(),
        available_names=(),
    )

    assert result.unassigned_people == 0
    assert result.direction == "ready"
    assert result.center_count == 0
    assert result.recommended_centers == ()
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py::test_minimum_crew_balance_uses_explicit_saturday_available_names -q
```

Expected: FAIL because `_minimum_crew_balance_for_day` does not accept `available_names`.

- [ ] **Step 3: Implement explicit available-name filtering**

At the top of `src/zira_dashboard/routes/staffing.py`, add `Collection` to the existing `collections.abc` imports. Extend the helper's signature and replace its `waiting` calculation with the following branch, retaining its existing `absent`, `by_name`, and `assigned` calculations:

```python
def _minimum_crew_balance_for_day(
    *, roster, schedule, time_off_entries, enabled_centers,
    available_names: Collection[str] | None = None,
):
    """Compare people waiting with enabled work centers' open minimum slots."""
    # Existing enabled, absent, by_name, and assigned derivation stays here.
    if available_names is None:
        waiting = sum(
            person.active and not person.reserve and person.name not in absent
            and person.name not in assigned
            for person in roster
        )
    else:
        available = set(available_names)
        waiting = sum(
            person.active and not person.reserve and person.name in available
            and person.name not in absent and person.name not in assigned
            for person in roster
        )
```

Do not alter the existing minimum-slot calculation or the call to
`analyze_minimum_crew_balance`.

- [ ] **Step 4: Recalculate after the Saturday view is final**

Immediately after the Saturday `staffing_view.build_staffing_bays(...)` call,
replace the earlier payload with one derived from the final rail:

```python
        minimum_crew_balance = _minimum_crew_balance_payload(
            _minimum_crew_balance_for_day(
                roster=roster,
                schedule=sched,
                time_off_entries=time_off_entries,
                enabled_centers=enabled_auto_work_centers,
                available_names=bay_model.get("unassigned") or (),
            )
        )
```

Leave the original pre-Saturday balance calculation in place for all other
days.

- [ ] **Step 5: Run the focused test to verify it passes**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py::test_minimum_crew_balance_uses_explicit_saturday_available_names -q
```

Expected: PASS.

- [ ] **Step 6: Add page-context coverage for the final Saturday rail**

Extend `_render_staffing_page` with an optional `minimum_crew_balance` stub,
then add this context assertion. The stubbed helper captures its keyword
arguments and returns a ready payload when it receives the final empty
Unassigned rail:

```python
def test_saturday_context_recalculates_balance_from_final_unassigned(monkeypatch):
    from zira_dashboard.routes import staffing as staffing_routes

    calls = []
    original = staffing_routes._minimum_crew_balance_for_day

    def capture_balance(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(staffing_routes, "_minimum_crew_balance_for_day", capture_balance)
    ctx = _render_staffing_page(
        monkeypatch,
        day=date(2026, 7, 18),
        roster=[_person("Off Person", 3)],
        bay_model={
            "bays": [], "publish_block_reasons": [], "defaults_by_loc": {},
            "unassigned": [], "off": ["Off Person"], "reserves": [],
            "time_off_names": [], "time_off_entries": [],
            "partial_hours_by_name": {}, "partial_range_by_name": {},
            "partial_clear_by_name": {}, "people_meta": {}, "all_active_people": [],
        },
    )

    assert calls[-1]["available_names"] == ()
    assert ctx["minimum_crew_balance"]["unassigned_people"] == 0
    assert ctx["minimum_crew_balance"]["direction"] == "ready"
    assert ctx["minimum_crew_balance"]["center_count"] == 0
```

If the page helper needs Saturday store stubs, set
`staffing_routes.saturday_recruiting_store.get` to return `None` and
`staffing_routes.saturday_recruiting_store.available_positions` to return an
empty tuple before rendering.

- [ ] **Step 7: Run both regression tests to verify they pass**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py -k 'minimum_crew_balance_uses_explicit_saturday_available_names or saturday_context_recalculates_balance_from_final_unassigned' -q
```

Expected: 2 passed.

- [ ] **Step 8: Run adjacent schedule-goal coverage**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_auto_schedule_capacity.py tests/test_staffing_rotations.py tests/test_staffing_saturday_recruiting.py -q
```

Expected: PASS with no failures.

- [ ] **Step 9: Commit the implementation**

```bash
git add src/zira_dashboard/routes/staffing.py tests/test_staffing_rotations.py
git commit -m "fix: align Saturday schedule goal with availability"
```

## Plan self-review

- Spec coverage: Task 1 makes the final Saturday Unassigned rail the only Saturday capacity source, preserves weekday behavior, and verifies the ready result and the template payload.
- Placeholder scan: no deferred work or unspecified error handling remains; the existing minimum fallback is explicitly preserved.
- Type consistency: `available_names` is a `Collection[str] | None` at the helper boundary and a tuple of names from `bay_model` at the Saturday call site.
