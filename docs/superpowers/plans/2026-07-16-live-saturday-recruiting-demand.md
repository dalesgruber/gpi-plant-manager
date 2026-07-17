# Live Saturday Recruiting Demand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep an open Saturday recruiting campaign's required headcount and visible summary synchronized with the work centers currently turned On.

**Architecture:** The Auto work-center save endpoint derives recruiting openings from its server-authoritative enabled list on Saturdays, then updates an existing recruitment through the lifecycle store before returning its serialized summary. The Staffing browser renders that server response immediately and does not guess demand client-side.

**Tech Stack:** Python 3, FastAPI, PostgreSQL-backed recruiting store, vanilla JavaScript, pytest.

## Global Constraints

- Use each enabled recruiting-capable center's effective minimum crew as its requested count.
- Preserve commitments; reject a change that would leave fewer requested slots than commitments require.
- Keep non-Saturday Auto work-center behavior unchanged.

---

## File Structure

- `src/zira_dashboard/routes/staffing.py`: derives recruiting counts from enabled centers so activation and live synchronization share one rule.
- `src/zira_dashboard/routes/rotations.py`: updates recruiting within a successful work-center save and returns a summary.
- `src/zira_dashboard/templates/staffing.html` and `src/zira_dashboard/static/staffing.js`: supply and refresh the demand display.
- `tests/test_staffing_rotations.py` and `tests/test_saturday_recruiting_static.py`: endpoint and browser contracts.

### Task 1: Server-authoritative Saturday recruitment synchronization

**Files:**

- Modify: `src/zira_dashboard/routes/staffing.py:320-334`
- Modify: `src/zira_dashboard/routes/rotations.py:370-424`
- Test: `tests/test_staffing_rotations.py`

**Interfaces:**

- Produces: `staffing._saturday_recruit_requested_counts(enabled: Sequence[str]) -> dict[int, int]`.
- Produces: `POST /api/rotations/auto-work-centers` responses with `saturday_recruiting`, the serialized updated recruitment or `null`.
- Consumes: `available_positions()`, `get(day)`, `update_openings(...)`, and `_effective_minimum(location)`.

- [ ] **Step 1: Write failing route tests**

```python
def test_auto_work_centers_updates_open_saturday_recruiting_demand(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    bundle = SimpleNamespace(
        recruitment=SimpleNamespace(status="recruiting", shift_start=time(6), shift_end=time(12)),
    )
    updated = []
    monkeypatch.setattr(rotations.saturday_recruiting_store, "get", lambda day: bundle)
    monkeypatch.setattr(rotations.staffing_route, "_saturday_recruit_requested_counts", lambda enabled: {17: 2, 18: 1})
    monkeypatch.setattr(rotations.saturday_recruiting_store, "update_openings", lambda **kwargs: updated.append(kwargs) or bundle)

    response = client.post("/api/rotations/auto-work-centers", json={
        "day": "2026-07-18", "work_centers": ["Repair 1", "Repair 2"], "turn_off": [],
    })

    assert response.status_code == 200
    assert updated[0]["requested_counts"] == {17: 2, 18: 1}
    assert updated[0]["shift_start"] == time(6)


def test_auto_work_centers_rejects_saturday_toggle_that_breaks_commitments(monkeypatch):
    client, rotations = _rotations_client(monkeypatch, raise_server_exceptions=False)
    monkeypatch.setattr(
        rotations.saturday_recruiting_store, "update_openings",
        lambda **kwargs: (_ for _ in ()).throw(
            rotations.saturday_recruiting_store.LifecycleConflict(
                "Requested openings cannot drop below committed Saturday coverage"
            )
        ),
    )

    response = client.post("/api/rotations/auto-work-centers", json={
        "day": "2026-07-18", "work_centers": [], "turn_off": [],
    })

    assert response.status_code == 409
    assert "committed Saturday coverage" in response.json()["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_staffing_rotations.py -k 'auto_work_centers and saturday_recruiting' -v`

Expected: FAIL because the endpoint neither updates the recruiting store nor returns a lifecycle conflict.

- [ ] **Step 3: Implement one shared derivation and lifecycle update**

