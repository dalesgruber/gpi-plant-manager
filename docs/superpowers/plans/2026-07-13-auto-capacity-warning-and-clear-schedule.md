# Auto Capacity Warning and Clear Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Warn a planner how many Auto work centers to enable when automatic scheduling leaves available people unused, and provide a confirmed action that empties every assignment on a draft schedule.

**Architecture:** Add a pure capacity-expansion calculator beside the existing minimum-crew capacity calculator. The staffing recommendation helpers append its advisory warning to both page-render and rebuild results; the existing warning renderer displays it unchanged. Split the current Reset to defaults UI from a new Clear schedule control, with the latter using the current client-side picker reconciliation and autosave pipeline.

**Tech Stack:** Python 3, FastAPI, Jinja2, vanilla JavaScript, CSS, pytest.

## Global Constraints

- Apply the warning only to weekday automatic scheduling; it must never turn on centers or block a rebuild.
- Preserve manual/default locks, Auto and Training toggles, notes, time off, shift hours, schedule goal, and published snapshots.
- Base the warning on available people, per-center maximum operators, and deterministic work-center ordering.
- Retain all existing shortage, training, and safety warnings.
- Clear schedule must require confirmation, preserve all non-assignment schedule data, respect posted/edit locks, and use the existing autosave path.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/zira_dashboard/auto_schedule_capacity.py` | Pure calculation for the number of disabled Auto centers required to hold a known number of unassigned people. |
| `src/zira_dashboard/routes/staffing.py` | Convert the calculator result into one advisory warning and append it to the recommendation result used by both GET and rebuild flows. |
| `src/zira_dashboard/templates/staffing.html` | Keep Reset to defaults and render the distinct Clear schedule control. |
| `src/zira_dashboard/static/staffing.js` | Confirm and clear every selected picker item, then update the existing live UI and autosave. |
| `src/zira_dashboard/static/staffing.css` | Give the destructive clear control a distinct but compact visual treatment. |
| `tests/test_auto_schedule_capacity.py` | Unit-test the pure expansion math. |
| `tests/test_staffing_rotations.py` | Test rebuild/page-level warning composition without a database. |
| `tests/test_staffing_static.py` | Guard the control labels, confirmation, lock behavior, UI reconciliation, and autosave hook. |

### Task 1: Add pure Auto-center expansion capacity calculation

**Files:**
- Modify: `src/zira_dashboard/auto_schedule_capacity.py`
- Modify: `tests/test_auto_schedule_capacity.py`

**Interfaces:**
- Produces: `AutoExpansion(unassigned_people: int, centers_to_enable: int | None, usable_centers: tuple[str, ...])`.
- Produces: `analyze_auto_expansion(*, unassigned_people: int, disabled_centers: Sequence[str], open_slots_by_center: Mapping[str, int], center_order: Mapping[str, int]) -> AutoExpansion`.
- Consumed by: Task 2's staffing warning helper.

- [ ] **Step 1: Write the failing unit tests**

Append these tests to `tests/test_auto_schedule_capacity.py`:

```python
from zira_dashboard.auto_schedule_capacity import (
    analyze_auto_capacity,
    analyze_auto_expansion,
)


def test_expansion_uses_largest_open_centers_to_minimize_toggle_count():
    result = analyze_auto_expansion(
        unassigned_people=4,
        disabled_centers=("One", "Two", "Three"),
        open_slots_by_center={"One": 1, "Two": 3, "Three": 2},
        center_order={"One": 0, "Two": 1, "Three": 2},
    )

    assert result.unassigned_people == 4
    assert result.centers_to_enable == 2
    assert result.usable_centers == ("Two", "Three", "One")


def test_expansion_reports_no_count_when_all_disabled_capacity_is_insufficient():
    result = analyze_auto_expansion(
        unassigned_people=4,
        disabled_centers=("One", "Two"),
        open_slots_by_center={"One": 1, "Two": 2},
        center_order={"One": 0, "Two": 1},
    )

    assert result.unassigned_people == 4
    assert result.centers_to_enable is None
    assert result.usable_centers == ("Two", "One")
```

- [ ] **Step 2: Run the unit tests to verify they fail**

Run: `pytest tests/test_auto_schedule_capacity.py -v`

Expected: collection fails because `analyze_auto_expansion` does not exist.

- [ ] **Step 3: Implement the smallest deterministic calculator**

Add this dataclass and function below `AutoCapacity` in `src/zira_dashboard/auto_schedule_capacity.py`:

```python
@dataclass(frozen=True)
class AutoExpansion:
    unassigned_people: int
    centers_to_enable: int | None
    usable_centers: tuple[str, ...]


