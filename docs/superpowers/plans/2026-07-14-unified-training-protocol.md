# Unified Training Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-work-center Training toggles with one scheduler-owned training protocol that reserves a trainee at an exact work center, pairs a level-3 trainer on day one, and promotes required skills after the configured attended days.

**Architecture:** Extend the existing training-block lifecycle with an exact work-center target and persisted target skill IDs. New records produce direct work-center scheduler effects; legacy records without a center retain their group-based behavior until they finish. Staffing becomes the only training-protocol surface.

**Tech Stack:** FastAPI, Jinja, vanilla JavaScript/CSS, PostgreSQL, pytest, Ruff.

## Global Constraints

- New protocols require trainee level 0 and trainer level 3 for every required skill of the selected work center.
- Day one reserves trainee and trainer together; later attended days reserve only the trainee.
- Full-day trainee absences extend the protocol; pause/end suppress effects; end never promotes skills.
- Completion promotes every persisted target skill to level 1 exactly once.
- Remove the Staffing per-row Training checkbox and old untrained-picker behavior, but retain manual later-day trainer assignment.

---

### Task 1: Persist exact protocol targets and validate creation

**Files:**

- Modify: `src/zira_dashboard/_schema.py:203-226`
- Modify: `src/zira_dashboard/staffing.py:60-170`
- Modify: `src/zira_dashboard/rotation_store.py:20-220`
- Test: `tests/test_rotation_store.py:70-150`

**Interfaces:**

- Produce `TrainingBlock.work_center: str | None` and `TrainingBlock.skill_ids: tuple[int, ...]`.
- Change `create_block` to accept `work_center: str`; legacy rows retain `work_center=None`.

- [ ] **Step 1: Write failing persistence tests**

```python
def test_training_block_persists_exact_center_and_skill_ids(monkeypatch):
    monkeypatch.setattr(rotation_store.db, "query", fake_valid_protocol_query)
    block = rotation_store.create_block(
        trainee_id=1, trainer_id=2, work_center="Repair 1",
        start_day=date(2026, 7, 14), planned_attended_days=5,
    )
    assert block.work_center == "Repair 1"
    assert block.skill_ids == (9,)


def test_training_block_rejects_trainer_below_three_for_any_target_skill(monkeypatch):
    monkeypatch.setattr(rotation_store.db, "query", fake_multi_skill_levels((0, 0), (3, 2)))
    with pytest.raises(rotation_store.InvalidTrainingBlock, match="level 3"):
        rotation_store.create_block(
            trainee_id=1, trainer_id=2, work_center="Loading/Jockeying",
            start_day=date(2026, 7, 14), planned_attended_days=5,
        )
```

- [ ] **Step 2: Run the new tests to verify failure**

Run: `pytest tests/test_rotation_store.py -k 'persists_exact_center or any_target_skill' -v`

Expected: FAIL because `create_block` does not accept `work_center`.

- [ ] **Step 3: Implement the schema and store contract**

```sql
ALTER TABLE rotation_training_blocks ADD COLUMN IF NOT EXISTS work_center TEXT;
ALTER TABLE rotation_training_blocks ADD COLUMN IF NOT EXISTS skill_ids INTEGER[];
UPDATE rotation_training_blocks SET skill_ids = ARRAY[skill_id] WHERE skill_ids IS NULL;
```

Use a single `staffing.location_by_name(name)` helper that returns the configured
`Location | None`. On creation, derive `required_skills_for(location)`, resolve
all skill IDs, validate all trainee levels equal 0 and all trainer levels equal
3, and write `work_center` and `skill_ids`; retain `skill_id=skill_ids[0]`
for legacy hydration.

- [ ] **Step 4: Verify green**

Run: `pytest tests/test_rotation_store.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/_schema.py src/zira_dashboard/staffing.py src/zira_dashboard/rotation_store.py tests/test_rotation_store.py
git commit -m "feat: persist exact training protocol targets"
```

### Task 2: Reserve exact work centers in the scheduler

**Files:**

