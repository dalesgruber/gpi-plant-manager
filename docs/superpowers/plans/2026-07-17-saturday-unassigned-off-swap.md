# Saturday Unassigned/Off Swap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a manager move a person between Saturday Unassigned and Off with a hover-only swap control and a confirmation dialog, while making the correction persist and affect Saturday scheduling.

**Architecture:** Persist manager availability corrections as a day-level `person name -> "unassigned" | "off"` schedule mapping, leaving employee recruiting responses intact. The staffing route combines that mapping with committed recruiting responses before it builds the view model, validates saves, and validates publication. A dedicated API mutation edits one mapping entry and the browser updates its left rail after a confirmed request.

**Tech Stack:** FastAPI, PostgreSQL JSONB, Python dataclasses/pytest, Jinja templates, vanilla JavaScript, CSS.

## Global Constraints

- The swap controls render only when `is_saturday_recruiting` is true.
- Full-day time off always wins over an availability override.
- An override must not alter recruiting responses, notifications, or a committed worker’s recorded partial availability.
- A posted schedule must become a draft before persisting a manager override.
- Preserve unrelated staged and unstaged work in the shared worktree.

---

### Task 1: Persist and snapshot Saturday availability overrides

**Files:**

- Modify: `src/zira_dashboard/_schema.py:192-195`
- Modify: `src/zira_dashboard/staffing.py:340-714`
- Test: `tests/test_rotation_store.py:210-358`

**Interfaces:**

- Produces: `Schedule.saturday_availability_overrides: dict[str, str]`.
- Produces: `_validate_saturday_availability_overrides(value) -> dict[str, str]`, accepting only `"unassigned"` and `"off"`.

- [ ] **Step 1: Write failing persistence tests**

```python
def test_schedule_saturday_availability_overrides_round_trip(monkeypatch):
    overrides = {"Ana": "off", "Cara": "unassigned"}
    schedule = staffing.Schedule(
        day=date(2026, 7, 18), saturday_availability_overrides=overrides,
    )
    staffing.save_schedule(schedule)
    assert "saturday_availability_overrides" in executed[0][0]
    assert staffing._load_schedule_from_db(schedule.day).saturday_availability_overrides == overrides


@pytest.mark.parametrize("overrides", [{"Ana": "maybe"}, {1: "off"}, ["Ana"]])
def test_schedule_rejects_malformed_saturday_availability_overrides(monkeypatch, overrides):
    with pytest.raises(ValueError, match="saturday_availability_overrides"):
        staffing.save_schedule(
            staffing.Schedule(day=date(2026, 7, 18), saturday_availability_overrides=overrides)
        )
```

Follow the existing `assignment_sources` fake cursor/query pattern so the test asserts both SQL and hydration.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rotation_store.py -k 'saturday_availability_overrides' -v`

Expected: FAIL because `Schedule` has no `saturday_availability_overrides` field.

- [ ] **Step 3: Add the schedule metadata and schema column**

```python
# _schema.py
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS saturday_availability_overrides JSONB NOT NULL DEFAULT '{}'::jsonb;

# staffing.py
_SATURDAY_AVAILABILITY_STATES = frozenset(("unassigned", "off"))

def _validate_saturday_availability_overrides(value) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("saturday_availability_overrides must be a person mapping")
    if any(not isinstance(name, str) or state not in _SATURDAY_AVAILABILITY_STATES
           for name, state in value.items()):
        raise ValueError("saturday_availability_overrides values must be 'unassigned' or 'off'")
    return dict(value)
```

Add the dataclass field; add it to `snapshot_of`; select/hydrate it in both loaders; include validated JSON in `save_schedule` and `create_schedule_if_absent`; and copy it from a posted snapshot in `staffing_page`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_rotation_store.py -k 'saturday_availability_overrides or assignment_sources' -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/_schema.py src/zira_dashboard/staffing.py tests/test_rotation_store.py
git commit -m "feat: persist Saturday availability overrides"
```

### Task 2: Apply effective availability to Saturday lists, pickers, save, and publication

**Files:**

- Modify: `src/zira_dashboard/staffing_view.py:20-310`
- Modify: `src/zira_dashboard/routes/staffing.py:1193-1291,1448-1610`
- Modify: `src/zira_dashboard/saturday_recruiting.py:134-175`
- Test: `tests/test_staffing_saturday_recruiting.py:49-351`

**Interfaces:**

- Consumes: `Schedule.saturday_availability_overrides`.
- Produces: effective Saturday availability names for the view, picker restrictions, save guard, and publication guard.
- Extends: `validate_publish(bundle, assignments, people_by_name, full_day_off_names, manually_available_names=frozenset())`.

- [ ] **Step 1: Write failing behavior tests**