def analyze_auto_expansion(
    *,
    unassigned_people: int,
    disabled_centers: Sequence[str],
    open_slots_by_center: Mapping[str, int],
    center_order: Mapping[str, int],
) -> AutoExpansion:
    remaining = max(0, int(unassigned_people))
    usable_names = tuple(sorted(
        (
            center for center in dict.fromkeys(disabled_centers)
            if int(open_slots_by_center.get(center, 0)) > 0
        ),
        key=lambda center: (
            -int(open_slots_by_center.get(center, 0)),
            center_order.get(center, 1_000_000),
            center.lower(),
        ),
    ))
    if remaining == 0:
        return AutoExpansion(0, 0, usable_names)

    covered = 0
    for count, center in enumerate(usable_names, start=1):
        covered += max(0, int(open_slots_by_center.get(center, 0)))
        if covered >= remaining:
            return AutoExpansion(remaining, count, usable_names)
    return AutoExpansion(remaining, None, usable_names)
```

- [ ] **Step 4: Run the focused unit tests to verify they pass**

Run: `pytest tests/test_auto_schedule_capacity.py -v`

Expected: all capacity tests pass.

- [ ] **Step 5: Commit the pure capacity calculation**

```bash
git add src/zira_dashboard/auto_schedule_capacity.py tests/test_auto_schedule_capacity.py
git commit -m "feat: calculate auto center expansion capacity"
```

### Task 2: Return the expansion warning from page and rebuild recommendations

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:19,490-655`
- Modify: `tests/test_staffing_rotations.py`

**Interfaces:**
- Consumes: `analyze_auto_expansion` and `AutoExpansion` from Task 1.
- Produces: `_append_auto_expansion_warning(*, suggestion, capacity, enabled_work_centers, base_assignments, assignment_sources) -> RecycledSuggestion`.
- Consumed by: `_recycled_suggestion_for_day` and `_recycled_context_for_day`; `POST /api/rotations/rebuild` already returns their `warnings` unchanged.

- [ ] **Step 1: Write the failing warning-composition tests**

Add a focused helper test in `tests/test_staffing_rotations.py`. It keeps database calls out by monkeypatching the center capacity readers and uses the existing `_stub_recommendation_inputs` fixture helper:

```python
def test_rebuild_warns_how_many_auto_centers_to_enable_for_unused_people(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    staffing_route = _stub_recommendation_inputs(monkeypatch)
    sched = staffing.Schedule(day=TARGET_DAY)
    roster = [_person("Green One", 3), _person("Green Two", 3), _person("Green Three", 3)]

    monkeypatch.setattr(staffing_route, "_enabled_auto_work_centers", lambda _d: {"Repair 1"})
    monkeypatch.setattr(staffing_route.work_centers_store, "max_ops", lambda loc: 1 if loc.name == "Repair 1" else 2)
    monkeypatch.setattr(rotations.staffing, "load_roster", lambda: roster)
    monkeypatch.setattr(rotations.staffing, "load_schedule", lambda _d: sched)
    monkeypatch.setattr(rotations.staffing, "save_schedule", lambda _schedule: None)
    monkeypatch.setattr(rotations._http_cache, "invalidate_today_cache", lambda: None)

    response = client.post("/api/rotations/rebuild", json={"day": "2026-07-14", "mode": "normal"})

    assert response.status_code == 200
    assert "Turn on 1 more Auto work center to schedule all 2 available people." in response.json()["warnings"]
```

Add a second test for the exhausted case by changing the `max_ops` monkeypatch to return `1` for every center and asserting this exact warning:

```python
"Not enough Auto work-center capacity is available to schedule all 2 remaining people."
```

If the live `LOCATIONS` fixture contains enough eligible centers for the second test, monkeypatch `staffing_route.staffing.LOCATIONS` to a three-center tuple of `staffing.Location` instances (`Repair 1`, `Repair 2`, `Repair 3`) before posting.

- [ ] **Step 2: Run the route tests to verify they fail**

Run: `pytest tests/test_staffing_rotations.py -k "auto_centers_to_enable or exhausted" -v`

Expected: FAIL because neither advisory warning is appended.

- [ ] **Step 3: Add one warning helper and invoke it from both recommendation paths**

Update the import near line 19 of `src/zira_dashboard/routes/staffing.py`:

```python
from ..auto_schedule_capacity import AutoCapacity, analyze_auto_capacity, analyze_auto_expansion
```

