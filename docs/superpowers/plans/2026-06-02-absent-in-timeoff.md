# Declared-Absent → "Absent" in the Scheduler Time Off — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a manager declares someone absent, show them in the scheduler's Time Off panel as a full-day "Absent" entry (light red), pull them from Unscheduled and their work-center slot (display + headcount), and preserve the saved assignment so "undo absent" restores them.

**Architecture:** Three small changes. (1) `scheduler_time_off.time_off_entries_for_day` emits a full-day `manual_absent` "Absent" entry per declared-absent person (sourced from the existing `late_report.absent_names_for_day`), replacing any other entry for that person. The existing dormant hooks do the rest: a full-day entry already lands in `time_off_set` (excludes from pool/Unscheduled/Reserves) and `manual_absent: True` already triggers the `.absent` CSS, which is already light red. (2) A tiny pure helper `staffing.present_operators(assigned, off_names)` filters out full-day-off people. (3) The route uses it for the station summary + headcount + publish count, while the full `assigned` list still drives the picker and form save (so the assignment is preserved). Scope: station-slot removal applies to all full-day time off, not just Absent.

**Tech Stack:** FastAPI, Jinja2, psycopg2; pytest via `ZIRA_API_KEY=test .venv/bin/python -m pytest`.

Spec: [`docs/superpowers/specs/2026-06-02-absent-in-timeoff-design.md`](../specs/2026-06-02-absent-in-timeoff-design.md).

---

## Context the implementer needs

- **Declare-absent already exists**: the Late/Absence Report writes `manual_absences` (via `late_report.declare_absent`) and removes via `late_report.undo_absent`. `late_report.absent_names_for_day(day) -> set[str]` returns the declared-absent roster names for `day`, already filtered to active/non-excluded people. This plan does **not** touch the declare/undo flow.
- **Scheduler entry shape** (each item in `time_off_entries`): `{name, hours, pay_type, time_range, timing_label, derived, manual_absent, pending}`. `hours is None` ⇒ full-day. Template at [`staffing.html:85`](../../../src/zira_dashboard/templates/staffing.html) sets `is_absent = e.derived or e.manual_absent` → `.absent` CSS row class. `.timeoff .time-off-row.absent` ([`staffing.css:71`](../../../src/zira_dashboard/static/staffing.css)) is already light red.
- **Exclusion**: the route builds `full_day_entries = [e for e in time_off_entries if e.get("hours") is None]` then `time_off_set = {e["name"] for e in full_day_entries}` ([`routes/staffing.py:333`](../../../src/zira_dashboard/routes/staffing.py)). `time_off_set` already removes people from the assignable pool ([:496]), Unscheduled ([:573-576]) and Reserves ([:578]).
- **The gap**: a person already assigned to a WC still renders in that slot. The route builds `assigned` (list of `{name, level, color}`) per WC and `count = len(assigned)`; the template renders the slot from `visible_assigned = row.assigned | rejectattr('name','in', _attrib_names_for_row)` ([`staffing.html:234`](../../../src/zira_dashboard/templates/staffing.html)). Neither excludes time-off people.
- **Reason text**: plain `"Absent"` (per the request). Appending a captured reason is out of scope.
- **Tests run in the venv**: `ZIRA_API_KEY=test ZIRA_BASE_URL=http://localhost .venv/bin/python -m pytest`. `scheduler_time_off` and `staffing` import without FastAPI; route-render tests are heavy to stub, which is why the present-filter is a unit-testable helper.

## File structure

- **Modify** `src/zira_dashboard/scheduler_time_off.py` — `time_off_entries_for_day` emits Absent entries.
- **Modify** `src/zira_dashboard/staffing.py` — add `present_operators(assigned, off_names)` helper.
- **Modify** `src/zira_dashboard/routes/staffing.py` — per-WC `present_assigned`; base `count`/`hc_status` and the publish message on it; add `present_assigned` to the row dict.
- **Modify** `src/zira_dashboard/templates/staffing.html` — station summary renders from `row.present_assigned`.
- **Test** `tests/test_scheduler_time_off.py`, `tests/test_present_operators.py`.

---

### Task 1: `scheduler_time_off` emits full-day "Absent" entries

