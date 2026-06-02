# Assign → Department Transfer + Testing Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a manager attributes sensed production to a person, automatically transfer that person to the work center's department in Odoo (reversible via Undo); and add a "Testing" button that carves a no-credit testing window out of the sensed window before assigning the real operator.

**Architecture:** All assign paths funnel through `POST /api/staffing/attribute`, so the department-transfer decision lives in one server-side helper (`staffing_transfer.decide_and_apply`) called from the endpoints. Testing segments are stored as `wc_time_attributions` rows with `source='testing'` and a sentinel person `'Testing'`; they are excluded from crediting as operators and their units are subtracted from the WC total in `production_history.attribution_for` — the single chokepoint feeding both live and precomputed numbers.

**Tech Stack:** Python 3.12, FastAPI, Postgres (`db.query`/`db.execute`), Odoo XML-RPC via `odoo_client.execute`, Jinja templates + vanilla JS, pytest with `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-06-02-assign-transfer-and-testing-design.md`

**Test run convention:** route/app-importing tests need `ZIRA_API_KEY` at import. Always run:
`ZIRA_API_KEY=test .venv/bin/python -m pytest <path> -v`

---

## File Structure

- **`src/zira_dashboard/wc_attributions.py`** (modify) — add `TESTING_PERSON` constant, exclude testing rows from `people_by_wc`, add `testing_windows_for_day`, make `unattributed_for_day` testing-aware.
- **`src/zira_dashboard/odoo_client.py`** (modify) — extend `get_current_attendance` to return the attendance's department id/name; add `undo_transfer`.
- **`src/zira_dashboard/staffing_transfer.py`** (create) — `decide_and_apply` decision logic + `_wc_department_label`.
- **`src/zira_dashboard/production_history.py`** (modify) — pure `_apply_testing_offsets`, `_fetch_wc_samples`, wire the carve-out into `attribution_for`.
- **`src/zira_dashboard/routes/staffing.py`** (modify) — call transfer from `/api/staffing/attribute`; add `/api/staffing/attribute-with-testing` and `/api/staffing/transfer/undo`.
- **`src/zira_dashboard/templates/_footer.html`** (modify) — Testing button + inline panel; transfer toast with Undo.
- **Tests:** `tests/test_wc_attributions_testing.py`, `tests/test_odoo_transfer_dept.py`, `tests/test_staffing_transfer.py`, `tests/test_production_history_testing.py`, `tests/test_staffing_attribute_endpoints.py` (create).

---

## Task 1: `wc_attributions` — testing rows excluded from credit, testing accessor, testing-aware to-do

**Files:**
- Modify: `src/zira_dashboard/wc_attributions.py`
- Test: `tests/test_wc_attributions_testing.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_wc_attributions_testing.py
"""Testing-segment handling in wc_attributions. for_day is monkeypatched so
no DB is needed."""
from __future__ import annotations

from datetime import datetime, timezone

from zira_dashboard import wc_attributions


def _row(wc, person, source, h_start, h_end, rid=1):
    return {
        "id": rid, "wc_name": wc, "person_name": person,
        "start_utc": datetime(2026, 6, 2, h_start, tzinfo=timezone.utc),
        "end_utc": datetime(2026, 6, 2, h_end, tzinfo=timezone.utc),
        "source": source,
    }


def test_people_by_wc_excludes_testing_rows(monkeypatch):
    rows = [
        _row("Junior #2", "Lauro", "manual", 14, 16, rid=1),
        _row("Junior #2", "Testing", "testing", 13, 14, rid=2),
    ]
    monkeypatch.setattr(wc_attributions, "for_day", lambda day: rows)
    out = wc_attributions.people_by_wc(object())
    assert out == {"Junior #2": ["Lauro"]}


def test_testing_windows_for_day_collects_only_testing(monkeypatch):
    rows = [
        _row("Junior #2", "Lauro", "manual", 14, 16, rid=1),
        _row("Junior #2", "Testing", "testing", 13, 14, rid=2),
        _row("Trim Saw 1", "Testing", "testing", 8, 9, rid=3),
    ]
    monkeypatch.setattr(wc_attributions, "for_day", lambda day: rows)
    out = wc_attributions.testing_windows_for_day(object())
    assert out == {
        "Junior #2": [(rows[1]["start_utc"], rows[1]["end_utc"])],
        "Trim Saw 1": [(rows[2]["start_utc"], rows[2]["end_utc"])],
    }


def test_testing_person_constant():
    assert wc_attributions.TESTING_PERSON == "Testing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_wc_attributions_testing.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'testing_windows_for_day'` / `TESTING_PERSON`.

- [ ] **Step 3: Add the constant and accessor, filter `people_by_wc`**

In `src/zira_dashboard/wc_attributions.py`, add near the top (after the imports):

```python
TESTING_PERSON = "Testing"
"""Sentinel person_name for ``source='testing'`` rows. These rows mark a
window whose production is credited to no one; they are never fed into
crediting as operators."""
```

Change `people_by_wc` to skip testing rows:

```python
def people_by_wc(day: date) -> dict[str, list[str]]:
    """Aggregated view: ``{wc_name: [person, ...]}`` -- convenience for joining
    into ``attribute_for_day``'s assignments dict. Excludes ``source='testing'``
    rows so a testing window never becomes a credited operator.

    Swallows DB errors (e.g. Postgres unreachable) so callers in hot paths
    like leaderboards keep working.
    """
    try:
        rows = for_day(day)
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    for r in rows:
        if r.get("source") == "testing":
            continue
        out.setdefault(r["wc_name"], []).append(r["person_name"])
    return out
```

Add the accessor below `people_by_wc`:

```python
def testing_windows_for_day(day: date) -> dict[str, list[tuple]]:
    """``{wc_name: [(start_utc, end_utc), ...]}`` for ``source='testing'``
    rows. Swallows DB errors like ``people_by_wc``."""
    try:
        rows = for_day(day)
    except Exception:
        return {}
    out: dict[str, list[tuple]] = {}
    for r in rows:
        if r.get("source") != "testing":
            continue
        out.setdefault(r["wc_name"], []).append((r["start_utc"], r["end_utc"]))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_wc_attributions_testing.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Make `unattributed_for_day` testing-aware**

In `unattributed_for_day`, change the line that builds `attributed_wcs` so a WC covered only by a testing window also drops off the to-do list:

```python
    attributed_wcs = (
        set(people_by_wc(day).keys())
        | set(testing_windows_for_day(day).keys())
    )
```

- [ ] **Step 6: Add a regression test for the to-do filter**

Append to `tests/test_wc_attributions_testing.py`:

```python
def test_unattributed_skips_testing_only_wc(monkeypatch):
    """A WC whose only attribution is a testing window must not appear as a
    pending to-do (it's been handled — credited to no one)."""
    from types import SimpleNamespace
    from zira_dashboard import staffing
    from zira_dashboard import leaderboard as lb_mod

    # One testing row for Junior #2, nothing else.
    rows = [_row("Junior #2", "Testing", "testing", 13, 14, rid=1)]
    monkeypatch.setattr(wc_attributions, "for_day", lambda day: rows)

    # Empty schedule so Junior #2 is unscheduled.
    monkeypatch.setattr(
        staffing, "load_schedule",
        lambda d: SimpleNamespace(assignments={}),
    )

    # Leaderboard reports production on Junior #2 above the fluke threshold.
    junior = next(loc for loc in staffing.LOCATIONS if loc.name == "Junior #2")
    result = SimpleNamespace(
        station=SimpleNamespace(meter_id=junior.meter_id, name="Junior #2"),
        units=40,
        active_intervals=(
            (datetime(2026, 6, 2, 13, tzinfo=timezone.utc),
             datetime(2026, 6, 2, 14, tzinfo=timezone.utc)),
        ),
    )
    monkeypatch.setattr(
        wc_attributions, "cached_leaderboard",
        lambda client, stations, day, now_utc=None: [result],
        raising=False,
    )

    from datetime import date
    out = wc_attributions.unattributed_for_day(date(2026, 6, 2), object())
    assert all(item["wc_name"] != "Junior #2" for item in out)
```

Note: `unattributed_for_day` imports `cached_leaderboard` as `leaderboard` locally. If the monkeypatch target above does not intercept it, change the import in `unattributed_for_day` to call `cached_leaderboard` through the module (e.g. `from . import leaderboard as _lb; results = _lb.cached_leaderboard(...)`) and patch `_lb.cached_leaderboard`. Pick whichever matches the existing code; do not leave the test patching a name that isn't used.

- [ ] **Step 7: Run the full file**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_wc_attributions_testing.py -v`
Expected: PASS (4 tests). Fix the monkeypatch target per the note if the 4th errors on patching.

- [ ] **Step 8: Commit**

```bash
git add src/zira_dashboard/wc_attributions.py tests/test_wc_attributions_testing.py
git commit -m "feat(assign): testing rows excluded from credit + testing-aware to-do"
```

---

## Task 2: `odoo_client` — department on current attendance + `undo_transfer`

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py:428` (`get_current_attendance`)
- Modify: `src/zira_dashboard/odoo_client.py` (add `undo_transfer` after `transfer`)
- Test: `tests/test_odoo_transfer_dept.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_odoo_transfer_dept.py
"""get_current_attendance department parsing + undo_transfer. odoo_client.execute
is stubbed; no Odoo needed."""
from __future__ import annotations

from unittest.mock import MagicMock

from zira_dashboard import odoo_client


def test_get_current_attendance_parses_department(monkeypatch):
    monkeypatch.setenv("ODOO_KIOSK_DEPARTMENT_FIELD", "x_kiosk_department_id")
    fake = MagicMock(return_value=[{
        "id": 55, "employee_id": [5, "Bob"], "check_in": "2026-06-02 13:00:00",
        "x_kiosk_department_id": [3, "01 Recycled"],
    }])
    monkeypatch.setattr(odoo_client, "execute", fake)

    row = odoo_client.get_current_attendance(5)

    _args, kwargs = fake.call_args
    assert "x_kiosk_department_id" in kwargs["fields"]
    assert row["department_id"] == 3
    assert row["department_name"] == "01 Recycled"


def test_get_current_attendance_no_dept_field(monkeypatch):
    monkeypatch.delenv("ODOO_KIOSK_DEPARTMENT_FIELD", raising=False)
    fake = MagicMock(return_value=[{
        "id": 55, "employee_id": [5, "Bob"], "check_in": "2026-06-02 13:00:00",
    }])
    monkeypatch.setattr(odoo_client, "execute", fake)

    row = odoo_client.get_current_attendance(5)
    assert row["department_id"] is None
    assert row["department_name"] is None


def test_get_current_attendance_none_when_clocked_out(monkeypatch):
    monkeypatch.setattr(odoo_client, "execute", MagicMock(return_value=[]))
    assert odoo_client.get_current_attendance(5) is None


def test_undo_transfer_unlinks_new_and_reopens_old(monkeypatch):
    fake = MagicMock(return_value=True)
    monkeypatch.setattr(odoo_client, "execute", fake)

    odoo_client.undo_transfer(closed_id=10, new_id=20)

    calls = [c.args for c in fake.call_args_list]
    assert ("hr.attendance", "unlink", [20]) in calls
    assert ("hr.attendance", "write", [10], {"check_out": False}) in calls