Add this helper immediately before `_recycled_suggestion_for_day`:

```python
def _append_auto_expansion_warning(
    *, suggestion, capacity, enabled_work_centers, base_assignments, assignment_sources,
):
    if capacity.shortage:
        return suggestion
    generated_people = {
        name
        for sources in suggestion.sources.values()
        for name, source in sources.items()
        if source == rotation_suggestions.GENERATED_SOURCE
    }
    unassigned_people = max(0, capacity.available_people - len(generated_people))
    if not unassigned_people:
        return suggestion

    enabled = set(_ordered_work_center_names(enabled_work_centers))
    locks = _protected_locks(assignment_sources, base_assignments, allowed_centers=None)
    open_slots_by_center = {}
    disabled_centers = []
    for loc in staffing.LOCATIONS:
        if loc.name in enabled:
            continue
        maximum = work_centers_store.max_ops(loc)
        if maximum is None:
            maximum = unassigned_people
        occupied = set(base_assignments.get(loc.name, [])) | set(locks.get(loc.name, []))
        open_slots_by_center[loc.name] = max(0, int(maximum) - len(occupied))
        disabled_centers.append(loc.name)

    expansion = analyze_auto_expansion(
        unassigned_people=unassigned_people,
        disabled_centers=disabled_centers,
        open_slots_by_center=open_slots_by_center,
        center_order=_location_order(),
    )
    if expansion.centers_to_enable is None:
        warning = (
            "Not enough Auto work-center capacity is available to schedule "
            f"all {unassigned_people} remaining people."
        )
    else:
        noun = "center" if expansion.centers_to_enable == 1 else "centers"
        people = "person" if unassigned_people == 1 else "people"
        warning = (
            f"Turn on {expansion.centers_to_enable} more Auto work {noun} "
            f"to schedule all {unassigned_people} available {people}."
        )
    return replace(suggestion, warnings=tuple(suggestion.warnings) + (warning,))
```

In `_recycled_suggestion_for_day`, after the existing `if capacity.shortage:` block, append the expansion warning with the same `suggestion`, `capacity`, `enabled`, `base_assignments`, and `assignment_sources` values before returning. In `_recycled_context_for_day`, perform the identical append before assigning `ctx["rotation_warnings"]`. This keeps the GET and rebuild response paths authoritative and byte-for-byte consistent.

- [ ] **Step 4: Run the focused route tests to verify they pass**

Run: `pytest tests/test_staffing_rotations.py -k "rebuild or capacity_for_day" -v`

Expected: all selected rebuild and capacity-orchestration tests pass, including both new warning cases.

- [ ] **Step 5: Commit the warning integration**

```bash
git add src/zira_dashboard/routes/staffing.py tests/test_staffing_rotations.py
git commit -m "feat: warn when auto centers cannot use available staff"
```

### Task 3: Add a confirmed Clear schedule UI action

**Files:**
- Modify: `src/zira_dashboard/templates/staffing.html:161`
- Modify: `src/zira_dashboard/static/staffing.js:45-74`
- Modify: `src/zira_dashboard/static/staffing.css:629-635`
- Modify: `tests/test_staffing_static.py`

**Interfaces:**
- Produces: `#reset-schedule-btn` for the existing default-replacement behavior.
- Produces: `#clear-schedule-btn` that clears picker selections only after confirmation.
- Consumes: existing `updateDdSummary`, `syncLeftRailWithSchedule`, `refreshPickerVisibility`, `__prevSel`, and `kickAutosave` functions.

- [ ] **Step 1: Write the failing static behavior test**

Append this test to `tests/test_staffing_static.py`:

```python
def test_clear_schedule_is_distinct_from_reset_and_uses_existing_autosave_flow():
    html = _template()
    js = _script()
    css = Path("src/zira_dashboard/static/staffing.css").read_text()

    assert 'id="reset-schedule-btn" class="clear-btn">Reset to defaults</button>' in html
    assert 'id="clear-schedule-btn" class="clear-btn clear-schedule-btn">Clear schedule</button>' in html
    assert "Reset every Scheduled cell to the page defaults?" in js
    assert "Clear every Scheduled cell for this day?" in js
    assert "const __resetBtn = document.getElementById('reset-schedule-btn');" in js
    assert "const __clearBtn = document.getElementById('clear-schedule-btn');" in js
    assert "cb.checked = false;" in js
    assert "item.classList.remove('selected');" in js
    assert "syncLeftRailWithSchedule();" in js
    assert "refreshPickerVisibility();" in js
    assert "kickAutosave();" in js
    assert ".clear-schedule-btn:hover" in css
```