**Files:**
- Modify: `src/zira_dashboard/scheduler_time_off.py` (`time_off_entries_for_day`, ~lines 90-124)
- Test: `tests/test_scheduler_time_off.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduler_time_off.py  (append)
def test_declared_absent_becomes_full_day_absent_entry(monkeypatch):
    # Bob is on Odoo full-day PTO; Ana has an approved partial; Carl is only
    # declared-absent. Declaring Ana absent must override her partial.
    _fake_db(monkeypatch, [
        {"name": "Bob", "shape": "full_day", "hour_from": None, "hour_to": None,
         "state": "validate", "pay_type": "Paid Time Off"},
        {"name": "Ana", "shape": "late_arrival", "hour_from": 6.0, "hour_to": 9.0,
         "state": "validate", "pay_type": "Unpaid Time Off"},
    ])
    import zira_dashboard.late_report as lr
    monkeypatch.setattr(lr, "absent_names_for_day", lambda day: {"Ana", "Carl"})

    out = {e["name"]: e for e in sto.time_off_entries_for_day(date(2026, 6, 1))}

    # Bob unchanged (not absent)
    assert out["Bob"]["pay_type"] == "Paid Time Off"
    assert out["Bob"]["manual_absent"] is False
    # Ana: her partial is replaced by a full-day Absent entry
    assert out["Ana"]["hours"] is None
    assert out["Ana"]["pay_type"] == "Absent"
    assert out["Ana"]["timing_label"] == "Absent"
    assert out["Ana"]["manual_absent"] is True
    # Carl: declared absent, not in the Odoo feed -> new Absent entry
    assert out["Carl"]["manual_absent"] is True
    assert out["Carl"]["hours"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_scheduler_time_off.py::test_declared_absent_becomes_full_day_absent_entry -v`
Expected: FAIL — `KeyError: 'Carl'` (no Absent entries emitted yet) / Ana still partial.

- [ ] **Step 3: Write minimal implementation**

In `scheduler_time_off.py`, append to `time_off_entries_for_day` after the existing `cleared` filter block, before `return out`:

```python
    # Manually declared absences become full-day "Absent" entries (rendered
    # light red via the template's manual_absent -> .absent class). An absence
    # overrides any other entry for that person: drop theirs, add one Absent.
    from . import late_report
    try:
        absent = late_report.absent_names_for_day(day)
    except Exception:  # noqa: BLE001 — degrade to "no declared absences"
        absent = set()
    if absent:
        out = [e for e in out if e["name"] not in absent]
        for name in sorted(absent):
            out.append({
                "name": name,
                "hours": None,
                "pay_type": "Absent",
                "time_range": "",
                "timing_label": "Absent",
                "derived": False,
                "manual_absent": True,
                "pending": False,
            })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_scheduler_time_off.py -v`
Expected: PASS (new test + the existing 7).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/scheduler_time_off.py tests/test_scheduler_time_off.py
git commit -m "feat(scheduler): declared absences show as full-day Absent time-off entries"
```

---

### Task 2: `staffing.present_operators` helper

**Files:**
- Modify: `src/zira_dashboard/staffing.py` (add near the other small helpers, e.g. after `skill_color`)
- Test: `tests/test_present_operators.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_present_operators.py
from zira_dashboard import staffing


def test_present_operators_excludes_full_day_off():
    assigned = [{"name": "Ana", "level": 3}, {"name": "Bob", "level": 2}]
    assert staffing.present_operators(assigned, {"Bob"}) == [{"name": "Ana", "level": 3}]


def test_present_operators_empty_off_set_returns_all():
    assigned = [{"name": "Ana", "level": 3}]
    assert staffing.present_operators(assigned, set()) == assigned