def test_undo_transfer_without_closed_id_only_unlinks(monkeypatch):
    fake = MagicMock(return_value=True)
    monkeypatch.setattr(odoo_client, "execute", fake)

    odoo_client.undo_transfer(closed_id=None, new_id=20)

    calls = [c.args for c in fake.call_args_list]
    assert ("hr.attendance", "unlink", [20]) in calls
    assert all(c[1] != "write" for c in calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_odoo_transfer_dept.py -v`
Expected: FAIL — `KeyError: 'department_id'` and `AttributeError: undo_transfer`.

- [ ] **Step 3: Extend `get_current_attendance`**

Replace `get_current_attendance` (currently at `odoo_client.py:428`) with:

```python
def get_current_attendance(employee_odoo_id: int) -> dict | None:
    """Return the open hr.attendance row for this employee (check_out IS
    NULL), or None if they're already clocked out. Most recent open
    attendance wins if there's somehow more than one.

    When ODOO_KIOSK_DEPARTMENT_FIELD is configured, the returned dict also
    carries ``department_id`` (int|None) and ``department_name`` (str|None)
    parsed from that Many2one, so callers can tell which department the
    person is currently punched into."""
    dept_field = _kiosk_department_field()
    fields = ["id", "employee_id", "check_in"]
    if dept_field:
        fields.append(dept_field)
    rows = execute(
        "hr.attendance", "search_read",
        [("employee_id", "=", employee_odoo_id), ("check_out", "=", False)],
        fields=fields,
        limit=1,
    )
    if not rows:
        return None
    row = rows[0]
    dept_val = row.get(dept_field) if dept_field else None
    if isinstance(dept_val, list) and dept_val:
        row["department_id"] = dept_val[0]
        row["department_name"] = dept_val[1] if len(dept_val) > 1 else None
    else:
        row["department_id"] = None
        row["department_name"] = None
    return row
```

- [ ] **Step 4: Add `undo_transfer`**

Immediately after the `transfer(...)` function (around `odoo_client.py:591`), add:

```python
def undo_transfer(closed_id: int | None, new_id: int) -> None:
    """Reverse a transfer: delete the newly opened attendance and reopen the
    previously closed one (clear its check_out). ``closed_id`` is None when the
    transfer actually opened a fresh punch (person had none) — then we only
    delete the new row."""
    execute("hr.attendance", "unlink", [new_id])
    if closed_id:
        execute("hr.attendance", "write", [closed_id], {"check_out": False})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_odoo_transfer_dept.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Run the existing odoo open-attendance tests for regressions**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_odoo_open_attendance.py -v`
Expected: PASS (the added `department_*` keys don't break existing assertions).

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/odoo_client.py tests/test_odoo_transfer_dept.py
git commit -m "feat(odoo): expose attendance department + undo_transfer"
```

---

## Task 3: `staffing_transfer.decide_and_apply` — the transfer decision

**Files:**
- Create: `src/zira_dashboard/staffing_transfer.py`
- Test: `tests/test_staffing_transfer.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_staffing_transfer.py
"""Department-transfer decision logic. staffing.load_roster and odoo_client are
stubbed; no DB / Odoo needed."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from zira_dashboard import staffing, staffing_transfer, odoo_client


WIN_START = datetime(2026, 6, 2, 13, 0, tzinfo=timezone.utc)


@pytest.fixture
def roster(monkeypatch):
    people = [
        staffing.Person(name="Lauro", employee_id=5),
        staffing.Person(name="Legacy Lou", employee_id=None),
    ]
    monkeypatch.setattr(staffing, "load_roster", lambda: people)
    return people


def test_skips_when_no_employee_id(roster, monkeypatch):
    monkeypatch.setattr(odoo_client, "get_current_attendance",
                        lambda eid: (_ for _ in ()).throw(AssertionError("must not call Odoo")))
    out = staffing_transfer.decide_and_apply("Legacy Lou", "Junior #2", WIN_START)
    assert out["transfer"] == "skipped_no_employee"


def test_no_op_when_already_in_dept(roster, monkeypatch):
    monkeypatch.setattr(odoo_client, "_department_id_for_wc", lambda wc: 9)
    monkeypatch.setattr(odoo_client, "get_current_attendance", lambda eid: {
        "id": 1, "check_in": "2026-06-02 12:00:00",
        "department_id": 9, "department_name": "07 New",
    })
    transfer_called = {"n": 0}
    monkeypatch.setattr(odoo_client, "transfer",
                        lambda *a, **k: transfer_called.__setitem__("n", 1) or (1, 2))
    out = staffing_transfer.decide_and_apply("Lauro", "Junior #2", WIN_START)
    assert out["transfer"] == "already_in_dept"
    assert transfer_called["n"] == 0


def test_transfers_when_dept_differs(roster, monkeypatch):
    monkeypatch.setattr(odoo_client, "_department_id_for_wc", lambda wc: 9)
    monkeypatch.setattr(odoo_client, "get_current_attendance", lambda eid: {
        "id": 1, "check_in": "2026-06-02 12:00:00",
        "department_id": 3, "department_name": "01 Recycled",
    })
    captured = {}
    def fake_transfer(eid, wc, ts):
        captured.update(eid=eid, wc=wc, ts=ts)
        return (100, 200)
    monkeypatch.setattr(odoo_client, "transfer", fake_transfer)

    out = staffing_transfer.decide_and_apply("Lauro", "Junior #2", WIN_START)

    assert out["transfer"] == "moved"
    assert out["closed_id"] == 100 and out["new_id"] == 200
    assert out["from_dept"] == "01 Recycled"
    assert out["to_dept"] == "New"  # Junior #2's Location.department
    # transfer_ts clamps to the later of window start and existing check_in;
    # check_in (12:00) is earlier, so window start (13:00) wins.
    assert captured["ts"] == WIN_START


def test_transfer_ts_clamps_to_checkin(roster, monkeypatch):
    monkeypatch.setattr(odoo_client, "_department_id_for_wc", lambda wc: 9)
    monkeypatch.setattr(odoo_client, "get_current_attendance", lambda eid: {
        "id": 1, "check_in": "2026-06-02 14:00:00",  # AFTER window start
        "department_id": 3, "department_name": "01 Recycled",
    })
    captured = {}
    monkeypatch.setattr(odoo_client, "transfer",
                        lambda eid, wc, ts: captured.update(ts=ts) or (1, 2))
    staffing_transfer.decide_and_apply("Lauro", "Junior #2", WIN_START)
    assert captured["ts"] == datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)


def test_opens_new_punch_when_none(roster, monkeypatch):
    monkeypatch.setattr(odoo_client, "_department_id_for_wc", lambda wc: 9)
    monkeypatch.setattr(odoo_client, "get_current_attendance", lambda eid: None)
    captured = {}
    monkeypatch.setattr(odoo_client, "clock_in",
                        lambda eid, wc, ts: captured.update(eid=eid, wc=wc, ts=ts) or 300)
    out = staffing_transfer.decide_and_apply("Lauro", "Junior #2", WIN_START)
    assert out["transfer"] == "opened"
    assert out["new_id"] == 300
    assert captured["ts"] == WIN_START
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_transfer.py -v`
Expected: FAIL — `ModuleNotFoundError: zira_dashboard.staffing_transfer`.

- [ ] **Step 3: Create the module**

```python
# src/zira_dashboard/staffing_transfer.py
"""Decide whether attributing production at a work center implies a department
transfer in Odoo, and apply it.

Called from the staffing attribute endpoints. One server-side chokepoint so
every assign path (footer modal + inline popovers) gets the same behavior.
"""

from __future__ import annotations

from datetime import datetime


def _wc_department_label(wc_name: str) -> str | None:
    """The human department label for a WC (e.g. 'New'), from staffing
    LOCATIONS. None if the WC is unknown."""
    from . import staffing
    for loc in staffing.LOCATIONS:
        if loc.name == wc_name:
            return loc.department
    return None


def _employee_id_for(person_name: str) -> int | None:
    from . import staffing
    for p in staffing.load_roster():
        if p.name == person_name:
            return p.employee_id
    return None


def decide_and_apply(
    person_name: str, wc_name: str, window_start_utc: datetime
) -> dict:
    """Transfer ``person_name`` to ``wc_name``'s department in Odoo if needed.

    Returns a dict describing what happened, suitable for the UI toast:
      {"transfer": "skipped_no_employee", "person"}
      {"transfer": "already_in_dept", "person", "to_dept"}
      {"transfer": "moved", "person", "closed_id", "new_id", "from_dept", "to_dept"}
      {"transfer": "opened", "person", "new_id", "to_dept"}

    Decision rules:
      * No Odoo employee id -> skip (legacy person).
      * transfer_ts = max(window_start_utc, current check_in) so we never
        close a punch before it opened.
      * Open punch already in the WC's department -> no-op.
      * Open punch in a different (or unknown) department -> transfer at ts.
      * No open punch -> open a fresh punch at the WC's department.
    """
    from . import odoo_client

    emp_id = _employee_id_for(person_name)
    to_dept = _wc_department_label(wc_name)
    if not emp_id:
        return {"transfer": "skipped_no_employee", "person": person_name}

    wc_dept_id = odoo_client._department_id_for_wc(wc_name)
    current = odoo_client.get_current_attendance(emp_id)

    if current is None:
        new_id = odoo_client.clock_in(emp_id, wc_name, window_start_utc)
        return {"transfer": "opened", "person": person_name,
                "new_id": new_id, "to_dept": to_dept}

    check_in_iso = odoo_client._odoo_dt_to_iso(current.get("check_in"))
    check_in_dt = datetime.fromisoformat(check_in_iso) if check_in_iso else None
    transfer_ts = (
        max(window_start_utc, check_in_dt) if check_in_dt else window_start_utc
    )

    cur_dept_id = current.get("department_id")
    if (cur_dept_id is not None and wc_dept_id is not None
            and cur_dept_id == wc_dept_id):
        return {"transfer": "already_in_dept", "person": person_name,
                "to_dept": to_dept}

    closed_id, new_id = odoo_client.transfer(emp_id, wc_name, transfer_ts)
    return {"transfer": "moved", "person": person_name,
            "closed_id": closed_id, "new_id": new_id,
            "from_dept": current.get("department_name"), "to_dept": to_dept}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_transfer.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/staffing_transfer.py tests/test_staffing_transfer.py
git commit -m "feat(assign): department-transfer decision (staffing_transfer)"
```

---

## Task 4: Crediting carve-out — subtract testing-window units

**Files:**
- Modify: `src/zira_dashboard/production_history.py` (add `_apply_testing_offsets`, `_fetch_wc_samples`; wire into `attribution_for`)
- Test: `tests/test_production_history_testing.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_production_history_testing.py
"""Testing-window carve-out in the crediting path. Pure helper tests plus an
attribution_for integration test with leaderboard + DB accessors stubbed."""
from __future__ import annotations

from datetime import date, datetime, timezone

from zira_dashboard import production_history


def _dt(h, m=0):
    return datetime(2026, 6, 2, h, m, tzinfo=timezone.utc)


def test_apply_testing_offsets_subtracts_in_window():
    wc_totals = {"Junior #2": (40, 5)}
    samples_by_wc = {"Junior #2": [(_dt(13, 10), 10), (_dt(13, 50), 5), (_dt(15, 0), 25)]}
    testing = {"Junior #2": [(_dt(13, 0), _dt(14, 0))]}
    out = production_history._apply_testing_offsets(wc_totals, samples_by_wc, testing)
    # 10 + 5 units fell inside 13:00-14:00; subtracted. Downtime untouched.
    assert out["Junior #2"] == (25, 5)


def test_apply_testing_offsets_floors_at_zero():
    wc_totals = {"Junior #2": (8, 0)}
    samples_by_wc = {"Junior #2": [(_dt(13, 10), 10)]}
    testing = {"Junior #2": [(_dt(13, 0), _dt(14, 0))]}
    out = production_history._apply_testing_offsets(wc_totals, samples_by_wc, testing)
    assert out["Junior #2"] == (0, 0)


def test_apply_testing_offsets_no_testing_returns_input():
    wc_totals = {"Junior #2": (40, 5)}
    assert production_history._apply_testing_offsets(wc_totals, {}, {}) == wc_totals


def test_attribution_for_excludes_testing_units(monkeypatch):
    from zira_dashboard import staffing, wc_attributions

    sched = staffing.Schedule(day=date(2026, 6, 2), published=True, assignments={})
    monkeypatch.setattr(staffing, "load_schedule", lambda d: sched)
    monkeypatch.setattr(production_history, "_fetch_wc_totals",
                        lambda client, day: {"Junior #2": (40, 0)})
    monkeypatch.setattr(production_history, "_fetch_wc_samples",
                        lambda client, day: {"Junior #2": [(_dt(13, 10), 15), (_dt(15, 0), 25)]})
    monkeypatch.setattr(production_history, "_elapsed_minutes_for", lambda d: 480)
    # Lauro is the remainder operator; Testing window 13:00-14:00 (15 units).
    monkeypatch.setattr(wc_attributions, "people_by_wc",
                        lambda d: {"Junior #2": ["Lauro"]})
    monkeypatch.setattr(wc_attributions, "testing_windows_for_day",
                        lambda d: {"Junior #2": [(_dt(13, 0), _dt(14, 0))]})

    out = production_history.attribution_for(date(2026, 6, 2), client=object())
    assert out["Lauro"]["Junior #2"]["units"] == 25.0  # 40 - 15 testing
    assert "Testing" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_production_history_testing.py -v`
Expected: FAIL — `AttributeError: _apply_testing_offsets` / `_fetch_wc_samples`.

- [ ] **Step 3: Add the pure helper and the samples fetcher**

In `src/zira_dashboard/production_history.py`, add after `_fetch_wc_totals` (around line 122):

```python
def _fetch_wc_samples(client, day: date) -> dict[str, list[tuple]]:
    """``{wc_name: [(event_dt_utc, units), ...]}`` for metered WCs on ``day``.
    Reuses the cached leaderboard (same call _fetch_wc_totals makes), so this
    is cheap when both run for the same day."""
    from . import staffing
    from .leaderboard import cached_leaderboard as leaderboard
    from .stations import Station

    metered = [loc for loc in staffing.LOCATIONS if loc.meter_id]
    if not metered:
        return {}
    stations = [
        Station(meter_id=loc.meter_id, name=loc.name, category=loc.skill, cell=loc.bay)
        for loc in metered
    ]
    results = leaderboard(client, stations, day)
    return {r.station.name: list(r.samples) for r in results}


def _apply_testing_offsets(
    wc_totals: dict[str, tuple[int, int]],
    samples_by_wc: dict[str, list[tuple]],
    testing_windows: dict[str, list[tuple]],
) -> dict[str, tuple[int, int]]:
    """Subtract units produced inside testing windows from each WC's total so
    they're credited to no one. Downtime is left untouched. Floors at 0."""
    if not testing_windows:
        return wc_totals
    out = dict(wc_totals)
    for wc, windows in testing_windows.items():
        if wc not in out:
            continue
        samples = samples_by_wc.get(wc, [])
        testing_units = sum(
            u for (t, u) in samples
            if any(s <= t < e for (s, e) in windows)
        )
        units, downtime = out[wc]
        out[wc] = (max(0, units - testing_units), downtime)
    return out
```

- [ ] **Step 4: Wire the carve-out into `attribution_for`**

In `attribution_for` (around line 151), after `wc_totals = _fetch_wc_totals(client, d)` and `extra = wc_attributions.people_by_wc(d)`, insert before the `return`:

```python
    testing = wc_attributions.testing_windows_for_day(d)
    if testing:
        samples_by_wc = _fetch_wc_samples(client, d)
        wc_totals = _apply_testing_offsets(wc_totals, samples_by_wc, testing)
```

The function tail stays:

```python
    return attribute_for_day(
        sched.assignments, wc_totals, elapsed, extra_assignments=extra
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_production_history_testing.py tests/test_production_history.py -v`
Expected: PASS (new file 4 tests + existing file unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/production_history.py tests/test_production_history_testing.py
git commit -m "feat(assign): subtract testing-window units from crediting"
```

---

## Task 5: Endpoints — transfer hook on attribute, attribute-with-testing, transfer/undo

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py` (`staffing_attribute` at line 866; add two new routes after it)
- Test: `tests/test_staffing_attribute_endpoints.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_staffing_attribute_endpoints.py
"""Endpoint tests for the attribute + testing + undo flow. wc_attributions,
staffing_transfer, and odoo_client are stubbed so no DB / Odoo is touched."""
from __future__ import annotations

from fastapi.testclient import TestClient

from zira_dashboard.app import app
from zira_dashboard import wc_attributions, staffing_transfer, odoo_client
from zira_dashboard.routes import staffing as staffing_routes

client = TestClient(app)


def test_attribute_returns_transfer_result(monkeypatch):
    monkeypatch.setattr(wc_attributions, "add", lambda *a, **k: 123)
    monkeypatch.setattr(staffing_transfer, "decide_and_apply",
                        lambda person, wc, ts: {"transfer": "moved", "person": person,
                                                "closed_id": 1, "new_id": 2,
                                                "from_dept": "01 Recycled", "to_dept": "New"})
    monkeypatch.setattr(staffing_routes, "invalidate_today_cache", lambda: None, raising=False)

    resp = client.post("/api/staffing/attribute", json={
        "day": "2026-06-02", "wc_name": "Junior #2", "person_name": "Lauro",
        "start_utc": "2026-06-02T13:00:00+00:00", "end_utc": "2026-06-02T16:00:00+00:00",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["id"] == 123
    assert body["transfer"]["transfer"] == "moved"


def test_attribute_with_testing_writes_two_rows_and_transfers(monkeypatch):
    added = []
    monkeypatch.setattr(wc_attributions, "add",
                        lambda day, wc, person, s, e, source="manual": added.append(
                            (wc, person, source)) or len(added))
    captured = {}
    monkeypatch.setattr(staffing_transfer, "decide_and_apply",
                        lambda person, wc, ts: captured.update(person=person, ts=ts)
                        or {"transfer": "moved", "person": person, "closed_id": 1,
                            "new_id": 2, "to_dept": "New"})
    monkeypatch.setattr(staffing_routes, "invalidate_today_cache", lambda: None, raising=False)

    resp = client.post("/api/staffing/attribute-with-testing", json={
        "day": "2026-06-02", "wc_name": "Junior #2",
        "testing_start_utc": "2026-06-02T13:00:00+00:00",
        "testing_end_utc": "2026-06-02T14:00:00+00:00",
        "sensed_end_utc": "2026-06-02T16:00:00+00:00",
        "remainder_person": "Lauro",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    # First row testing, second row the remainder operator.
    assert ("Junior #2", wc_attributions.TESTING_PERSON, "testing") in added
    assert ("Junior #2", "Lauro", "manual") in added
    # Remainder transfer anchored to testing end (when real work began).
    from datetime import datetime, timezone
    assert captured["ts"] == datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)
    assert body["transfer"]["transfer"] == "moved"


def test_attribute_with_testing_testing_only(monkeypatch):
    added = []
    monkeypatch.setattr(wc_attributions, "add",
                        lambda day, wc, person, s, e, source="manual": added.append(
                            (wc, person, source)) or len(added))
    called = {"n": 0}
    monkeypatch.setattr(staffing_transfer, "decide_and_apply",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {})
    monkeypatch.setattr(staffing_routes, "invalidate_today_cache", lambda: None, raising=False)

    resp = client.post("/api/staffing/attribute-with-testing", json={
        "day": "2026-06-02", "wc_name": "Junior #2",
        "testing_start_utc": "2026-06-02T13:00:00+00:00",
        "testing_end_utc": "2026-06-02T16:00:00+00:00",
    })
    assert resp.status_code == 200
    assert added == [("Junior #2", wc_attributions.TESTING_PERSON, "testing")]
    assert called["n"] == 0  # no remainder person -> no transfer
    assert resp.json()["transfer"] == {"transfer": "none"}


def test_attribute_with_testing_rejects_bad_window(monkeypatch):
    monkeypatch.setattr(wc_attributions, "add", lambda *a, **k: 1)
    resp = client.post("/api/staffing/attribute-with-testing", json={
        "day": "2026-06-02", "wc_name": "Junior #2",
        "testing_start_utc": "2026-06-02T15:00:00+00:00",
        "testing_end_utc": "2026-06-02T14:00:00+00:00",  # end before start
    })
    assert resp.status_code == 400


def test_transfer_undo_calls_odoo(monkeypatch):
    captured = {}
    monkeypatch.setattr(odoo_client, "undo_transfer",
                        lambda closed_id, new_id: captured.update(closed_id=closed_id, new_id=new_id))
    monkeypatch.setattr(staffing_routes, "invalidate_today_cache", lambda: None, raising=False)

    resp = client.post("/api/staffing/transfer/undo", json={"closed_id": 1, "new_id": 2})
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert captured == {"closed_id": 1, "new_id": 2}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_attribute_endpoints.py -v`
Expected: FAIL — `/api/staffing/attribute` has no `transfer` key; the two new routes 404.

- [ ] **Step 3: Add the transfer hook to `staffing_attribute`**

In `src/zira_dashboard/routes/staffing.py`, replace the body of `staffing_attribute` after `new_id = wc_attributions.add(...)` (line 890) with:

```python
    new_id = wc_attributions.add(day, wc, person, start_utc, end_utc)
    # Department transfer side-effect: if the person physically moved to this
    # WC's department, reflect it in Odoo. Never let an Odoo hiccup fail the
    # attribution write — the credit is the source of truth.
    from .. import staffing_transfer
    try:
        transfer = staffing_transfer.decide_and_apply(person, wc, start_utc)
    except Exception as e:  # noqa: BLE001 — surface, don't fail the attribution
        transfer = {"transfer": "error", "error": str(e)}
    # Drop cached dashboard responses so the next load reflects the change.
    from .._http_cache import invalidate_today_cache
    invalidate_today_cache()
    return JSONResponse({"ok": True, "id": new_id, "transfer": transfer})
```

- [ ] **Step 4: Add the two new routes**

In `src/zira_dashboard/routes/staffing.py`, immediately after `staffing_attribute_delete` (line 907), add:

```python
@router.post("/api/staffing/attribute-with-testing")
async def staffing_attribute_with_testing(request: Request):
    """Carve a no-credit testing window out of sensed production, then
    optionally attribute the remainder to a real person.

    Body (JSON):
      day:                ISO date
      wc_name:            work center name
      testing_start_utc:  ISO datetime (UTC)
      testing_end_utc:    ISO datetime (UTC)   (> testing_start_utc)
      sensed_end_utc:     ISO datetime (UTC)   (optional; remainder row end,
                          defaults to testing_end_utc)
      remainder_person:   person to credit for the post-testing window (optional)
    """
    from datetime import date as _date, datetime as _dt
    from .. import wc_attributions, staffing_transfer
    body = await request.json()
    try:
        day = _date.fromisoformat(body["day"])
        wc = str(body["wc_name"]).strip()
        t_start = _dt.fromisoformat(body["testing_start_utc"])
        t_end = _dt.fromisoformat(body["testing_end_utc"])
    except (KeyError, TypeError, ValueError) as e:
        return JSONResponse({"ok": False, "error": f"bad body: {e}"}, status_code=400)
    if not wc or t_end <= t_start:
        return JSONResponse({"ok": False, "error": "missing/invalid fields"}, status_code=400)

    ids: list[int] = []
    ids.append(wc_attributions.add(
        day, wc, wc_attributions.TESTING_PERSON, t_start, t_end, source="testing"))

    transfer = {"transfer": "none"}
    remainder = str(body.get("remainder_person") or "").strip()
    if remainder:
        try:
            rem_end = _dt.fromisoformat(body["sensed_end_utc"])
        except (KeyError, TypeError, ValueError):
            rem_end = t_end
        if rem_end <= t_end:
            rem_end = t_end
        ids.append(wc_attributions.add(day, wc, remainder, t_end, rem_end))
        try:
            transfer = staffing_transfer.decide_and_apply(remainder, wc, t_end)
        except Exception as e:  # noqa: BLE001
            transfer = {"transfer": "error", "error": str(e)}

    from .._http_cache import invalidate_today_cache
    invalidate_today_cache()
    return JSONResponse({"ok": True, "ids": ids, "transfer": transfer})


@router.post("/api/staffing/transfer/undo")
async def staffing_transfer_undo(request: Request):
    """Reverse an Odoo department transfer created by an assignment.

    Body (JSON): {closed_id: int|null, new_id: int}.
    """
    from .. import odoo_client
    body = await request.json()
    try:
        new_id = int(body["new_id"])
    except (KeyError, TypeError, ValueError) as e:
        return JSONResponse({"ok": False, "error": f"bad body: {e}"}, status_code=400)
    closed_id = body.get("closed_id")
    closed_id = int(closed_id) if closed_id else None
    try:
        odoo_client.undo_transfer(closed_id, new_id)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    from .._http_cache import invalidate_today_cache
    invalidate_today_cache()
    return JSONResponse({"ok": True})
```

Note: the endpoint tests monkeypatch `staffing_routes.invalidate_today_cache`, but the handlers import it locally (`from .._http_cache import invalidate_today_cache`). For the monkeypatch to bite, add a module-level import at the top of `routes/staffing.py` if not already present — `from .._http_cache import invalidate_today_cache` — and call the bare name inside the handlers instead of re-importing. Check the file: if `invalidate_today_cache` is already imported at module scope, just use it. Otherwise add the module-level import and drop the local `from .._http_cache import ...` lines in these three handlers.

- [ ] **Step 5: Run tests to verify they pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_attribute_endpoints.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Run the broader staffing route tests for regressions**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/ -k "staffing" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py tests/test_staffing_attribute_endpoints.py
git commit -m "feat(assign): attribute transfer hook + testing/undo endpoints"
```

---

## Task 6: Footer modal — Testing button, testing panel, transfer toast + Undo

**Files:**
- Modify: `src/zira_dashboard/templates/_footer.html` (the "Assignments to Do" block: `renderModal` ~line 403 and `wireBodyHandlers` ~line 441)

This task is browser JS in a server-rendered template; the repo has no JS test harness, so it's verified by running the app. Keep each edit small.

- [ ] **Step 1: Add a Testing button + hidden panel to each to-do item**

In `renderModal`, inside the `d.items.forEach` loop, change the action line that currently ends the item. Replace:

```javascript
        html += '</select> <button type="button" class="atd-save">Save</button>';
        html += '<span class="atd-status" hidden></span></div></li>';
```

with:

```javascript
        html += '</select> <button type="button" class="atd-save">Save</button>';
        html += ' <button type="button" class="atd-testing-btn">Testing</button>';
        html += '<span class="atd-status" hidden></span></div>';
        // Hidden testing panel — start/end prefilled with the sensed window.
        html += '<div class="atd-testing-panel" hidden>';
        html += '<label>Testing from <input type="time" class="atd-test-start" value="' + to24h(item.first_label) + '"></label>';
        html += '<label>to <input type="time" class="atd-test-end" value="' + to24h(item.last_label) + '"></label>';
        html += '<div class="atd-test-remainder"><label>Who worked after testing? '
              + '<select class="atd-test-person"><option value="">— no one (all testing) —</option>';
        d.people.forEach(function (n) {
          html += '<option value="' + escapeHtml(n) + '">' + escapeHtml(n) + '</option>';
        });
        html += '</select></label></div>';
        html += '<button type="button" class="atd-test-confirm">Confirm testing</button>';
        html += '<span class="atd-test-status" hidden></span></div>';
        html += '</li>';
```

- [ ] **Step 2: Add the `to24h` helper**

Above `renderModal` (next to `escapeHtml`), add a helper that turns the server's `"1:05 PM"` labels into a `<input type="time">` `"HH:MM"` value:

```javascript
  function to24h(label) {
    // label like "1:05 PM" / "8:50 AM" -> "13:05" / "08:50"
    var m = /^(\d{1,2}):(\d{2})\s*(AM|PM)$/i.exec(String(label || '').trim());
    if (!m) return '';
    var h = parseInt(m[1], 10) % 12;
    if (/pm/i.test(m[3])) h += 12;
    return (h < 10 ? '0' : '') + h + ':' + m[2];
  }
```

- [ ] **Step 3: Add a local-time → UTC ISO helper**

Also above `renderModal`, add (the plant kiosks/managers run in plant-local time, so browser-local is the site clock):

```javascript
  function localTimeToIso(dayIso, hhmm) {
    // dayIso "2026-06-02", hhmm "13:05" -> UTC ISO using the browser's local tz.
    var p = hhmm.split(':');
    var dp = dayIso.split('-');
    var dt = new Date(parseInt(dp[0], 10), parseInt(dp[1], 10) - 1,
                      parseInt(dp[2], 10), parseInt(p[0], 10), parseInt(p[1], 10), 0, 0);
    return dt.toISOString();
  }
```

- [ ] **Step 4: Wire the Testing button + Confirm in `wireBodyHandlers`**

At the end of `wireBodyHandlers` (before its closing `}`), add:

```javascript
    modal.querySelectorAll('.atd-testing-btn').forEach(function (b) {
      b.addEventListener('click', function () {
        var li = b.closest('.atd-item');
        var panel = li.querySelector('.atd-testing-panel');
        panel.hidden = !panel.hidden;
      });
    });
    modal.querySelectorAll('.atd-test-confirm').forEach(function (b) {
      b.addEventListener('click', function () {
        var li = b.closest('.atd-item');
        var startV = li.querySelector('.atd-test-start').value;
        var endV = li.querySelector('.atd-test-end').value;
        var person = li.querySelector('.atd-test-person').value;
        var status = li.querySelector('.atd-test-status');
        if (!startV || !endV || endV <= startV) {
          status.hidden = false; status.textContent = 'Testing end must be after start.'; return;
        }
        b.disabled = true; status.hidden = true;
        fetch('/api/staffing/attribute-with-testing', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            day: li.dataset.day, wc_name: li.dataset.wc,
            testing_start_utc: localTimeToIso(li.dataset.day, startV),
            testing_end_utc: localTimeToIso(li.dataset.day, endV),
            sensed_end_utc: li.dataset.end,
            remainder_person: person || null,
          }),
        }).then(function (r) { return r.json(); }).then(function (resp) {
          if (resp.ok) {
            status.hidden = false;
            status.textContent = person ? ('Saved ✓ testing + ' + person) : 'Saved ✓ testing';
            maybeTransferToast(resp.transfer);
            setTimeout(function () { location.reload(); }, 800);
          } else {
            b.disabled = false; status.hidden = false;
            status.textContent = 'Failed: ' + (resp.error || 'unknown');
          }
        }).catch(function () {
          b.disabled = false; status.hidden = false; status.textContent = 'Network error.';
        });
      });
    });
