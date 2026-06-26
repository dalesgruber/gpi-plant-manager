# Exception Inbox Undo (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a short-window Undo to the Exception Inbox for the four cleanly-reversible actions (missing-WC Assign, missing-WC Dismiss, Mark-absent, Late-reason), so a misclick is a non-event.

**Architecture:** A resolve action that is undoable returns its `inbox_events` id in the response; the inbox row then shows a brief "Undo" affordance. `POST /api/exceptions/undo/{event_id}` loads the event, reverses it by `(item_kind, action)` — clearing the Odoo work center / refusing the absence leave / deleting the local suppression row — writes an `undo` event, and marks the original undone. Everything undo needs is already in the event's `item_key` (attendance id, or emp+day). Approve/Deny/Correct are deliberately NOT undoable (Odoo has no clean reverse) and keep `reversible=False`.

**Tech Stack:** Python 3.12, FastAPI, psycopg2 + Postgres, vanilla JS, pytest. Builds on Phases 1–2.

**Scope (decided):** Undoable = `missing_wc:assign`, `missing_wc:dismiss`, `late:absent`, `late:reason`. NOT undoable = time-off `approve`/`deny`, missed-punch `correct`, assignment `assign` (the staffing credit), `auto_resolved`.

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `src/zira_dashboard/inbox_log.py` | Activity log | **Modify** — add `get_event(event_id)` + `mark_undone(event_id, undo_event_id)` |
| `src/zira_dashboard/missing_wc.py` | Missing-WC suppression | **Modify** — add `unresolve(attendance_id)` |
| `src/zira_dashboard/late_report.py` | Late state | **Modify** — add `undo_late_arrival(day, emp_id)` |
| `src/zira_dashboard/routes/missing_wc.py` | assign/dismiss handlers | **Modify** — return `event_id` |
| `src/zira_dashboard/routes/late_report.py` | absent/reason handlers | **Modify** — `reason` reversible=True; return `event_id` |
| `src/zira_dashboard/routes/exceptions.py` | inbox routes | **Modify** — time-off log `reversible=False`; add `POST /api/exceptions/undo/{event_id}` + `_reverse_event` |
| `src/zira_dashboard/routes/missed_punch_out.py` | correct handler | **Modify** — log `reversible=False` |
| `src/zira_dashboard/routes/staffing.py` | assignment credit | **Modify** — log `reversible=False` |
| `src/zira_dashboard/static/exceptions.js` | client | **Modify** — Undo affordance on undoable resolves |
| `tests/test_inbox_undo.py` | undo endpoint + helpers | **Create** |

---

### Task 1: `inbox_log.get_event` + `mark_undone`

**Files:** Modify `src/zira_dashboard/inbox_log.py`; create `tests/test_inbox_undo.py`.

- [ ] **Step 1: Write the failing tests (DB-gated)**

Create `tests/test_inbox_undo.py`:

```python
"""Undo: load + mark-undone helpers and the reverse endpoint."""
import os

import pytest

from zira_dashboard import db, inbox_log

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs Postgres")

KP = "test:undo:"


@pytest.fixture(autouse=True)
def _clean():
    db.bootstrap_schema()
    db.execute("DELETE FROM inbox_events WHERE item_key LIKE %s", (KP + "%",))
    yield
    db.execute("DELETE FROM inbox_events WHERE item_key LIKE %s", (KP + "%",))


def test_get_event_and_mark_undone():
    eid = inbox_log.record_event(
        item_kind="missing_wc", item_key=KP + "1", person_name="Maria",
        category_label="Missing WC", action="dismiss",
        actor_upn="dale@gruberpallets.com", actor_name="Dale", reversible=True)
    ev = inbox_log.get_event(eid)
    assert ev is not None
    assert ev["item_key"] == KP + "1"
    assert ev["action"] == "dismiss"
    assert ev["undone_at"] is None

    undo_id = inbox_log.record_event(
        item_kind="missing_wc", item_key=KP + "1", person_name="Maria",
        category_label="Missing WC", action="undo", actor_upn="dale@gruberpallets.com",
        actor_name="Dale")
    inbox_log.mark_undone(eid, undo_id)
    ev2 = inbox_log.get_event(eid)
    assert ev2["undone_at"] is not None
    assert ev2["undo_event_id"] == undo_id


def test_get_event_missing_returns_none():
    assert inbox_log.get_event(-1) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `DATABASE_URL=${DATABASE_URL:-} ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_inbox_undo.py -v`
Expected: skips locally (no DATABASE_URL); with a test Postgres FAILS (`get_event`/`mark_undone` missing).

- [ ] **Step 3: Add the helpers to `inbox_log.py`**

Add after `archive()`:

```python
def get_event(event_id: int) -> dict[str, Any] | None:
    """One event row by id, or None."""
    rows = db.query(
        "SELECT id, item_kind, item_key, person_name, category_label, action, "
        "outcome, before_value, after_value, reason, actor_upn, actor_name, "
        "source, reversible, undone_at, undo_event_id, resolved_at "
        "FROM inbox_events WHERE id = %s",
        (event_id,),
    )
    return rows[0] if rows else None