- Modify: `src/zira_dashboard/rotation_training.py:20-170`
- Modify: `src/zira_dashboard/rotation_suggestions.py:1100-1170`
- Test: `tests/test_rotation_training.py:20-220`
- Test: `tests/test_rotation_suggestions.py:650-730`

**Interfaces:**

- Add `locked_work_centers: dict[str, list[str]]` and `temporary_extra_work_centers: dict[str, list[str]]` to `BlockEffect`.
- Preserve existing `locked_people`/group fields for legacy records.

- [ ] **Step 1: Write failing exact-placement tests**

```python
def test_exact_center_protocol_pairs_only_on_day_one():
    block = _block(work_center="Repair 2")
    first = rotation_training.effect_for_day(block, date(2026, 7, 14))
    later = rotation_training.effect_for_day(block, date(2026, 7, 15))
    assert first.locked_work_centers == {"Repair 2": ["Trainee"]}
    assert first.temporary_extra_work_centers == {"Repair 2": ["Trainer"]}
    assert later.locked_work_centers == {"Repair 2": ["Trainee"]}
    assert later.temporary_extra_work_centers == {}


def test_exact_center_protocol_never_falls_back_to_sibling_center():
    out = rotation_suggestions.suggest_recycled_assignments(
        day=date(2026, 7, 14), mode="normal", roster=protocol_roster(),
        group_locations={"Repair": ("Repair 1", "Repair 2")},
        block_effects=[exact_repair_two_effect()],
    )
    assert out.assignments["Repair 2"] == ["Trainee", "Trainer"]
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_rotation_training.py tests/test_rotation_suggestions.py -k exact_center_protocol -v`

Expected: FAIL because effects only identify a group.

- [ ] **Step 3: Implement direct-center reservation**

For blocks with `work_center`, `effect_for_day` must return direct-center
maps. The first planned day supplies both maps; later days supply only the
trainee map. In the solver, consume direct-center maps before grouped effects;
warn without moving anybody if the center is disabled, at capacity, or a
manual lock owns the trainee/trainer. Then preserve the existing group-based
loop for legacy blocks.

- [ ] **Step 4: Verify green**

Run: `pytest tests/test_rotation_training.py tests/test_rotation_suggestions.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/rotation_training.py src/zira_dashboard/rotation_suggestions.py tests/test_rotation_training.py tests/test_rotation_suggestions.py
git commit -m "feat: reserve training protocols at exact work centers"
```

### Task 3: Promote all protocol skills on completion

**Files:**

- Modify: `src/zira_dashboard/rotation_training.py:145-185`
- Test: `tests/test_rotation_training.py:220-500`

**Interfaces:**

- Keep `reconcile_blocks(as_of: date) -> list[int]` stable.
- Iterate `block.skill_ids` before `mark_completed(block.id)`.

- [ ] **Step 1: Write a failing multi-skill reconciliation test**

```python
def test_reconcile_promotes_every_persisted_protocol_skill(monkeypatch):
    block = SimpleNamespace(
        id=42, trainee_id=17, skill_ids=(9, 10), skill_id=9,
        planned_attended_days=2, status="active",
    )
    calls, completed = [], []
    monkeypatch.setattr(rotation_training.rotation_store, "active_blocks", lambda: [block])
    monkeypatch.setattr(rotation_training.rotation_store, "resolved_days", lambda _id: [_attended()] * 2)
    monkeypatch.setattr(rotation_training.skill_levels, "set_person_skill_level", lambda *args: calls.append(args))
    monkeypatch.setattr(rotation_training.rotation_store, "mark_completed", completed.append)
    assert rotation_training.reconcile_blocks(date(2026, 7, 21)) == [42]
    assert calls == [(17, 9, 1), (17, 10, 1)]
    assert completed == [42]
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_rotation_training.py -k every_persisted_protocol_skill -v`

Expected: FAIL because reconciliation uses only `skill_id`.

- [ ] **Step 3: Implement ordered, retry-safe promotion**