```python
def test_saturday_overrides_replace_recruiting_status_in_left_rail(patch_wcs):
    model = staffing_view.build_staffing_bays(
        roster=[_person("Ana", Repair=3), _person("Cara", Repair=3)],
        sched=_sched(), time_off_entries=[], publish_blocked=0,
        saturday_commitments={"Ana": {"start": time(6), "end": time(12)}},
        saturday_availability_overrides={"Ana": "off", "Cara": "unassigned"},
        saturday_shift=(time(6), time(12)),
    )
    assert model["unassigned"] == ["Cara"]
    assert model["off"] == ["Ana"]


def test_full_day_time_off_beats_saturday_unassigned_override(patch_wcs):
    model = staffing_view.build_staffing_bays(
        roster=[_person("Cara", Repair=3)], sched=_sched(), publish_blocked=0,
        time_off_entries=[{"name": "Cara", "hours": None}], saturday_commitments={},
        saturday_availability_overrides={"Cara": "unassigned"},
        saturday_shift=(time(6), time(12)),
    )
    assert model["unassigned"] == []
    assert model["off"] == []
```

Add route tests showing a manually Unassigned person may be assigned/saved and published, while a manually Off person is still rejected by the existing noncommitted validation.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_staffing_saturday_recruiting.py -v`

Expected: FAIL because `build_staffing_bays` has no override parameter and save/publication checks know only recruiting commitments.

- [ ] **Step 3: Implement one shared effective-availability calculation**

```python
def _effective_saturday_commitments(commitments, overrides, shift_start, shift_end):
    effective = dict(commitments)
    for name, destination in (overrides or {}).items():
        if destination == "off":
            effective.pop(name, None)
        else:
            effective.setdefault(name, {"start": shift_start, "end": shift_end})
    return effective
```

Build the map once from committed responses plus `sched.saturday_availability_overrides`. Pass it to `build_staffing_bays`; use its names in the Saturday save guard; and pass manually-Unassigned names to `validate_publish`. Extend `build_staffing_bays` with an optional override map so its pure tests cover the same precedence and ensure the picker pool, Unassigned, and Off lists agree.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_staffing_saturday_recruiting.py tests/test_staffing_schedule_metadata.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/staffing_view.py src/zira_dashboard/routes/staffing.py src/zira_dashboard/saturday_recruiting.py tests/test_staffing_saturday_recruiting.py tests/test_staffing_schedule_metadata.py
git commit -m "feat: apply Saturday availability overrides"
```

### Task 3: Add the manager mutation endpoint

**Files:**

- Modify: `src/zira_dashboard/routes/staffing.py:2383-2425`
- Test: `tests/test_staffing_saturday_recruiting.py`

**Interfaces:**

- Produces: `POST /api/staffing/saturday-availability` with JSON `{day, name, destination}`.
- Returns: `{ok: true, destination, unassigned_count, off_count}`.

- [ ] **Step 1: Write failing endpoint tests**

```python
def test_saturday_availability_endpoint_persists_override_and_drafts_posted_schedule(monkeypatch):
    posted = staffing.Schedule(day=SATURDAY, published=True, assignments={})
    saved = []
    monkeypatch.setattr(staffing_routes.staffing, "load_schedule", lambda _day: posted)
    monkeypatch.setattr(staffing_routes.staffing, "save_schedule", saved.append)
    monkeypatch.setattr(staffing_routes.saturday_recruiting_store, "get", lambda _day: _bundle())
    monkeypatch.setattr(staffing_routes.staffing, "load_roster", lambda: list(_people().values()))
    monkeypatch.setattr(staffing_routes, "_safe_time_off_entries", lambda _day: [])
    result = staffing_routes._set_saturday_availability_work(SATURDAY, "Cara", "unassigned")
    assert result["ok"] is True
    assert saved[0].published is False
    assert saved[0].saturday_availability_overrides == {"Cara": "unassigned"}


@pytest.mark.parametrize("body", [
    {"day": "2026-07-20", "name": "Cara", "destination": "off"},
    {"day": SATURDAY.isoformat(), "name": "Cara", "destination": "away"},
])
def test_saturday_availability_endpoint_rejects_invalid_requests(body):
    with pytest.raises(HTTPException) as exc_info:
        staffing_routes._parse_saturday_availability_body(body)
    assert exc_info.value.status_code == 422
```

Add explicit `_set_saturday_availability_work` tests for no active recruitment,
an unknown name, an inactive person, a reserve person, and a full-day-time-off
name. Each test must capture `save_schedule` in a list and assert that list is
empty after the expected `HTTPException(status_code=409)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_staffing_saturday_recruiting.py -k 'availability_endpoint' -v`

