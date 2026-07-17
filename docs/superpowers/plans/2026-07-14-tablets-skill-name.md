# Tablets Scheduler Skill Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the automatic scheduler recognize the Odoo-synced `Tablets` skill for the Tablets work center.

**Architecture:** The static `staffing.LOCATIONS` definition is the source of scheduler qualification requirements. Change its Tablets entry to the canonical roster skill name and lock the contract down through the existing scheduling-target unit tests.

**Tech Stack:** Python 3.12, pytest, FastAPI application domain models.

## Global Constraints

- Change only the static Tablets requirement; do not mutate production roster skills, saved defaults, or schedules.
- Keep Work Orders’ `Mechanic` requirement unchanged.
- Use a test-first red-green cycle.

---

### Task 1: Align Tablets qualification with the synced roster skill

**Files:**
- Modify: `tests/test_rotation_store.py:24-44`
- Modify: `src/zira_dashboard/staffing.py:110`

**Interfaces:**
- Consumes: `staffing.eligible_scheduling_preference_targets(person)` and `staffing.Person.skills`.
- Produces: a `Tablets` scheduling target requiring the canonical `Tablets` skill.

- [ ] **Step 1: Write the failing test**

Add this test after `test_eligible_targets_require_every_required_skill`:

```python
def test_tablets_skill_qualifies_for_tablets_scheduling_target():
    from zira_dashboard import staffing

    person = staffing.Person("Tablets Operator", skills={"Tablets": 1})

    targets = {
        target.key: target
        for target in staffing.eligible_scheduling_preference_targets(person)
    }

    assert targets["Tablets"].required_skills == ("Tablets",)
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest tests/test_rotation_store.py::test_tablets_skill_qualifies_for_tablets_scheduling_target -v`

Expected: FAIL because the current Tablets work-center definition requires `Forklift: Tablets` and the target is absent.

- [ ] **Step 3: Apply the minimal production correction**

In `src/zira_dashboard/staffing.py`, change the Tablets location’s legacy skill field:

```python
Location("Tablets", "Tablets", "Forklift", "Supervisor", None, min_ops=1, max_ops=None),
```

- [ ] **Step 4: Re-run the focused test and verify it passes**

Run: `pytest tests/test_rotation_store.py::test_tablets_skill_qualifies_for_tablets_scheduling_target -v`

Expected: PASS.

- [ ] **Step 5: Run the relevant regression suite**

Run: `pytest tests/test_rotation_store.py tests/test_staffing_rotations.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the implementation**

```bash
git add src/zira_dashboard/staffing.py tests/test_rotation_store.py
git commit -m "fix: align tablets scheduler skill name"
```