- [ ] **Step 2: Run the static test to verify it fails**

Run: `pytest tests/test_staffing_static.py::test_clear_schedule_is_distinct_from_reset_and_uses_existing_autosave_flow -v`

Expected: FAIL because the template still has only `#clear-schedule-btn` labeled Reset to defaults.

- [ ] **Step 3: Render separate controls and wire both handlers**

Replace line 161 of `src/zira_dashboard/templates/staffing.html` with:

```html
<button type="button" id="reset-schedule-btn" class="clear-btn">Reset to defaults</button>
<button type="button" id="clear-schedule-btn" class="clear-btn clear-schedule-btn">Clear schedule</button>
```

In `src/zira_dashboard/static/staffing.js`, rename the current `__clearBtn` declaration to:

```javascript
const __resetBtn = document.getElementById('reset-schedule-btn');
```

and use `__resetBtn` throughout its existing Reset to defaults handler without changing its confirmation copy or selection behavior. Immediately after that handler, add:

```javascript
const __clearBtn = document.getElementById('clear-schedule-btn');
if (__clearBtn) {
  __clearBtn.addEventListener('click', () => {
    if (__isPublished && !__unlocked) {
      alert('This schedule is Posted. Click Edit first if you need to clear it.');
      return;
    }
    if (__viewingPosted) return;
    if (!confirm('Clear every Scheduled cell for this day?\n\n(Time off and notes stay. You can undo this before leaving the page.)')) return;
    document.querySelectorAll('details.sched-dd').forEach(dd => {
      dd.querySelectorAll('.dd-item.selected').forEach(item => {
        const cb = item.querySelector('input[type=checkbox]');
        if (cb) cb.checked = false;
        item.classList.remove('selected');
      });
      updateDdSummary(dd);
      __prevSel.set(dd, []);
    });
    syncLeftRailWithSchedule();
    refreshPickerVisibility();
    kickAutosave();
  });
}
```

Keep the current `clear-btn` base styles. Add the destructive hover style to `src/zira_dashboard/static/staffing.css`:

```css
.clear-schedule-btn:hover {
  color: var(--bad);
  border-color: var(--bad);
  background: color-mix(in srgb, var(--bad) 8%, transparent);
}
```

Remove the old `.clear-btn:hover` red styling so Reset to defaults stays neutral; replace it with `color: var(--fg); border-color: var(--muted);`.

- [ ] **Step 4: Run focused static tests to verify they pass**

Run: `pytest tests/test_staffing_static.py -k "reset_to_defaults or clear_schedule" -v`

Expected: both the reset reconciliation test and the new clear-schedule test pass.

- [ ] **Step 5: Commit the clear schedule action**

```bash
git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css tests/test_staffing_static.py
git commit -m "feat: add confirmed clear schedule control"
```

### Task 4: Run end-to-end verification and inspect the changed behavior

**Files:**
- Verify only: files changed in Tasks 1-3

**Interfaces:**
- Consumes: all completed test coverage and the local scheduler page.
- Produces: a verified implementation ready for review.

- [ ] **Step 1: Run the complete targeted suite**

Run: `pytest tests/test_auto_schedule_capacity.py tests/test_staffing_rotations.py tests/test_staffing_static.py -v`

Expected: all tests pass.

- [ ] **Step 2: Run the repository's normal validation command**

Read `README.md` or `pyproject.toml` to select the documented test command, then run it. If the documented command is `pytest`, run: `pytest -q`.

Expected: exit code 0; if an unrelated pre-existing failure occurs, record its exact test name and output without changing unrelated code.

- [ ] **Step 3: Perform a focused browser check**

Open a draft weekday schedule and verify:

1. Reset to defaults still restores the existing page defaults.
2. Clear schedule requires confirmation, empties all Scheduled pickers, updates the Unscheduled list, and triggers autosave.
3. A posted schedule refuses both actions until Edit is selected.
4. With more available workers than enabled Auto-center capacity, running a schedule-goal action displays the exact count warning; after enabling sufficient centers and rebuilding, the warning disappears.

- [ ] **Step 4: Review the diff and commit verification fixes only if needed**

Run: `git diff HEAD~3..HEAD --check && git status --short`

Expected: no whitespace errors and no unexpected modified files. If the verification steps required a production or test correction, commit only those corrections using `git add <specific-files>` followed by `git commit -m "fix: verify scheduler capacity controls"`.