Expected: FAIL because the handler does not exist.

- [ ] **Step 3: Implement the atomic endpoint**

```python
@router.post("/api/staffing/saturday-availability")
async def set_saturday_availability(request: Request):
    body = await request.json()
    day, name, destination = _parse_saturday_availability_body(body)
    result = await asyncio.to_thread(
        _set_saturday_availability_work, day, name, destination,
    )
    return JSONResponse(result)
```

Implement `_parse_saturday_availability_body` to require a JSON object with a
Saturday ISO date, a nonblank name, and destination in
`{"unassigned", "off"}`; raise `HTTPException(status_code=422, detail="Invalid Saturday availability request")` for invalid input.
Implement `_set_saturday_availability_work` to require an active recruiting
bundle, find an active non-reserve roster person, reject full-day time off,
call `staffing.draft_from_posted(staffing.load_schedule(day))`, replace only
that name’s override, save it, call `_bust_after_mutation()`, and return counts
from Task 2’s effective-availability helper. Convert missing or invalid
business state to `HTTPException(status_code=409, detail="Saturday availability could not be changed")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_staffing_saturday_recruiting.py tests/test_staffing_schedule_metadata.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py tests/test_staffing_saturday_recruiting.py
git commit -m "feat: edit Saturday availability from scheduler"
```

### Task 4: Render the hover swap control and confirmation dialog

**Files:**

- Modify: `src/zira_dashboard/templates/staffing.html:57-76,432-462`
- Modify: `src/zira_dashboard/static/staffing.js:480-620`
- Modify: `src/zira_dashboard/static/staffing.css:228-246`
- Test: `tests/test_staffing_static.py`

**Interfaces:**

- Consumes: `window.SATURDAY_RECRUITING`, `window.SCHEDULE_DAY`, and Task 3’s API.
- Produces: rows with `.saturday-availability-swap`, dialog `#saturday-availability-confirm`, and a confirmed in-place move.

- [ ] **Step 1: Write failing static contract tests**

```python
def test_saturday_availability_swap_is_limited_to_left_rail_and_has_a_dialog():
    html, js, css = _template(), _script(), _style()
    assert 'class="saturday-availability-swap"' in html
    assert 'id="saturday-availability-confirm"' in html
    assert "/api/staffing/saturday-availability" in js
    assert ".saturday-availability-swap { opacity: 0;" in css
    assert ".saturday-person-row:hover .saturday-availability-swap" in css
```

Assert the template emits controls only in the Saturday Unassigned and Off loops and gives each an `aria-label` naming the destination.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_staffing_static.py -k 'saturday_availability_swap' -v`

Expected: FAIL because no swap markup, dialog, API call, or CSS exists.

- [ ] **Step 3: Add accessible markup, dialog behavior, and styling**

```javascript
document.addEventListener('click', (event) => {
  const button = event.target.closest('.saturday-availability-swap');
  if (!button || !__saturdayRecruiting || __viewingPosted) return;
  openSaturdayAvailabilityConfirm(button.dataset.name, button.dataset.destination, button);
});

async function confirmSaturdayAvailability() {
  const response = await fetch('/api/staffing/saturday-availability', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({day: window.SCHEDULE_DAY, name: state.name, destination: state.destination}),
  });
  if (!response.ok) throw new Error((await response.json()).detail || 'Could not update Saturday availability.');
  moveSaturdayLeftRailRow(state.name, state.destination);
}
```

Render a button after each name in only the two Saturday lists. The destination is the opposite list. Use a compact dialog with Cancel/Move, restore trigger focus after closing, disable Move during fetch, and show errors in an `aria-live` element. Keep the icon visible on `:focus-within` as well as hover, and reserve its inline space to prevent name jitter.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_staffing_static.py tests/test_saturday_recruiting_static.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css tests/test_staffing_static.py
git commit -m "feat: add Saturday availability swap control"
```

### Task 5: Verify the integrated change

**Files:**

- Verify only: files changed in Tasks 1-4.

- [ ] **Step 1: Run the focused scheduler suite**

Run: `pytest tests/test_rotation_store.py tests/test_staffing_saturday_recruiting.py tests/test_staffing_schedule_metadata.py tests/test_staffing_static.py tests/test_saturday_recruiting_static.py -v`

Expected: PASS with zero failures.

- [ ] **Step 2: Run the full test suite**

Run: `pytest -q`

Expected: PASS with zero failures; database-dependent tests may be explicitly skipped when `DATABASE_URL` is absent.

- [ ] **Step 3: Inspect the final diff and worktree scope**

Run: `git diff --check HEAD~4..HEAD && git status --short`

Expected: no whitespace errors; only this feature’s intended files are committed, and pre-existing user work remains untouched.
