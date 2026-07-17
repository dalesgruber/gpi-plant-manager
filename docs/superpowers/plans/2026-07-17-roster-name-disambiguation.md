# Roster Name Disambiguation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store a compact, unique `First Last-initial.` label for every synced Odoo employee so every roster display uses the same unambiguous name.

**Architecture:** Move name derivation out of `sync()` into a module-level pure helper that receives the full Odoo employee batch and returns labels by Odoo ID. Generate each label from the first two name tokens, then lengthen the second token only for collision groups; use later name tokens and an ID suffix only when the available surname letters cannot distinguish people. The existing sync upsert continues to write the resulting value to `people.name`, leaving all roster consumers unchanged.

**Tech Stack:** Python 3.11, pytest, FastAPI application modules, Postgres-backed Odoo sync.

## Global Constraints

- Source names come from `odoo_client.fetch_employees()`; `people.name` remains the shared display label for existing roster consumers.
- The normal label is `First L.`; lengthen only the abbreviated surname part when another employee would have the same label.
- Labels must be deterministic for an unchanged employee batch and unique within that batch.
- Preserve a one-word source name unchanged, because there is no surname to abbreviate.

---

### Task 1: Define the roster-label behavior with pure unit tests

**Files:**
- Modify: `tests/test_odoo_sync_unit.py`
- Test: `tests/test_odoo_sync_unit.py`

**Interfaces:**
- Consumes: `odoo_sync._roster_names(employees: list[dict]) -> dict[int, str]`.
- Produces: executable behavior examples for unique names, collision expansion, and a full-surname collision fallback.

- [ ] **Step 1: Write the failing tests**

```python
def test_roster_names_abbreviate_each_unique_last_name():
    labels = odoo_sync._roster_names([
        {"id": 1, "name": "Porfirio Cazares"},
        {"id": 2, "name": "Lauro Benitez"},
        {"id": 3, "name": "SingleName"},
    ])

    assert labels == {1: "Porfirio C.", 2: "Lauro B.", 3: "SingleName"}


def test_roster_names_expand_surname_only_for_matching_first_and_initial():
    labels = odoo_sync._roster_names([
        {"id": 1, "name": "Jesus Martinez"},
        {"id": 2, "name": "Jesus Morales"},
        {"id": 3, "name": "Carlos Jimenez"},
    ])

    assert labels == {1: "Jesus Ma.", 2: "Jesus Mo.", 3: "Carlos J."}


def test_roster_names_use_later_tokens_then_id_for_unresolved_collisions():
    labels = odoo_sync._roster_names([
        {"id": 7, "name": "Juan Garcia Lopez"},
        {"id": 8, "name": "Juan Garcia Martinez"},
        {"id": 9, "name": "Juan Garcia Lopez"},
    ])

    assert labels == {
        7: "Juan Garcia L. #7",
        8: "Juan Garcia M.",
        9: "Juan Garcia L. #9",
    }
```

- [ ] **Step 2: Run the unit test to verify it fails**

Run: `pytest tests/test_odoo_sync_unit.py -v`

Expected: FAIL because `odoo_sync._roster_names` does not exist.

### Task 2: Derive labels once per Odoo employee batch

**Files:**
- Modify: `src/zira_dashboard/odoo_sync.py:184-252`
- Test: `tests/test_odoo_sync_unit.py`

**Interfaces:**
- Consumes: `employees: list[dict]`, each with integer `id` and string `name`.
- Produces: `_roster_names(employees) -> dict[int, str]`, used by `sync()` as `labels[emp["id"]]`.

- [ ] **Step 1: Implement the minimal pure helper**

```python
def _roster_names(employees: list[dict]) -> dict[int, str]:
    parts_by_id = {
        int(emp["id"]): (emp.get("name") or "").strip().split()
        for emp in employees
    }
    surname_lengths = {
        employee_id: 1
        for employee_id, parts in parts_by_id.items()
        if len(parts) >= 2
    }

    def label(employee_id: int) -> str:
        parts = parts_by_id[employee_id]
        if len(parts) < 2:
            return " ".join(parts)
        surname = parts[1]
        tail = surname[:surname_lengths[employee_id]]
        return f"{parts[0]} {tail}."

    while True:
        groups: dict[str, list[int]] = {}
        for employee_id in surname_lengths:
            groups.setdefault(label(employee_id).casefold(), []).append(employee_id)
        expandable = [
            employee_id
            for group in groups.values() if len(group) > 1
            for employee_id in group
            if surname_lengths[employee_id] < len(parts_by_id[employee_id][1])
        ]
        if not expandable:
            break
        for employee_id in expandable:
            surname_lengths[employee_id] += 1

    labels = {employee_id: label(employee_id) for employee_id in parts_by_id}

    def collision_groups() -> list[list[int]]:
        groups: dict[str, list[int]] = {}
        for employee_id, display_name in labels.items():
            groups.setdefault(display_name.casefold(), []).append(employee_id)
        return [group for group in groups.values() if len(group) > 1]

    for group in collision_groups():
        for employee_id in group:
            parts = parts_by_id[employee_id]
            if len(parts) > 2:
                labels[employee_id] = " ".join([
                    f"{parts[0]} {parts[1][:surname_lengths[employee_id]]}",
                    *(f"{part[0]}." for part in parts[2:] if part),
                ])

    for group in collision_groups():
        for employee_id in group:
            labels[employee_id] = f"{labels[employee_id]} #{employee_id}"
    return labels
```

Replace the nested `_short_name()` function and calculate `roster_names = _roster_names(employees)` immediately before the database cursor. In the people upsert, replace `_short_name(emp["name"])` with `roster_names[emp["id"]]`.

- [ ] **Step 2: Run the targeted tests and make them pass**

Run: `pytest tests/test_odoo_sync_unit.py -v`

Expected: PASS with all roster-label and legacy-skill unit tests green.

- [ ] **Step 3: Run the relevant integration suite**

Run: `pytest tests/test_odoo_sync.py -v`

Expected: PASS when `DATABASE_URL` is configured; otherwise all tests are reported as skipped.

### Task 3: Verify the final change set

**Files:**
- Modify: `src/zira_dashboard/odoo_sync.py`
- Modify: `tests/test_odoo_sync_unit.py`
- Create: `docs/superpowers/specs/2026-07-17-roster-name-disambiguation-design.md`
- Create: `docs/superpowers/plans/2026-07-17-roster-name-disambiguation.md`

**Interfaces:**
- Consumes: the Task 1 behavior tests and Task 2 implementation.
- Produces: verified naming behavior ready for review.

- [ ] **Step 1: Run formatting and focused validation**

Run: `git diff --check && pytest tests/test_odoo_sync_unit.py tests/test_odoo_sync.py -v`

Expected: no whitespace errors; unit tests pass; Postgres integration tests pass or skip only because `DATABASE_URL` is absent.

- [ ] **Step 2: Review the diff for scope**

Run: `git diff -- src/zira_dashboard/odoo_sync.py tests/test_odoo_sync_unit.py docs/superpowers/specs/2026-07-17-roster-name-disambiguation-design.md docs/superpowers/plans/2026-07-17-roster-name-disambiguation.md`

Expected: only the canonical label derivation, its tests, and its approved design/plan documentation are changed.