```

- [ ] **Step 5: Show the transfer toast after a normal Save**

In the existing `.atd-save` handler, in the `if (resp.ok)` branch (after setting the "Saved ✓" status, before the `setTimeout(... location.reload ...)`), add:

```javascript
            maybeTransferToast(resp.transfer);
```

- [ ] **Step 6: Add the `maybeTransferToast` function + Undo**

Inside the same IIFE (above `refreshCount`'s `setTimeout` kickoff), add:

```javascript
  function maybeTransferToast(t) {
    if (!t || (t.transfer !== 'moved' && t.transfer !== 'opened')) return;
    var toast = document.createElement('div');
    toast.className = 'atd-transfer-toast';
    var verb = t.transfer === 'opened' ? 'Clocked in' : 'Transferred';
    toast.innerHTML = escapeHtml(verb + ' ' + (t.person || '') + ' → ' + (t.to_dept || ''))
      + ' <button type="button" class="atd-transfer-undo">Undo</button>';
    document.body.appendChild(toast);
    toast.querySelector('.atd-transfer-undo').addEventListener('click', function () {
      fetch('/api/staffing/transfer/undo', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({closed_id: t.closed_id || null, new_id: t.new_id}),
      }).then(function (r) { return r.json(); }).then(function () {
        toast.textContent = 'Transfer undone.';
        setTimeout(function () { toast.remove(); }, 1500);
      });
    });
    setTimeout(function () { if (toast.parentNode) toast.remove(); }, 8000);
  }