```python
skill_ids = tuple(getattr(block, "skill_ids", ()) or (block.skill_id,))
try:
    for skill_id in skill_ids:
        skill_levels.set_person_skill_level(block.trainee_id, skill_id, 1)
except Exception:
    log.exception("Training block %s promotion failed; leaving active to retry", block.id)
    continue
rotation_store.mark_completed(block.id)
```

- [ ] **Step 4: Verify green**

Run: `pytest tests/test_rotation_training.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/rotation_training.py tests/test_rotation_training.py
git commit -m "feat: promote all training protocol skills on completion"
```

### Task 4: Replace the API request contract

**Files:**

- Modify: `src/zira_dashboard/routes/rotations.py:220-330`
- Test: `tests/test_staffing_rotations.py:179-333`

**Interfaces:**

- `POST /api/rotations/training-blocks` accepts `{trainee, trainer, work_center, start_day, workdays}`.
- Response exposes `work_center`, `skill_ids`, names, dates, and status.
- Existing pause/resume/end endpoints do not change.

- [ ] **Step 1: Write failing API tests**

```python
def test_training_protocol_endpoint_creates_exact_center_block(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    monkeypatch.setattr(rotations.rotation_store, "create_block", lambda **kw: protocol_block("Repair 2"))
    resp = client.post("/api/rotations/training-blocks", json={
        "trainee": "Alex", "trainer": "Green", "work_center": "Repair 2",
        "start_day": "2026-07-14", "workdays": 5,
    })
    assert resp.status_code == 200
    assert resp.json()["block"]["work_center"] == "Repair 2"


def test_training_protocol_endpoint_rejects_unknown_work_center(monkeypatch):
    client, _ = _rotations_client(monkeypatch)
    resp = client.post("/api/rotations/training-blocks", json={
        "trainee": "Alex", "trainer": "Green", "work_center": "Nope",
        "start_day": "2026-07-14", "workdays": 5,
    })
    assert resp.status_code == 422
    assert "work center" in resp.json()["error"].lower()
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_staffing_rotations.py -k training_protocol_endpoint -v`

Expected: FAIL because the endpoint requires `group`.

- [ ] **Step 3: Implement request parsing and payload**

Require nonblank `work_center`, parse the date and positive integer as now,
look up people, then call:

```python
block = rotation_store.create_block(
    trainee_id=trainee_id, trainer_id=trainer_id, work_center=work_center,
    start_day=start_day, planned_attended_days=workdays,
)
```

Change `_block_to_dict` to include `work_center` and `skill_ids`.
Keep the existing cache invalidation and lifecycle routes.

- [ ] **Step 4: Verify green**

Run: `pytest tests/test_staffing_rotations.py -k 'training_block or training_protocol or block_lifecycle' -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py
git commit -m "feat: accept exact work center training protocols"
```

### Task 5: Move the unified training UI to Staffing

**Files:**

- Modify: `src/zira_dashboard/routes/staffing.py:820-1010`
- Modify: `src/zira_dashboard/templates/staffing.html:20-35,210-230,300-410`
- Modify: `src/zira_dashboard/static/staffing.js:1-30,130-155,1369-1600`
- Modify: `src/zira_dashboard/static/staffing.css:450-465,640-725`
- Modify: `src/zira_dashboard/templates/skills.html:158-208`
- Modify: `src/zira_dashboard/static/skills-page.js:1128-1400`
- Modify: `src/zira_dashboard/static/skills.css:326-520`
- Test: `tests/test_staffing_rotations.py:1400-1470,2000-2060,2200-2290`

**Interfaces:**

- Staffing receives active protocols, active people, and configured work centers.
- Bootstrap `window.TRAINING_PROTOCOLS`, `window.TRAINING_PROTOCOL_PEOPLE`, and `window.TRAINING_PROTOCOL_WORK_CENTERS`.
- People Matrix retains scheduling preferences but no training-block form.

- [ ] **Step 1: Write failing UI contract tests**