def test_present_operators_all_off_returns_empty():
    assigned = [{"name": "Ana", "level": 3}]
    assert staffing.present_operators(assigned, {"Ana"}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_present_operators.py -v`
Expected: FAIL — `AttributeError: module 'zira_dashboard.staffing' has no attribute 'present_operators'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/zira_dashboard/staffing.py
def present_operators(assigned: list[dict], off_names) -> list[dict]:
    """The assigned operators actually present — i.e. not out for the full day.

    `assigned` is a list of {name, ...} dicts; `off_names` is the set of names
    with a full-day time-off/absent entry today. Used for the station summary
    and the headcount, while the full `assigned` list still drives the picker
    and the schedule save — so the assignment is preserved and undoing an
    absence restores the person to the slot.
    """
    off = set(off_names)
    return [a for a in assigned if a["name"] not in off]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_present_operators.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/staffing.py tests/test_present_operators.py
git commit -m "feat(scheduler): present_operators helper (assigned minus full-day off)"
```

---

### Task 3: Wire `present_assigned` into the route + template

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py` (the per-WC row loop + publish-block message)
- Modify: `src/zira_dashboard/templates/staffing.html:234`

- [ ] **Step 1: Compute `present_assigned` and base the headcount on it**

In `routes/staffing.py`, in the per-WC loop, right after the "currently-assigned safety net" finishes building `assigned`/`assigned_set` (just before `count = len(assigned)` at ~line 508), insert:

```python
        # Full-day-off / absent people stay assigned in the saved data (picker
        # checkbox + form input below), but are pulled from the station's
        # display and headcount so the slot reads as needing coverage.
        present_assigned = staffing.present_operators(assigned, time_off_set)
```

Change the headcount line from `count = len(assigned)` to:

```python
        count = len(present_assigned)
```

- [ ] **Step 2: Add `present_assigned` to the row dict**

In the same loop, in the `row = { ... }` dict (~line 522), add the key (next to `"assigned": assigned,`):

```python
            "present_assigned": present_assigned,
```

- [ ] **Step 3: Base the publish-block message on present count**

Change the publish-block reason (~line 555) from `len(r['assigned'])` to:

```python
                        f"{r['loc'].name} requires {r['min_ops']} operators — currently {len(r['present_assigned'])}."
```

- [ ] **Step 4: Render the station summary from `present_assigned`**

In `templates/staffing.html` line 234, change:

```jinja
          {% set visible_assigned = row.assigned | rejectattr('name', 'in', _attrib_names_for_row) | list %}
```
to:
```jinja
          {% set visible_assigned = row.present_assigned | rejectattr('name', 'in', _attrib_names_for_row) | list %}
```

- [ ] **Step 5: Verify — syntax, import, full suite**

```bash
ZIRA_API_KEY=test .venv/bin/python -m py_compile src/zira_dashboard/routes/staffing.py src/zira_dashboard/staffing.py
ZIRA_API_KEY=test ZIRA_BASE_URL=http://localhost .venv/bin/python -c "import zira_dashboard.routes.staffing"   # imports clean (FastAPI present in venv)
ZIRA_API_KEY=test ZIRA_BASE_URL=http://localhost .venv/bin/python -m pytest -q
```
Expected: py_compile OK; import OK; suite green (≥ prior pass count, 0 new failures). The `present_assigned` filter + template change carry no dedicated route test (full staffing render needs heavy stubs); they're covered by the `present_operators` unit test plus the suite staying green. Live-verify after deploy: declare someone absent who's assigned to a station → they vanish from that station and Unscheduled, appear in Time Off as "Absent" (light red), the station headcount drops by one; undo-absent restores them.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/templates/staffing.html
git commit -m "feat(scheduler): pull full-day-off/absent people from station slot + headcount"
```

---

## Self-Review

**Spec coverage:**
- Absent → full-day "Absent" entry, light red, in Time Off → Task 1 (+ dormant CSS/template, no change needed). ✓
- Excluded from pool/Unscheduled/Reserves → existing `time_off_set` (entry is full-day). ✓
- Removed from assigned station display + headcount + publish count → Tasks 2-3. ✓
- Assignment preserved / undo restores → Task 3 keeps `assigned`/`assigned_set` for the picker + form; `present_assigned` is display-only. ✓
- All full-day time off (not just Absent) for slot removal → `present_operators(assigned, time_off_set)` uses the full off-set. ✓
- Manual declares only; reason "Absent" → Task 1 sources `late_report.absent_names_for_day`, label "Absent". ✓

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `time_off_entries` item shape matches the existing dict (8 keys). `present_operators(assigned: list[dict], off_names) -> list[dict]` consumes the `assigned` shape (`{name, level, color}`) built in the route and is consumed as `row.present_assigned` in the template. `count`/publish message both use `present_assigned`. Consistent.