def mark_undone(event_id: int, undo_event_id: int | None) -> None:
    """Stamp an event as undone, pointing at the undo event."""
    db.execute(
        "UPDATE inbox_events SET undone_at = now(), undo_event_id = %s WHERE id = %s",
        (undo_event_id, event_id),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run (with test Postgres): `DATABASE_URL=$DATABASE_URL ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_inbox_undo.py -v`
Expected: PASS (skips without DATABASE_URL).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/inbox_log.py tests/test_inbox_undo.py
git commit -m "feat(inbox): add inbox_log.get_event + mark_undone for undo

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Un-resolve helpers (`missing_wc.unresolve`, `late_report.undo_late_arrival`)

**Files:** Modify `src/zira_dashboard/missing_wc.py`; modify `src/zira_dashboard/late_report.py`; append to `tests/test_inbox_undo.py`.

- [ ] **Step 1: Write the failing tests (DB-gated)**

Append to `tests/test_inbox_undo.py`:

```python
def test_missing_wc_unresolve_removes_suppression():
    from zira_dashboard import missing_wc
    ATT = 999700
    db.execute("DELETE FROM missing_wc_resolved WHERE attendance_id = %s", (ATT,))
    missing_wc.resolve(ATT, "dismissed", name="Maria")
    assert ATT in missing_wc.resolved_ids()
    missing_wc.unresolve(ATT)
    assert ATT not in missing_wc.resolved_ids()


def test_undo_late_arrival_deletes_row():
    from zira_dashboard import late_report
    from datetime import date
    DAY, EMP = date(2026, 6, 26), "999801"
    db.execute("DELETE FROM late_arrivals WHERE day = %s AND emp_id = %s", (DAY, EMP))
    late_report.save_late_arrival(DAY, EMP, "Test Person", reason="Sick")
    assert db.query("SELECT 1 FROM late_arrivals WHERE day=%s AND emp_id=%s", (DAY, EMP))
    late_report.undo_late_arrival(DAY, EMP)
    assert not db.query("SELECT 1 FROM late_arrivals WHERE day=%s AND emp_id=%s", (DAY, EMP))
```

- [ ] **Step 2: Run to verify it fails**

Run (with test Postgres): `DATABASE_URL=$DATABASE_URL ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_inbox_undo.py -k "unresolve or undo_late" -v`
Expected: FAIL (`unresolve`/`undo_late_arrival` missing). Skips without DATABASE_URL.

- [ ] **Step 3: Add `missing_wc.unresolve`**

In `src/zira_dashboard/missing_wc.py`, add after `resolve()`:

```python
def unresolve(attendance_id) -> None:
    """Drop a suppression row so the attendance re-appears in the alert (undo)."""
    from . import db
    db.execute(
        "DELETE FROM missing_wc_resolved WHERE attendance_id = %s",
        (int(attendance_id),),
    )
```

- [ ] **Step 4: Add `late_report.undo_late_arrival`**

In `src/zira_dashboard/late_report.py`, add immediately after `undo_absent` (around line 64):

```python
def undo_late_arrival(day, emp_id: str) -> None:
    db.execute(
        "DELETE FROM late_arrivals WHERE day = %s AND emp_id = %s",
        (day, str(emp_id)),
    )
```

- [ ] **Step 5: Run to verify it passes**

Run (with test Postgres): `DATABASE_URL=$DATABASE_URL ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_inbox_undo.py -k "unresolve or undo_late" -v`
Expected: PASS (skips without DATABASE_URL).

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/missing_wc.py src/zira_dashboard/late_report.py tests/test_inbox_undo.py
git commit -m "feat(inbox): add un-resolve helpers for undo (missing-wc, late-arrival)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `reversible` flags + return `event_id` from undoable handlers

The undo UI needs the `inbox_events` id of the action it can reverse. The four undoable handlers return it; the non-undoable handlers' log calls are marked `reversible=False`.

**Files:** Modify `routes/missing_wc.py`, `routes/late_report.py`, `routes/exceptions.py`, `routes/missed_punch_out.py`, `routes/staffing.py`; update `tests/test_inbox_event_wiring.py`.

- [ ] **Step 1: Update the wiring tests to assert the new contract**

In `tests/test_inbox_event_wiring.py`, the `_capture_events` helper already returns `1` from the stubbed `log_event_safe`, so an undoable handler's response should carry `"event_id": 1`. Make these exact edits (no new test functions — extend the existing ones):

1. At the top of the file, add: `import json`
2. In `test_missing_wc_assign_records_inbox_event`, after the existing assertions add:
   ```python
   assert json.loads(resp.body)["event_id"] == 1
   assert events[0]["reversible"] is True
   ```
3. Add the identical two lines to `test_missing_wc_dismiss_records_inbox_event`, `test_late_declare_absent_records_inbox_event`, and `test_late_save_reason_records_inbox_event` (each already binds `resp` and `events`).
4. In `test_time_off_approve_records_inbox_event` and `test_time_off_deny_records_inbox_event`, add:
   ```python
   assert events[0]["reversible"] is False
   ```

- [ ] **Step 2: Run to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_inbox_event_wiring.py -v`
Expected: the four `event_id`/reversible asserts FAIL (responses don't carry `event_id` yet; time-off `reversible` is still `True`).

- [ ] **Step 3: `routes/missing_wc.py` — capture + return `event_id`**

In `_assign_sync`, change the `inbox_log.log_event_safe(...)` call to capture its id and return it:

```python
    eid = inbox_log.log_event_safe(
        item_kind="missing_wc",
        item_key=inbox_keys.missing_wc(att_id),
        person_name=name,
        category_label="Missing WC",
        action="assign",
        outcome=f"Assigned to {wc_name}",
        after_value=wc_name,
        actor_upn=actor_upn,
        actor_name=actor_name,
        source="inbox",
        reversible=True,
    )
    return JSONResponse({"ok": True, "event_id": eid})
```

In `_dismiss_sync`, the same pattern:

```python
    eid = inbox_log.log_event_safe(
        item_kind="missing_wc",
        item_key=inbox_keys.missing_wc(att_id),
        person_name=name,
        category_label="Missing WC",
        action="dismiss",
        outcome="Dismissed",
        actor_upn=actor_upn,
        actor_name=actor_name,
        source="inbox",
        reversible=True,
    )
    return JSONResponse({"ok": True, "event_id": eid})
```

- [ ] **Step 4: `routes/late_report.py` — `reason` reversible + return `event_id`**

In `_declare_absent_sync`, capture the id and change the return:

```python
    eid = inbox_log.log_event_safe(
        item_kind="late",
        item_key=inbox_keys.late(emp_id, today.isoformat()),
        person_name=name,
        category_label="Late",
        action="absent",
        outcome="Marked absent",
        reason=reason,
        actor_upn=actor_upn,
        actor_name=actor_name,
        source="inbox",
        reversible=True,
    )
    _bust_caches()
    return JSONResponse({"ok": True, "event_id": eid})
```

In `_save_late_arrival_sync`, flip `reversible` to `True`, capture the id, change the return:

```python
    eid = inbox_log.log_event_safe(
        item_kind="late",
        item_key=inbox_keys.late(emp_id, today.isoformat()),
        person_name=name,
        category_label="Late",
        action="reason",
        outcome="Late reason recorded",
        reason=reason,
        actor_upn=actor_upn,
        actor_name=actor_name,
        source="inbox",
        reversible=True,
    )
    _bust_caches()
    return JSONResponse({"ok": True, "event_id": eid})
```

- [ ] **Step 5: Mark the non-undoable logs `reversible=False`**

- `routes/exceptions.py`: in BOTH the approve and deny `inbox_log.log_event_safe(...)` calls, change `reversible=True,` to `reversible=False,`.
- `routes/missed_punch_out.py`: in the correct `inbox_log.log_event_safe(...)` call, change `reversible=True,` to `reversible=False,`.
- `routes/staffing.py`: in the assignment-credit `inbox_log.log_event_safe(...)` call, change `reversible=True,` to `reversible=False,`.

- [ ] **Step 6: Run the wiring tests**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_inbox_event_wiring.py tests/test_assignment_credit_logging.py -v`
Expected: PASS (undoable handlers return `event_id == 1` and log `reversible True`; time-off logs `reversible False`).

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/routes/missing_wc.py src/zira_dashboard/routes/late_report.py src/zira_dashboard/routes/exceptions.py src/zira_dashboard/routes/missed_punch_out.py src/zira_dashboard/routes/staffing.py tests/test_inbox_event_wiring.py
git commit -m "feat(inbox): mark undoable actions + return event_id; others reversible=false

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `POST /api/exceptions/undo/{event_id}`

**Files:** Modify `src/zira_dashboard/routes/exceptions.py`; append to `tests/test_inbox_undo.py`.

- [ ] **Step 1: Write the failing tests (mock Odoo, no DB)**

Create a NEW file `tests/test_inbox_undo_endpoint.py` (separate from the DB-gated `test_inbox_undo.py` — these monkeypatch every reversal collaborator + `inbox_log`, so they must run without a Postgres skip):

```python
"""POST /api/exceptions/undo/{event_id}: reverse the four undoable actions."""
from zira_dashboard import inbox_log
from zira_dashboard.routes import exceptions as exceptions_route


def _ev(**over):
    base = {
        "id": 7, "item_kind": "missing_wc", "item_key": "missing_wc:48213",
        "person_name": "Maria", "category_label": "Missing WC", "action": "dismiss",
        "outcome": "Dismissed", "before_value": None, "after_value": None,
        "reason": None, "actor_upn": "dale@gruberpallets.com", "actor_name": "Dale",
        "source": "inbox", "reversible": True, "undone_at": None,
        "undo_event_id": None, "resolved_at": exceptions_route.plant_day.now(),
    }
    base.update(over)
    return base


def test_undo_dismiss_unresolves(monkeypatch):
    from zira_dashboard import missing_wc
    calls = {}
    monkeypatch.setattr(inbox_log, "get_event", lambda eid: _ev(id=eid))
    monkeypatch.setattr(missing_wc, "unresolve", lambda att: calls.setdefault("unresolve", att))
    monkeypatch.setattr(inbox_log, "log_event_safe", lambda **kw: 99)
    monkeypatch.setattr(inbox_log, "mark_undone", lambda e, u: calls.setdefault("marked", (e, u)))
    monkeypatch.setattr(exceptions_route, "_refresh_time_off_surfaces", lambda: None)

    resp = exceptions_route._undo_sync(7, "maria@gruberpallets.com", "Maria Ruiz")
    assert resp.status_code == 200
    assert calls["unresolve"] == 48213
    assert calls["marked"] == (7, 99)


def test_undo_assign_clears_wc_then_unresolves(monkeypatch):
    from zira_dashboard import missing_wc, odoo_client
    calls = {}
    monkeypatch.setattr(inbox_log, "get_event", lambda eid: _ev(id=eid, action="assign", after_value="Saw 1"))
    monkeypatch.setattr(odoo_client, "set_attendance_wc", lambda att, wc: calls.setdefault("cleared", (att, wc)))
    monkeypatch.setattr(missing_wc, "unresolve", lambda att: calls.setdefault("unresolve", att))
    monkeypatch.setattr(inbox_log, "log_event_safe", lambda **kw: 99)
    monkeypatch.setattr(inbox_log, "mark_undone", lambda e, u: None)
    monkeypatch.setattr(exceptions_route, "_refresh_time_off_surfaces", lambda: None)

    resp = exceptions_route._undo_sync(7, None, None)
    assert resp.status_code == 200
    assert calls["cleared"] == (48213, None)
    assert calls["unresolve"] == 48213


def test_undo_late_reason_deletes_row(monkeypatch):
    from zira_dashboard import late_report
    calls = {}
    monkeypatch.setattr(inbox_log, "get_event",
                        lambda eid: _ev(id=eid, item_kind="late", item_key="late:42:2026-06-26", action="reason"))
    monkeypatch.setattr(late_report, "undo_late_arrival", lambda day, emp: calls.setdefault("undo", (day, emp)))
    monkeypatch.setattr(inbox_log, "log_event_safe", lambda **kw: 99)
    monkeypatch.setattr(inbox_log, "mark_undone", lambda e, u: None)
    monkeypatch.setattr(exceptions_route, "_refresh_time_off_surfaces", lambda: None)

    resp = exceptions_route._undo_sync(7, None, None)
    assert resp.status_code == 200
    assert calls["undo"] == ("2026-06-26", "42")


def test_undo_rejects_non_undoable(monkeypatch):
    monkeypatch.setattr(inbox_log, "get_event",
                        lambda eid: _ev(id=eid, item_kind="time_off", item_key="time_off:55", action="approve"))
    resp = exceptions_route._undo_sync(7, None, None)
    assert resp.status_code == 400


def test_undo_rejects_already_undone(monkeypatch):
    monkeypatch.setattr(inbox_log, "get_event",
                        lambda eid: _ev(id=eid, undone_at=exceptions_route.plant_day.now()))
    resp = exceptions_route._undo_sync(7, None, None)
    assert resp.status_code == 409
```

- [ ] **Step 2: Run to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_inbox_undo_endpoint.py -v`
Expected: FAIL — `exceptions_route._undo_sync` doesn't exist.

- [ ] **Step 3: Implement the endpoint + reversal in `routes/exceptions.py`**

Add near the other module constants (after `_TERMINAL_TIME_OFF_STATES`):

```python
_UNDOABLE = {
    ("missing_wc", "assign"),
    ("missing_wc", "dismiss"),
    ("late", "absent"),
    ("late", "reason"),
}
_UNDO_WINDOW = timedelta(minutes=10)
```

Add the reversal helper + sync + route (place after `_refuse_time_off_sync`/its route):

```python
def _reverse_event(ev: dict[str, Any]) -> None:
    """Reverse a resolved inbox action. Assumes (item_kind, action) is undoable."""
    from .. import absence_sync, late_report, missing_wc, odoo_client

    kind, action, key = ev["item_kind"], ev["action"], ev["item_key"]
    if kind == "missing_wc":
        att_id = int(key.split(":")[1])
        if action == "assign":
            odoo_client.set_attendance_wc(att_id, None)
        missing_wc.unresolve(att_id)
    elif kind == "late":
        _, emp_id, day = key.split(":", 2)
        if action == "absent":
            absence_sync.refuse_absence_leave(
                late_report.odoo_leave_id_for_absence(day, emp_id)
            )
            late_report.undo_absent(day, emp_id)
        elif action == "reason":
            late_report.undo_late_arrival(day, emp_id)


def _undo_sync(
    event_id: int,
    actor_upn: str | None = None,
    actor_name: str | None = None,
) -> JSONResponse:
    from .. import inbox_log

    ev = inbox_log.get_event(event_id)
    if ev is None:
        return _json_error("event not found", 404)
    if ev.get("undone_at") is not None:
        return _json_error("already undone", 409)
    if (ev["item_kind"], ev["action"]) not in _UNDOABLE:
        return _json_error("this action can't be undone", 400)
    resolved = ev["resolved_at"]
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    if plant_day.now() - resolved > _UNDO_WINDOW:
        return _json_error("undo window expired", 409)
    try:
        _reverse_event(ev)
    except Exception as e:  # noqa: BLE001 -- surface reversal failure to caller
        return _json_error(str(e), 500)
    undo_id = inbox_log.log_event_safe(
        item_kind=ev["item_kind"],
        item_key=ev["item_key"],
        person_name=ev.get("person_name"),
        category_label=ev.get("category_label"),
        action="undo",
        outcome="Undone",
        actor_upn=actor_upn,
        actor_name=actor_name,
        source="inbox",
    )
    inbox_log.mark_undone(event_id, undo_id)
    _refresh_time_off_surfaces()
    return JSONResponse({"ok": True})


@router.post("/api/exceptions/undo/{event_id}")
async def undo_inbox_event(event_id: int, request: Request):
    actor_upn, actor_name = _actor_from(request)
    return await asyncio.to_thread(_undo_sync, event_id, actor_upn, actor_name)
```

(`timedelta` was added to the datetime import in Phase 2a; `_refresh_time_off_surfaces` already busts the staffing + HTTP caches so the re-opened item reappears.)

- [ ] **Step 4: Run to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_inbox_undo_endpoint.py -v`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/exceptions.py tests/test_inbox_undo_endpoint.py
git commit -m "feat(inbox): add POST /api/exceptions/undo for the four reversible actions

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Undo affordance in `exceptions.js`

**Files:** Modify `src/zira_dashboard/static/exceptions.js`; append a JS static check to `tests/test_exception_inbox.py`.

- [ ] **Step 1: Write the failing static check**

Append to `tests/test_exception_inbox.py`:

```python
def test_exceptions_js_has_undo_affordance():
    import pathlib
    js = pathlib.Path("src/zira_dashboard/static/exceptions.js").read_text()
    assert "/api/exceptions/undo/" in js
    assert "data-undo" in js  # the Undo control rendered in the row status
```

- [ ] **Step 2: Run to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_exception_inbox.py::test_exceptions_js_has_undo_affordance -v`
Expected: FAIL (no undo wiring yet).

- [ ] **Step 3: Add the undo affordance to `resolveRow`**

In `src/zira_dashboard/static/exceptions.js`, replace the `resolveRow` function with a version that, when given an `eventId`, keeps the row briefly with an Undo control instead of removing it immediately:

```javascript
  var UNDO_MS = 5000;

  function undoRow(row, eventId) {
    setBusy(row, true);
    rowStatus(row, 'Undoing...', false);
    postJson('/api/exceptions/undo/' + encodeURIComponent(eventId), {})
      .then(function (resp) {
        if (resp && resp.ok) {
          window.location.reload();
        } else {
          rowStatus(row, (resp && resp.error) || 'Undo failed.', true);
        }
      })
      .catch(function () { rowStatus(row, 'Network error.', true); });
  }

  function finalizeResolved(row) {
    bumpTotal(-1);
    bumpUrgentInline(row, -1);
    bumpFocusCounts(row, -1);
    refreshSharedBadge(row);
    refreshInboxSummary();
    removeResolvedRow(row);
    applyFocus(currentFocus);
  }

  function resolveRow(row, label, eventId) {
    setBusy(row, true);
    row.classList.add('is-resolved');
    var status = row.querySelector('.row-status');
    if (eventId && status) {
      status.hidden = false;
      status.textContent = (label || 'Done') + ' · ';
      var undo = document.createElement('button');
      undo.type = 'button';
      undo.className = 'undo-link';
      undo.setAttribute('data-undo', String(eventId));
      undo.textContent = 'Undo';
      status.appendChild(undo);
      var timer = setTimeout(function () { finalizeResolved(row); }, UNDO_MS);
      undo.addEventListener('click', function () {
        clearTimeout(timer);
        undoRow(row, eventId);
      });
    } else {
      rowStatus(row, label || 'Done', false);
      setTimeout(function () { finalizeResolved(row); }, 450);
    }
  }
```

> `resolveRow` previously decremented the counters itself; that logic now lives in `finalizeResolved` so the counts only drop when the row actually leaves (after the undo window or a non-undoable resolve). Undo reloads the page, which re-renders fresh counts.

- [ ] **Step 4: Pass the `event_id` through at the four undoable call sites**

In the same file, update the four undoable handlers to forward `resp.event_id` to `resolveRow`:

- `js-assign` branch: `resolveRow(row, 'Assigned', resp.event_id);`
- `js-missing-wc-save` branch: `resolveRow(row, 'Assigned', resp.event_id);`
- `js-missing-wc-dismiss` branch: `resolveRow(row, 'Dismissed', resp.event_id);`
- `js-absent` branch: `resolveRow(row, 'Marked absent', resp.event_id);`
- `js-save-late` branch: `resolveRow(row, 'Reason saved', resp.event_id);`

> Note: `js-assign` is the staffing-credit (assignment) action — it is NOT undoable, so its response has no `event_id`; passing `resp.event_id` (undefined) makes `resolveRow` take the non-undo path. Leave `js-snooze` / time-off / punch calls as `resolveRow(row, 'X')` (no event id).

- [ ] **Step 5: Add the `.undo-link` style**

In `src/zira_dashboard/static/exceptions.css`, add:

```css
.undo-link {
  border: 0;
  background: transparent;
  color: var(--info);
  cursor: pointer;
  font: inherit;
  font-weight: 800;
  text-decoration: underline;
  padding: 0;
}
```

- [ ] **Step 6: Run to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_exception_inbox.py -v`
Expected: PASS (incl. the new undo-affordance check).

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/static/exceptions.js src/zira_dashboard/static/exceptions.css tests/test_exception_inbox.py
git commit -m "feat(inbox): show a 5s Undo on the four reversible actions

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Full-suite verification

- [ ] **Step 1: Full suite + ruff**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest -q` (no regressions; DB-gated undo helper tests skip locally, the endpoint + wiring + JS-static tests run).
Run: `.venv/bin/python -m ruff check src/zira_dashboard/inbox_log.py src/zira_dashboard/missing_wc.py src/zira_dashboard/late_report.py src/zira_dashboard/routes/exceptions.py src/zira_dashboard/routes/missing_wc.py src/zira_dashboard/routes/late_report.py src/zira_dashboard/routes/missed_punch_out.py src/zira_dashboard/routes/staffing.py tests/test_inbox_undo.py tests/test_inbox_undo_endpoint.py`
Expected: no errors.

---

## Done criteria

- Dismiss / Assign-WC / Mark-absent / Late-reason each show a 5-second **Undo**; clicking it reverses the action (clears the Odoo WC / refuses the absence leave / deletes the local row), writes an `undo` event, marks the original undone, and the item returns to the queue.
- Approve / Deny / Correct / assignment-credit show NO Undo (`reversible=False`), and `/api/exceptions/undo` rejects them (400), already-undone (409), and expired-window (409).
- An undo reversal failure surfaces as an error; a failed audit write never blocks it.
- Full suite green; ruff clean.

## Non-goals

- No undo for Odoo-committed decisions (approve/deny/correct) — there is no clean Odoo reverse; fix those in Odoo.
- No multi-level undo / redo. One undo per event, guarded by `undone_at`.