```python
def _saturday_recruit_requested_counts(enabled: Sequence[str]) -> dict[int, int]:
    enabled_names = set(enabled)
    positions_by_name = {
        position.wc_name: position.wc_id
        for position in saturday_recruiting_store.available_positions()
    }
    return {
        positions_by_name[location.name]: _effective_minimum(location)
        for location in staffing.LOCATIONS
        if location.name in enabled_names
        and location.name in positions_by_name
        and _effective_minimum(location) > 0
    }
```

In `save_auto_work_centers._work`, on Saturday load the current recruitment before persisting enabled centers. If its status is `recruiting` or `closed`, call `update_openings(day=d, requested_counts=_saturday_recruit_requested_counts(enabled), shift_start=bundle.recruitment.shift_start, shift_end=bundle.recruitment.shift_end, actor=None, now=plant_now())`. Catch `SaturdayRecruitingError` and return `_error(str(exc), 409)` before `_save_enabled_auto_work_centers(enabled)`. Return `serialize_bundle(updated_bundle)` in the success JSON as `saturday_recruiting`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_staffing_rotations.py -k 'auto_work_centers and saturday_recruiting' -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py
git commit -m "feat: sync Saturday recruiting to work centers"
```

### Task 2: Live recruiting-demand display

**Files:**

- Modify: `src/zira_dashboard/templates/staffing.html:214-226`
- Modify: `src/zira_dashboard/static/staffing.js:1543-1607`
- Test: `tests/test_saturday_recruiting_static.py`
- Test: `tests/test_staffing_rotations.py`

**Interfaces:**

- Consumes: `data.saturday_recruiting.coverage.requested`, `total`, and `data.enabled_work_centers`.
- Produces: `renderSaturdayRecruitingDemand(bundle, enabledCenters)`, which updates `[data-saturday-recruit-demand]`.

- [ ] **Step 1: Write failing static contracts**

```python
def test_staffing_template_has_live_saturday_recruiting_demand_target():
    template = Path("src/zira_dashboard/templates/staffing.html").read_text()
    assert 'data-saturday-recruit-demand' in template


def test_work_center_save_renders_server_recruiting_demand():
    js = Path("src/zira_dashboard/static/staffing.js").read_text()
    assert "function renderSaturdayRecruitingDemand(bundle, enabledCenters)" in js
    assert "renderSaturdayRecruitingDemand(data.saturday_recruiting, data.enabled_work_centers);" in js
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_saturday_recruiting_static.py tests/test_staffing_rotations.py -k 'recruiting_demand' -v`

Expected: FAIL because the template target and renderer do not exist.

- [ ] **Step 3: Render only returned server state**

```javascript
function renderSaturdayRecruitingDemand(bundle, enabledCenters) {
  const demand = document.querySelector('[data-saturday-recruit-demand]');
  if (!demand) return;
  if (!bundle) {
    demand.textContent = `${(enabledCenters || []).length} work centers`;
    return;
  }
  const coverage = bundle.coverage || {};
  const requested = Number(coverage.requested || 0);
  const filled = Number(coverage.total || 0);
  demand.textContent = `${Math.max(0, requested - filled)} needed`;
}
```

Add a `data-saturday-recruit-demand` element to the Recruit control area. Before activation it represents enabled work centers; during active recruiting it represents remaining people needed. Invoke the renderer immediately after `applyEnabledCenters(data.enabled_work_centers)` in the successful `saveAutoCenters` path with both response fields. Do not call it from the failure path.

- [ ] **Step 4: Run focused UI tests to verify they pass**

Run: `pytest tests/test_saturday_recruiting_static.py tests/test_staffing_rotations.py -k 'recruiting_demand' -v`

Expected: PASS.

- [ ] **Step 5: Run regressions and commit**

Run: `pytest tests/test_saturday_recruiting_static.py tests/test_saturday_recruiting_manager_routes.py tests/test_staffing_rotations.py -v`

Expected: PASS.

```bash
git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js tests/test_saturday_recruiting_static.py tests/test_staffing_rotations.py
git commit -m "feat: show live Saturday recruiting demand"
```