```

- [ ] **Step 7: Add minimal styling**

In the `<style>` block of `_footer.html` (near the `.assign-todo-nav-badge` rules), add:

```css
  .atd-testing-panel { margin-top: 6px; padding: 8px; background: #f8f8f8; border-radius: 6px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .atd-testing-panel label { font-size: 0.85rem; }
  .atd-test-remainder { flex-basis: 100%; }
  .atd-transfer-toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: #1f2937; color: #fff; padding: 10px 16px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.25); z-index: 10000; font-size: 0.9rem; }
  .atd-transfer-toast .atd-transfer-undo { margin-left: 10px; background: #f59e0b; color: #1f2937; border: 0; border-radius: 4px; padding: 3px 8px; cursor: pointer; }
```

- [ ] **Step 8: Manual verification**

Run the app and exercise the modal:

```bash
ZIRA_API_KEY=test .venv/bin/python -m uvicorn zira_dashboard.app:app --port 8010
```

(Or use the project `run`/`run_dashboard.bat` path.) Verify:
1. Open any page → "Assignments to Do" badge → modal lists a to-do item with **Save** and **Testing** buttons.
2. Click **Testing** → panel expands with start/end prefilled to the sensed window and a "Who worked after testing?" picker.
3. Confirm with bad window (end ≤ start) → inline error, no request.
4. Confirm with a remainder person → success status; if a transfer fired, a toast appears with **Undo**; **Undo** posts and confirms.
5. A normal **Save** that triggers a transfer also shows the toast.

(If a live Odoo/DB isn't available locally, confirm requests are well-formed via the browser Network tab and that the endpoints return 200 with the expected JSON shape.)

- [ ] **Step 9: Commit**

```bash
git add src/zira_dashboard/templates/_footer.html
git commit -m "feat(assign): Testing button + panel and transfer toast/undo in to-do modal"
```

---

## Task 7: Full suite + changelog

- [ ] **Step 1: Run the whole suite**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest -q`
Expected: PASS (DATABASE_URL/Odoo-gated tests skip as usual).

- [ ] **Step 2: Update CHANGELOG.md**

Add a dated entry at the top of `CHANGELOG.md` summarizing: auto department-transfer on assign (with Undo); Testing window button in the Assignments-to-Do modal that credits testing units to no one and assigns the remainder operator.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): assign-time dept transfer + testing window"
```

---

## Self-Review Notes (for the implementer)

- **`unattributed_for_day` patch target (Task 1, Step 6):** verify how the function references `cached_leaderboard` and patch the name it actually calls. Don't ship a test that patches a dead name.
- **`invalidate_today_cache` (Task 5, Step 4):** confirm whether it's already module-imported in `routes/staffing.py`. The endpoint tests assume a module-level name; align the handlers to use it.
- **`get_current_attendance` new keys (Task 2):** `transfer()` calls this and only uses `current["id"]` — adding keys is safe. Confirm no caller does a strict dict-equality assertion on its result (grep `get_current_attendance` in tests).
- **Browser tz assumption (Task 6):** `localTimeToIso` treats input times as browser-local, which equals plant-local on the on-site kiosks/managers. Documented in the spec's edge cases.