```python
def test_staffing_exposes_unified_training_setup_and_removes_row_toggles():
    html = (ROOT / "src/zira_dashboard/templates/staffing.html").read_text()
    js = (ROOT / "src/zira_dashboard/static/staffing.js").read_text()
    assert 'id="training-protocol-open"' in html
    assert 'id="training-protocol-modal"' in html
    assert 'class="wc-training-cb"' not in html
    assert "setWcTraining" not in js
    assert "/api/rotations/training-blocks" in js


def test_people_matrix_no_longer_renders_training_block_form():
    html = (ROOT / "src/zira_dashboard/templates/skills.html").read_text()
    assert 'id="rotation-block-form"' not in html
    assert "Start Recycled level-0 training block" not in html
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_staffing_rotations.py -k 'unified_training_setup or no_longer_renders_training_block_form' -v`

Expected: FAIL because the old UI is still present.

- [ ] **Step 3: Render the accessible dialog and remove the old controls**

Add `<button id="training-protocol-open">+ Training</button>` in the Staffing
header. Add a dialog with labelled trainee, trainer, work-center, start-date,
and attended-days fields; an assertive error region; submit; and an active
protocol list with pause/resume/end controls. Remove `.wc-training-toggle`,
`.wc-training-cb`, `setWcTraining`, the visual-state training loop, and the
old hint. Delete the People Matrix block form, its bootstrap data, its JS, and
its CSS while retaining rotation-preference controls.

- [ ] **Step 4: Implement dialog request and lifecycle behavior**

```javascript
const { resp, data } = await postJSON('/api/rotations/training-blocks', {
  trainee: traineeSelect.value,
  trainer: trainerSelect.value,
  work_center: workCenterSelect.value,
  start_day: startInput.value,
  workdays: Number(workdaysInput.value),
});
if (!resp.ok || !data.ok) throw new Error(data.error || 'Could not start training.');
protocols.push(data.block);
renderTrainingProtocols();
```

Use the existing pause/resume/end endpoints. Do not disable the normal picker
after day one, so managers may manually add the trainer beside the trainee.

- [ ] **Step 5: Verify green**

Run: `pytest tests/test_staffing_rotations.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css src/zira_dashboard/templates/skills.html src/zira_dashboard/static/skills-page.js src/zira_dashboard/static/skills.css tests/test_staffing_rotations.py
git commit -m "feat: manage unified training protocols from staffing"
```

### Task 6: Document and verify the integrated feature

**Files:**

- Modify: `README.md:58-80`
- Modify: `CHANGELOG.md:1-30`
- Test: `tests/test_staffing_rotations.py`

- [ ] **Step 1: Write a failing documentation assertion**

```python
def test_readme_describes_exact_work_center_training_protocol():
    readme = (ROOT / "README.md").read_text()
    assert "exact work center" in readme
    assert "day one" in readme
    assert "level 3" in readme
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_staffing_rotations.py -k readme_describes_exact_work_center_training_protocol -v`

Expected: FAIL because README describes the old group-based Recycled block.

- [ ] **Step 3: Update concise operator copy**

Document clicking **+ Training** on Staffing; choosing trainee, trainer, work
center, start date, and attended days; the automatic day-one pair; later
manual trainer pairing; absence extension; and level-1 completion. Add a
CHANGELOG entry for removal of the per-row toggle.

- [ ] **Step 4: Run final verification**

Run: `pytest tests/test_rotation_store.py tests/test_rotation_training.py tests/test_rotation_suggestions.py tests/test_staffing_rotations.py -v`

Expected: PASS.

Run: `pytest -v`

Expected: PASS, with only environment-gated database tests skipped.

Run: `ruff check src tests`

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md tests/test_staffing_rotations.py
git commit -m "docs: explain unified training protocols"
```

## Plan self-review

- Spec coverage: Tasks 1–4 implement persistence, exact placement, validation, lifecycle, absences through existing planned-day logic, and all-skill promotion. Task 5 replaces the UI. Task 6 documents and verifies the user-visible workflow.
- Placeholder scan: no unresolved decisions, TODOs, or deferred implementation steps remain.
- Type consistency: `work_center` and `skill_ids` originate in Task 1 and are consumed consistently in Tasks 2–5.
