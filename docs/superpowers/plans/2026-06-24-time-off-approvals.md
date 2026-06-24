# Time Off Approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing inbox approve/deny flow into a trustworthy production feature — a full pending-approvals page with balance + coverage context, a required denial reason, and an audited decision history — while keeping the inbox as a fast-path.

**Architecture:** A new `time_off_audit` module + `time_off_decisions` table records every decision (append-only, denormalized). A new `time_off_context` module computes balance and department-scoped coverage from local mirrors. The existing `/api/exceptions/time-off/{id}/approve|refuse` handlers are extended in place (actor capture, required reason, audit write, Odoo chatter post). A new `routes/time_off_approvals.py` + template renders the full queue and history. The inbox keeps its section as a fast-path with the deny-reason field inline.

**Tech Stack:** FastAPI, Jinja2 templates, vanilla JS, Postgres (psycopg2 via `db.query`/`db.execute`), Odoo XML-RPC via `odoo_client.execute`, pytest + `fastapi.testclient`.

**Spec:** [docs/superpowers/specs/2026-06-24-time-off-approvals-design.md](../specs/2026-06-24-time-off-approvals-design.md)

**Conventions for every task:**
- Run the suite with `ZIRA_API_KEY=test .venv/bin/python -m pytest <path> -v` (the key is needed at import or route tests error at collection).
- Every commit message ends with the trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` (omitted from the examples below for brevity).
- New DB-touching unit tests monkeypatch `db.query` / `db.execute` — no live Postgres needed.

---

### Task 1: Add the `time_off_decisions` table to the schema

**Files:**
- Modify: `src/zira_dashboard/_schema.py` (append before the closing `"""` of `SCHEMA_DDL`, which ends at line ~888)
- Test: `tests/test_time_off_decisions.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_time_off_decisions.py
from zira_dashboard._schema import SCHEMA_DDL


def test_schema_defines_time_off_decisions_table():
    assert "CREATE TABLE IF NOT EXISTS time_off_decisions" in SCHEMA_DDL
    for col in (
        "request_id", "odoo_leave_id", "person_odoo_id", "person_name",
        "leave_type", "date_from", "date_to", "action", "result_state",
        "reason", "actor_upn", "actor_name", "source", "decided_at",
    ):
        assert col in SCHEMA_DDL, f"missing column {col}"
    assert "action IN ('approve','deny')" in SCHEMA_DDL
    assert "time_off_decisions_decided_at_idx" in SCHEMA_DDL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_decisions.py -v`
Expected: FAIL with `assert "CREATE TABLE IF NOT EXISTS time_off_decisions" in SCHEMA_DDL`

- [ ] **Step 3: Append the DDL inside `SCHEMA_DDL`**

Add immediately before the closing `"""` that terminates `SCHEMA_DDL` in `src/zira_dashboard/_schema.py`:

```sql

-- 2026-06-24: append-only audit log of time-off approve/deny decisions made
-- in-app. Deliberately denormalized (no FK to time_off_requests): the leave
-- poller hard-deletes mirror rows when a leave is deleted in Odoo, and the
-- decision history must survive that. request_id is the mirror id at decision
-- time, kept for correlation only.
CREATE TABLE IF NOT EXISTS time_off_decisions (
  id              SERIAL PRIMARY KEY,
  request_id      INTEGER,
  odoo_leave_id   INTEGER,
  person_odoo_id  INTEGER,
  person_name     TEXT,
  leave_type      TEXT,
  date_from       DATE,
  date_to         DATE,
  action          TEXT NOT NULL CHECK (action IN ('approve','deny')),
  result_state    TEXT,
  reason          TEXT,
  actor_upn       TEXT,
  actor_name      TEXT,
  source          TEXT,
  decided_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS time_off_decisions_decided_at_idx
  ON time_off_decisions (decided_at DESC);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_decisions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/_schema.py tests/test_time_off_decisions.py
git commit -m "Add time_off_decisions audit table to schema"
```

---

### Task 2: Audit module — `record_decision` + `recent_decisions`

**Files:**
- Create: `src/zira_dashboard/time_off_audit.py`
- Test: `tests/test_time_off_audit.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_time_off_audit.py
from datetime import date

from zira_dashboard import time_off_audit


def test_record_decision_inserts_denormalized_row(monkeypatch):
    calls = []
    monkeypatch.setattr(time_off_audit.db, "execute",
                        lambda sql, params: calls.append((sql, params)))

    time_off_audit.record_decision(
        request_id=55, odoo_leave_id=99, person_odoo_id=7,
        person_name="Maria Delgado", leave_type="PTO",
        date_from=date(2026, 6, 30), date_to=date(2026, 7, 2),
        action="deny", result_state="refuse", reason="Coverage too thin",
        actor_upn="dale@gruberpallets.com", actor_name="Dale Gruber",
        source="page",
    )

    assert len(calls) == 1
    sql, params = calls[0]
    assert "INSERT INTO time_off_decisions" in sql
    assert params[0] == 55 and "Maria Delgado" in params
    assert "deny" in params and "Coverage too thin" in params


def test_recent_decisions_queries_window(monkeypatch):
    captured = {}
    def fake_query(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return [{"action": "approve", "person_name": "Ana Flores"}]
    monkeypatch.setattr(time_off_audit.db, "query", fake_query)

    rows = time_off_audit.recent_decisions(days=30)

    assert rows and rows[0]["person_name"] == "Ana Flores"
    assert "FROM time_off_decisions" in captured["sql"]
    assert "ORDER BY decided_at DESC" in captured["sql"]
    assert captured["params"] == (30,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_audit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zira_dashboard.time_off_audit'`

- [ ] **Step 3: Create the module**

```python
# src/zira_dashboard/time_off_audit.py
"""Append-only audit log for in-app time-off approve/deny decisions.

Denormalized on purpose: the leave poller hard-deletes time_off_requests
rows when a leave is deleted in Odoo, so this log snapshots person name,
leave type, and dates to stand alone. See the design spec.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from . import db


def record_decision(
    *,
    request_id: int | None,
    odoo_leave_id: int | None,
    person_odoo_id: int | None,
    person_name: str | None,
    leave_type: str | None,
    date_from: date | None,
    date_to: date | None,
    action: str,
    result_state: str | None,
    reason: str | None,
    actor_upn: str | None,
    actor_name: str | None,
    source: str | None,
) -> None:
    """Insert one decision row. ``action`` is 'approve' or 'deny'."""
    db.execute(
        "INSERT INTO time_off_decisions "
        "(request_id, odoo_leave_id, person_odoo_id, person_name, leave_type, "
        " date_from, date_to, action, result_state, reason, actor_upn, "
        " actor_name, source) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (request_id, odoo_leave_id, person_odoo_id, person_name, leave_type,
         date_from, date_to, action, result_state, reason, actor_upn,
         actor_name, source),
    )


def recent_decisions(days: int = 30) -> list[dict[str, Any]]:
    """Decisions in the last ``days`` days, newest first."""
    return db.query(
        "SELECT id, request_id, odoo_leave_id, person_odoo_id, person_name, "
        "leave_type, date_from, date_to, action, result_state, reason, "
        "actor_upn, actor_name, source, decided_at "
        "FROM time_off_decisions "
        "WHERE decided_at >= now() - make_interval(days => %s) "
        "ORDER BY decided_at DESC",
        (days,),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_audit.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/time_off_audit.py tests/test_time_off_audit.py
git commit -m "Add time_off_audit module for decision history"
```

---

### Task 3: Odoo client — `post_leave_message`

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py` (add after `refuse_leave`, ~line 1207)
- Test: `tests/test_odoo_client_leaves.py` (add a test)

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_odoo_client_leaves.py
def test_post_leave_message_calls_message_post(monkeypatch):
    from zira_dashboard import odoo_client

    calls = []
    monkeypatch.setattr(
        odoo_client, "execute",
        lambda model, method, *args, **kwargs: calls.append((model, method, args, kwargs)),
    )

    odoo_client.post_leave_message(99, "Coverage too thin that Friday")

    assert calls == [
        ("hr.leave", "message_post", ([99],),
         {"body": "Coverage too thin that Friday"}),
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_odoo_client_leaves.py::test_post_leave_message_calls_message_post -v`
Expected: FAIL with `AttributeError: module 'zira_dashboard.odoo_client' has no attribute 'post_leave_message'`

- [ ] **Step 3: Add the function**

Add after `refuse_leave` in `src/zira_dashboard/odoo_client.py`:

```python
def post_leave_message(leave_id: int, body: str) -> None:
    """Post a message to an hr.leave's chatter so the employee is notified.

    ``body`` is passed as a keyword arg because ``execute`` forwards
    **kwargs as Odoo's keyword args (see ``execute``); the leave id is the
    positional recordset. Used to deliver a denial reason back to the
    requester. Callers treat this as best-effort — a failed post must not
    roll back a completed refusal.
    """
    execute("hr.leave", "message_post", [leave_id], body=body)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_odoo_client_leaves.py::test_post_leave_message_calls_message_post -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/odoo_client.py tests/test_odoo_client_leaves.py
git commit -m "Add odoo_client.post_leave_message for denial reasons"
```

---

### Task 4: Decision-context module — department, coverage, balance

**Files:**
- Create: `src/zira_dashboard/time_off_context.py`
- Test: `tests/test_time_off_context.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_time_off_context.py
from datetime import date

from zira_dashboard import time_off_context as ctx


def test_department_for_person_returns_distinct_departments(monkeypatch):
    monkeypatch.setattr(ctx.db, "query",
                        lambda sql, params: [{"department": "Recycled"}, {"department": "New"}])
    assert ctx.department_for_person(7) == {"Recycled", "New"}


def test_coverage_for_uses_department_scope_when_known(monkeypatch):
    monkeypatch.setattr(ctx, "department_for_person", lambda pid: {"Recycled"})
    seen = {}
    def fake_query(sql, params):
        seen["sql"] = sql
        seen["params"] = params
        return [{"n": 2}]
    monkeypatch.setattr(ctx.db, "query", fake_query)

    result = ctx.coverage_for(7, date(2026, 6, 30), date(2026, 7, 2))

    assert result == {"count": 2, "scope": "department"}
    assert "ANY(%s)" in seen["sql"]
    assert seen["params"][0] == 7  # exclude requester


def test_coverage_for_falls_back_to_plant_when_no_department(monkeypatch):
    monkeypatch.setattr(ctx, "department_for_person", lambda pid: set())
    monkeypatch.setattr(ctx.db, "query", lambda sql, params: [{"n": 5}])

    result = ctx.coverage_for(7, date(2026, 6, 30), date(2026, 7, 2))

    assert result == {"count": 5, "scope": "plant"}


def test_balance_for_returns_remaining_and_unit(monkeypatch):
    monkeypatch.setattr(ctx.db, "query",
                        lambda sql, params: [{"available": 24.0, "unit": "hours"}])
    assert ctx.balance_for(7, 3) == {"remaining": 24.0, "unit": "hours"}


def test_balance_for_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(ctx.db, "query", lambda sql, params: [])
    assert ctx.balance_for(7, 3) is None


def test_request_amount_hours_and_days():
    assert ctx.request_amount(
        {"hour_from": 8.0, "hour_to": 12.0, "date_from": date(2026, 7, 3),
         "date_to": date(2026, 7, 3)}) == (4.0, "hours")
    assert ctx.request_amount(
        {"hour_from": None, "hour_to": None, "date_from": date(2026, 6, 30),
         "date_to": date(2026, 7, 2)}) == (3.0, "days")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zira_dashboard.time_off_context'`

- [ ] **Step 3: Create the module**

```python
# src/zira_dashboard/time_off_context.py
"""Decision context for time-off approvals: remaining balance and
same-day coverage, computed from local mirrors only (no live Odoo).

Coverage is scoped to the requester's department, derived from their
default work-center membership (work_center_default_people ->
work_centers.department), with a plant-wide fallback when no department
resolves. Balance reads the time_off_balances cache.
"""
from __future__ import annotations

from datetime import date

from . import db


def department_for_person(person_odoo_id: int) -> set[str]:
    """Departments the person is a default member of (via their default
    work centers). Empty set when they map to no department."""
    rows = db.query(
        "SELECT DISTINCT wc.department "
        "FROM work_center_default_people wcdp "
        "JOIN work_centers wc ON wc.id = wcdp.wc_id "
        "JOIN people pe ON pe.id = wcdp.person_id "
        "WHERE pe.odoo_id = %s AND wc.department IS NOT NULL "
        "AND wc.department <> ''",
        (person_odoo_id,),
    )
    return {r["department"] for r in rows if r.get("department")}


def coverage_for(person_odoo_id: int, date_from: date, date_to: date) -> dict:
    """Count OTHER people with an approved leave overlapping [date_from,
    date_to]. Scoped to the requester's department when known, else
    plant-wide. Returns {'count': int, 'scope': 'department'|'plant'}."""
    depts = department_for_person(person_odoo_id)
    if depts:
        rows = db.query(
            "SELECT COUNT(DISTINCT r.person_odoo_id) AS n "
            "FROM time_off_requests r "
            "JOIN people pe ON pe.odoo_id = r.person_odoo_id "
            "JOIN work_center_default_people wcdp ON wcdp.person_id = pe.id "
            "JOIN work_centers wc ON wc.id = wcdp.wc_id "
            "WHERE r.state = 'validate' AND r.person_odoo_id <> %s "
            "AND r.date_to >= %s AND r.date_from <= %s "
            "AND wc.department = ANY(%s)",
            (person_odoo_id, date_from, date_to, list(depts)),
        )
        return {"count": int(rows[0]["n"] if rows else 0), "scope": "department"}
    rows = db.query(
        "SELECT COUNT(DISTINCT r.person_odoo_id) AS n "
        "FROM time_off_requests r "
        "WHERE r.state = 'validate' AND r.person_odoo_id <> %s "
        "AND r.date_to >= %s AND r.date_from <= %s",
        (person_odoo_id, date_from, date_to),
    )
    return {"count": int(rows[0]["n"] if rows else 0), "scope": "plant"}


def balance_for(person_odoo_id: int, holiday_status_id: int) -> dict | None:
    """Remaining balance for one (person, leave type) from the local cache,
    or None when no balance row exists. {'remaining': float, 'unit': str}."""
    rows = db.query(
        "SELECT available, unit FROM time_off_balances "
        "WHERE person_odoo_id = %s AND holiday_status_id = %s",
        (person_odoo_id, holiday_status_id),
    )
    if not rows:
        return None
    return {"remaining": float(rows[0]["available"]), "unit": rows[0]["unit"]}


def request_amount(row: dict) -> tuple[float, str]:
    """Approximate amount + unit a request consumes, for the balance warning.
    Hour-bounded requests -> hours; otherwise inclusive day count."""
    hf, ht = row.get("hour_from"), row.get("hour_to")
    if hf is not None and ht is not None:
        return (float(ht) - float(hf), "hours")
    days = (row["date_to"] - row["date_from"]).days + 1
    return (float(days), "days")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_context.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/time_off_context.py tests/test_time_off_context.py
git commit -m "Add time_off_context module for balance and coverage"
```

---

### Task 5: Snapshot person name + leave type in `_load_time_off_request`

The audit log needs a person name and leave-type name; the loader currently
returns neither. Extend its SELECT (mirrors the join in
`exception_inbox._pending_time_off`).

**Files:**
- Modify: `src/zira_dashboard/routes/exceptions.py:60-70` (`_load_time_off_request`)
- Test: `tests/test_exception_inbox.py` (add a test)

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_exception_inbox.py
def test_load_time_off_request_selects_name_and_type(monkeypatch):
    captured = {}
    def fake_query(sql, params):
        captured["sql"] = sql
        return [{"id": 55, "person_name": "Maria Delgado", "leave_type": "PTO"}]
    from zira_dashboard import db as _db
    monkeypatch.setattr(_db, "query", fake_query)

    row = exceptions_route._load_time_off_request(55)

    assert row["person_name"] == "Maria Delgado"
    assert row["leave_type"] == "PTO"
    assert "COALESCE(p.name" in captured["sql"]
    assert "leave_types_cache" in captured["sql"]
```

(Note: `_load_time_off_request` imports `db` lazily inside the function, so
patch `zira_dashboard.db.query` directly as shown.)

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_exception_inbox.py::test_load_time_off_request_selects_name_and_type -v`
Expected: FAIL — current SQL has no `person_name`/`leave_type` columns, so `row["person_name"]` raises `KeyError` (or the assert on `captured["sql"]` fails).

- [ ] **Step 3: Extend the SELECT**

Replace the body of `_load_time_off_request` in `src/zira_dashboard/routes/exceptions.py`:

```python
def _load_time_off_request(request_id: int) -> dict[str, Any] | None:
    from .. import db

    rows = db.query(
        "SELECT r.id, r.person_odoo_id, r.originating_kiosk_user, r.shape, "
        "r.holiday_status_id, r.date_from, r.date_to, r.hour_from, r.hour_to, "
        "r.note, r.state, r.odoo_leave_id, r.sync_error, "
        "COALESCE(p.name, '#' || r.person_odoo_id::text) AS person_name, "
        "COALESCE(lt.name, 'Time off') AS leave_type "
        "FROM time_off_requests r "
        "LEFT JOIN people p ON p.odoo_id = r.person_odoo_id "
        "LEFT JOIN leave_types_cache lt ON lt.holiday_status_id = r.holiday_status_id "
        "WHERE r.id = %s",
        (request_id,),
    )
    return rows[0] if rows else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_exception_inbox.py::test_load_time_off_request_selects_name_and_type -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/exceptions.py tests/test_exception_inbox.py
git commit -m "Snapshot person name and leave type in time-off loader"
```

---

### Task 6: Approve endpoint — capture actor + write audit row

**Files:**
- Modify: `src/zira_dashboard/routes/exceptions.py` (`_approve_time_off_sync`, the async route, add `_actor_from`)
- Test: `tests/test_exception_inbox.py` (update existing approve test + add one)

- [ ] **Step 1: Update the existing approve test and add an audit test**

Replace `test_time_off_approve_endpoint_updates_to_odoo_state` and add a second test in `tests/test_exception_inbox.py`:

```python
def test_time_off_approve_endpoint_updates_to_odoo_state(monkeypatch):
    from zira_dashboard import odoo_client

    row = {
        "id": 55, "person_odoo_id": 7, "person_name": "Maria Delgado",
        "leave_type": "PTO", "date_from": date(2026, 6, 22),
        "date_to": date(2026, 6, 22), "state": "confirm", "odoo_leave_id": 99,
    }
    updates = []
    audits = []
    monkeypatch.setattr(exceptions_route, "_load_time_off_request", lambda rid: row)
    monkeypatch.setattr(odoo_client, "approve_leave", lambda leave_id: "validate")
    monkeypatch.setattr(exceptions_route, "_set_time_off_state",
                        lambda old, state: updates.append((old["id"], state)))
    monkeypatch.setattr(exceptions_route.time_off_audit, "record_decision",
                        lambda **kw: audits.append(kw))

    resp = exceptions_route._approve_time_off_sync(
        55, actor_upn="dale@gruberpallets.com", actor_name="Dale Gruber",
        source="page")

    assert resp.status_code == 200
    assert updates == [(55, "validate")]
    assert len(audits) == 1
    assert audits[0]["action"] == "approve"
    assert audits[0]["result_state"] == "validate"
    assert audits[0]["actor_upn"] == "dale@gruberpallets.com"
    assert audits[0]["person_name"] == "Maria Delgado"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_exception_inbox.py::test_time_off_approve_endpoint_updates_to_odoo_state -v`
Expected: FAIL — `_approve_time_off_sync` takes only `request_id`; passing `actor_upn=` raises `TypeError`, and `exceptions_route.time_off_audit` does not exist yet.

- [ ] **Step 3: Add the import, `_actor_from`, and extend approve**

In `src/zira_dashboard/routes/exceptions.py`, add the import near the top:

```python
from .. import exception_inbox, time_off_audit
```

Add this helper (e.g. after `_json_error`):

```python
def _actor_from(request: Request) -> tuple[str | None, str | None]:
    """(upn, name) of the logged-in user from the auth middleware, or
    (None, None) when unset (AUTH_DISABLED dev / tests)."""
    return (
        getattr(request.state, "user_upn", None),
        getattr(request.state, "user_name", None),
    )
```

Change `_approve_time_off_sync` to record the decision on success:

```python
def _approve_time_off_sync(
    request_id: int,
    actor_upn: str | None = None,
    actor_name: str | None = None,
    source: str | None = None,
) -> JSONResponse:
    from .. import odoo_client

    row = _load_time_off_request(request_id)
    if row is None:
        return _json_error("request not found", 404)
    state = str(row.get("state") or "")
    if state == "validate":
        return JSONResponse({"ok": True, "state": state, "no_op": True})
    if state in _TERMINAL_TIME_OFF_STATES or state == "draft_cancel":
        return _json_error("request is already closed", 409)
    if state not in _PENDING_TIME_OFF_STATES:
        return _json_error(f"request cannot be approved from state {state}", 409)

    synced = _sync_to_odoo_if_needed(row)
    if isinstance(synced, JSONResponse):
        return synced
    try:
        final_state = odoo_client.approve_leave(int(synced["odoo_leave_id"])) or synced["state"]
    except Exception as e:
        return _json_error(str(e), 500)
    if final_state not in _TIME_OFF_STATES:
        return _json_error(f"unexpected Odoo state {final_state}", 500)
    _set_time_off_state(row, final_state)
    time_off_audit.record_decision(
        request_id=row["id"], odoo_leave_id=synced.get("odoo_leave_id"),
        person_odoo_id=row.get("person_odoo_id"),
        person_name=row.get("person_name"), leave_type=row.get("leave_type"),
        date_from=row.get("date_from"), date_to=row.get("date_to"),
        action="approve", result_state=final_state, reason=None,
        actor_upn=actor_upn, actor_name=actor_name, source=source,
    )
    return JSONResponse({"ok": True, "state": final_state, "approved": final_state == "validate"})
```

Update the async route to capture the actor and an optional `source`:

```python
@router.post("/api/exceptions/time-off/{request_id}/approve")
async def approve_time_off_request(request_id: int, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    source = (body or {}).get("source")
    actor_upn, actor_name = _actor_from(request)
    return await asyncio.to_thread(
        _approve_time_off_sync, request_id, actor_upn, actor_name, source)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_exception_inbox.py -k time_off_approve -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/exceptions.py tests/test_exception_inbox.py
git commit -m "Record actor + audit row on time-off approval"
```

---

### Task 7: Refuse endpoint — required reason + actor + audit + chatter post

**Files:**
- Modify: `src/zira_dashboard/routes/exceptions.py` (`_refuse_time_off_sync`, async route)
- Test: `tests/test_exception_inbox.py` (update existing refuse test + add three)

- [ ] **Step 1: Update/add tests**

Replace `test_time_off_refuse_unsynced_draft_stays_local` and add three tests in `tests/test_exception_inbox.py`:

```python
def test_time_off_refuse_requires_reason():
    resp = exceptions_route._refuse_time_off_sync(56, reason="")
    assert resp.status_code == 400


def test_time_off_refuse_unsynced_draft_stays_local(monkeypatch):
    from zira_dashboard import odoo_client

    row = {
        "id": 56, "person_odoo_id": 8, "person_name": "Carlos Ortega",
        "leave_type": "Unpaid", "date_from": date(2026, 6, 22),
        "date_to": date(2026, 6, 22), "state": "draft", "odoo_leave_id": None,
    }
    updates, refused, posted, audits = [], [], [], []
    monkeypatch.setattr(exceptions_route, "_load_time_off_request", lambda rid: row)
    monkeypatch.setattr(odoo_client, "refuse_leave", lambda lid: refused.append(lid))
    monkeypatch.setattr(odoo_client, "post_leave_message",
                        lambda lid, body: posted.append((lid, body)))
    monkeypatch.setattr(exceptions_route, "_set_time_off_state",
                        lambda old, state: updates.append((old["id"], state)))
    monkeypatch.setattr(exceptions_route.time_off_audit, "record_decision",
                        lambda **kw: audits.append(kw))

    resp = exceptions_route._refuse_time_off_sync(
        56, reason="No coverage", actor_upn="dale@gruberpallets.com",
        actor_name="Dale Gruber", source="inbox")

    assert resp.status_code == 200
    assert updates == [(56, "refuse")]
    assert refused == [] and posted == []  # never synced -> no Odoo calls
    assert audits[0]["action"] == "deny" and audits[0]["reason"] == "No coverage"


def test_time_off_refuse_synced_posts_reason_to_odoo(monkeypatch):
    from zira_dashboard import odoo_client

    row = {
        "id": 57, "person_odoo_id": 9, "person_name": "Luis Vega",
        "leave_type": "PTO", "date_from": date(2026, 6, 25),
        "date_to": date(2026, 6, 25), "state": "confirm", "odoo_leave_id": 99,
    }
    posted = []
    monkeypatch.setattr(exceptions_route, "_load_time_off_request", lambda rid: row)
    monkeypatch.setattr(odoo_client, "refuse_leave", lambda lid: None)
    monkeypatch.setattr(odoo_client, "post_leave_message",
                        lambda lid, body: posted.append((lid, body)))
    monkeypatch.setattr(exceptions_route, "_set_time_off_state", lambda old, state: None)
    monkeypatch.setattr(exceptions_route.time_off_audit, "record_decision", lambda **kw: None)

    resp = exceptions_route._refuse_time_off_sync(57, reason="Coverage too thin")

    assert resp.status_code == 200
    assert posted == [(99, "Coverage too thin")]


def test_time_off_refuse_survives_chatter_post_failure(monkeypatch):
    from zira_dashboard import odoo_client

    row = {
        "id": 58, "person_odoo_id": 9, "person_name": "Luis Vega",
        "leave_type": "PTO", "date_from": date(2026, 6, 25),
        "date_to": date(2026, 6, 25), "state": "confirm", "odoo_leave_id": 99,
    }
    updates, audits = [], []
    monkeypatch.setattr(exceptions_route, "_load_time_off_request", lambda rid: row)
    monkeypatch.setattr(odoo_client, "refuse_leave", lambda lid: None)
    def boom(lid, body):
        raise RuntimeError("odoo down")
    monkeypatch.setattr(odoo_client, "post_leave_message", boom)
    monkeypatch.setattr(exceptions_route, "_set_time_off_state",
                        lambda old, state: updates.append(state))
    monkeypatch.setattr(exceptions_route.time_off_audit, "record_decision",
                        lambda **kw: audits.append(kw))

    resp = exceptions_route._refuse_time_off_sync(58, reason="No coverage")

    assert resp.status_code == 200  # denial still succeeds
    assert updates == ["refuse"] and len(audits) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_exception_inbox.py -k time_off_refuse -v`
Expected: FAIL — `_refuse_time_off_sync` does not yet take `reason`.

- [ ] **Step 3: Rewrite `_refuse_time_off_sync` and its route**

Replace `_refuse_time_off_sync` and `refuse_time_off_request` in `src/zira_dashboard/routes/exceptions.py`:

```python
def _refuse_time_off_sync(
    request_id: int,
    reason: str,
    actor_upn: str | None = None,
    actor_name: str | None = None,
    source: str | None = None,
) -> JSONResponse:
    import logging

    from .. import odoo_client

    reason = (reason or "").strip()
    if not reason:
        return _json_error("a reason is required to deny", 400)

    row = _load_time_off_request(request_id)
    if row is None:
        return _json_error("request not found", 404)
    state = str(row.get("state") or "")
    if state in _TERMINAL_TIME_OFF_STATES:
        return JSONResponse({"ok": True, "state": state, "no_op": True})

    leave_id = row.get("odoo_leave_id")
    if leave_id is not None:
        try:
            odoo_client.refuse_leave(int(leave_id))
        except Exception as e:
            return _json_error(str(e), 500)
        try:
            odoo_client.post_leave_message(int(leave_id), reason)
        except Exception as e:  # best-effort — denial already succeeded
            logging.getLogger(__name__).warning(
                "chatter post failed for leave %s (denial still applied): %s",
                leave_id, e,
            )
    _set_time_off_state(row, "refuse")
    time_off_audit.record_decision(
        request_id=row["id"], odoo_leave_id=leave_id,
        person_odoo_id=row.get("person_odoo_id"),
        person_name=row.get("person_name"), leave_type=row.get("leave_type"),
        date_from=row.get("date_from"), date_to=row.get("date_to"),
        action="deny", result_state="refuse", reason=reason,
        actor_upn=actor_upn, actor_name=actor_name, source=source,
    )
    return JSONResponse({"ok": True, "state": "refuse"})


@router.post("/api/exceptions/time-off/{request_id}/refuse")
async def refuse_time_off_request(request_id: int, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = (body or {}).get("reason", "")
    source = (body or {}).get("source")
    actor_upn, actor_name = _actor_from(request)
    return await asyncio.to_thread(
        _refuse_time_off_sync, request_id, reason, actor_upn, actor_name, source)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_exception_inbox.py -k time_off_refuse -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/exceptions.py tests/test_exception_inbox.py
git commit -m "Require reason, audit, and notify employee on time-off denial"
```

---

### Task 8: Inbox fast-path — point "Open" at the page; deny captures a reason

**Files:**
- Modify: `src/zira_dashboard/exception_inbox.py` (the `time_off` section `href`, ~line 321)
- Modify: `src/zira_dashboard/templates/exceptions.html:171-173` (time_off row actions)
- Modify: `src/zira_dashboard/static/exceptions.js` (refuse handler, ~line 451)
- Test: `tests/test_exception_inbox.py` (add assertions)

- [ ] **Step 1: Write failing tests**

```python
# add to tests/test_exception_inbox.py
def test_pending_time_off_section_links_to_approvals_page():
    snap = exception_inbox.build_snapshot()
    time_off = next(s for s in snap["sections"] if s["id"] == "time_off")
    assert time_off["href"] == "/staffing/time-off/approvals"


def test_inbox_template_has_inline_deny_reason():
    html = (Path(__file__).resolve().parents[1]
            / "src/zira_dashboard/templates/exceptions.html").read_text("utf-8")
    assert "js-time-off-reason" in html
    assert "js-time-off-refuse" in html


def test_inbox_js_requires_reason_and_sends_source():
    js = (STATIC_DIR / "exceptions.js").read_text("utf-8")
    assert "js-time-off-reason" in js
    assert "source: 'inbox'" in js
```

(`build_snapshot` calls real source functions; this test runs under the same
conditions as the existing `test_*` snapshot tests in this file — if those
require monkeypatching, mirror their setup. The `href` assertion only reads the
static section definition, which is unconditional.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_exception_inbox.py -k "approvals_page or inline_deny or requires_reason" -v`
Expected: FAIL — current `href` is `/staffing/time-off`; no reason field exists.

- [ ] **Step 3a: Repoint the section href**

In `src/zira_dashboard/exception_inbox.py`, the `time_off` section dict, change:

```python
            "href": "/staffing/time-off",
```
to:
```python
            "href": "/staffing/time-off/approvals",
```

- [ ] **Step 3b: Add the inline reason field to the row**

In `src/zira_dashboard/templates/exceptions.html`, replace the `time_off` action block (lines 171-173):

```html
              {% elif action and action.type == 'time_off' %}
                <button type="button" class="row-btn primary js-time-off-approve">Approve</button>
                <input type="text" class="inline-input js-time-off-reason" placeholder="Reason to deny" hidden>
                <button type="button" class="row-btn danger js-time-off-refuse">Deny</button>
```

- [ ] **Step 3c: Update the refuse handler in `exceptions.js`**

Replace the `js-time-off-refuse` block (~line 451) in `src/zira_dashboard/static/exceptions.js`:

```javascript
    if (rowBtn.classList.contains('js-time-off-refuse')) {
      var reasonInput = row.querySelector('.js-time-off-reason');
      if (reasonInput && reasonInput.hidden) {
        reasonInput.hidden = false;
        reasonInput.focus();
        rowStatus(row, 'Enter a reason, then Deny again.', false);
        return;
      }
      var reason = reasonInput ? reasonInput.value.trim() : '';
      if (!reason) {
        rowStatus(row, 'A reason is required to deny.', true);
        if (reasonInput) reasonInput.focus();
        return;
      }
      setBusy(row, true);
      rowStatus(row, 'Denying...', false);
      postJson('/api/exceptions/time-off/' + encodeURIComponent(row.dataset.requestId) + '/refuse',
        {reason: reason, source: 'inbox'})
        .then(function (resp) {
          if (resp && resp.ok) resolveRow(row, 'Denied');
          else failRow(row, (resp && resp.error) || 'Deny failed.');
        }).catch(function () { failRow(row, 'Network error.'); });
      return;
    }
```

Also update the approve handler (~line 437) to send the source — change its
`postJson(...)` payload from `{}` to `{source: 'inbox'}`:

```javascript
      postJson('/api/exceptions/time-off/' + encodeURIComponent(row.dataset.requestId) + '/approve',
        {source: 'inbox'})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_exception_inbox.py -k "approvals_page or inline_deny or requires_reason" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/exception_inbox.py src/zira_dashboard/templates/exceptions.html src/zira_dashboard/static/exceptions.js tests/test_exception_inbox.py
git commit -m "Inbox time-off: link to approvals page, require deny reason"
```

---

### Task 9: Approvals page route + payload builder

**Files:**
- Create: `src/zira_dashboard/routes/time_off_approvals.py`
- Modify: `src/zira_dashboard/app.py` (import + `include_router`)
- Test: `tests/test_time_off_approvals.py` (create)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_time_off_approvals.py
from datetime import date

from fastapi.testclient import TestClient

from zira_dashboard.app import app
from zira_dashboard.routes import time_off_approvals as page


def test_pending_payload_attaches_balance_and_coverage(monkeypatch):
    monkeypatch.setattr(page, "_pending_rows", lambda today: [{
        "id": 55, "person_odoo_id": 7, "person_name": "Maria Delgado",
        "leave_type": "PTO", "holiday_status_id": 3,
        "date_from": date(2026, 6, 30), "date_to": date(2026, 7, 2),
        "hour_from": None, "hour_to": None, "state": "confirm",
    }])
    monkeypatch.setattr(page.time_off_context, "balance_for",
                        lambda pid, hsid: {"remaining": 24.0, "unit": "days"})
    monkeypatch.setattr(page.time_off_context, "coverage_for",
                        lambda pid, df, dt: {"count": 2, "scope": "department"})

    rows = page._pending_payload(date(2026, 6, 24))

    assert len(rows) == 1
    r = rows[0]
    assert r["person_name"] == "Maria Delgado"
    assert r["balance"] == {"remaining": 24.0, "unit": "days"}
    assert r["coverage"] == {"count": 2, "scope": "department"}
    assert r["over_balance"] is False
    assert r["past_due"] is False


def test_pending_payload_flags_over_balance_and_past_due(monkeypatch):
    monkeypatch.setattr(page, "_pending_rows", lambda today: [{
        "id": 56, "person_odoo_id": 8, "person_name": "Juan Morales",
        "leave_type": "Sick", "holiday_status_id": 4,
        "date_from": date(2026, 6, 20), "date_to": date(2026, 6, 20),
        "hour_from": 8.0, "hour_to": 12.0, "state": "confirm",
    }])
    monkeypatch.setattr(page.time_off_context, "balance_for",
                        lambda pid, hsid: {"remaining": 2.0, "unit": "hours"})
    monkeypatch.setattr(page.time_off_context, "coverage_for",
                        lambda pid, df, dt: {"count": 0, "scope": "department"})

    rows = page._pending_payload(date(2026, 6, 24))

    assert rows[0]["over_balance"] is True   # 4h request vs 2h left
    assert rows[0]["past_due"] is True       # date_to < today


def test_approvals_page_renders_200(monkeypatch):
    monkeypatch.setattr(page, "_pending_payload", lambda today: [])
    monkeypatch.setattr(page.time_off_audit, "recent_decisions", lambda days=30: [])
    client = TestClient(app)
    resp = client.get("/staffing/time-off/approvals")
    assert resp.status_code == 200
    assert "Time off approvals" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_approvals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zira_dashboard.routes.time_off_approvals'`

- [ ] **Step 3: Create the route module**

```python
# src/zira_dashboard/routes/time_off_approvals.py
"""Time Off Approvals page: GET /staffing/time-off/approvals.

The full pending-approvals workspace. Lists every pending request (no cap,
past-due flagged) with balance + department-scoped coverage context, plus a
recent-decisions history. Decisions POST to the existing
/api/exceptions/time-off/{id}/approve|refuse endpoints. Reads local mirrors
only — no live Odoo call on render.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import time_off_audit, time_off_context
from ..deps import templates
from ..plant_day import today as plant_today

router = APIRouter()

_PENDING_STATES = ("draft", "draft_edit", "confirm", "validate1")


def _pending_rows(today: date) -> list[dict]:
    """Every pending request in the mirror (no date filter), with person
    name + leave type, ordered by start date."""
    from .. import db

    return db.query(
        "SELECT r.id, r.person_odoo_id, r.holiday_status_id, r.shape, "
        "r.date_from, r.date_to, r.hour_from, r.hour_to, r.state, "
        "COALESCE(p.name, '#' || r.person_odoo_id::text) AS person_name, "
        "COALESCE(lt.name, 'Time off') AS leave_type "
        "FROM time_off_requests r "
        "LEFT JOIN people p ON p.odoo_id = r.person_odoo_id "
        "LEFT JOIN leave_types_cache lt ON lt.holiday_status_id = r.holiday_status_id "
        "WHERE r.state IN ('draft','draft_edit','confirm','validate1') "
        "ORDER BY r.date_from, lower(COALESCE(p.name, '#' || r.person_odoo_id::text))",
        (),
    )


def _pending_payload(today: date) -> list[dict]:
    """Attach balance, coverage, over-balance, and past-due flags to each
    pending row."""
    out = []
    for r in _pending_rows(today):
        balance = time_off_context.balance_for(r["person_odoo_id"], r["holiday_status_id"])
        coverage = time_off_context.coverage_for(
            r["person_odoo_id"], r["date_from"], r["date_to"])
        amount, unit = time_off_context.request_amount(r)
        over = bool(balance and balance["unit"] == unit
                    and amount > balance["remaining"])
        out.append({
            **r,
            "balance": balance,
            "coverage": coverage,
            "request_amount": amount,
            "request_unit": unit,
            "over_balance": over,
            "past_due": r["date_to"] < today,
            "awaiting_second": r["state"] == "validate1",
        })
    return out


@router.get("/staffing/time-off/approvals", response_class=HTMLResponse)
def time_off_approvals(request: Request):
    today = plant_today()
    return templates.TemplateResponse(
        request,
        "time_off_approvals.html",
        {
            "active": "time_off",
            "today_iso": today.isoformat(),
            "pending": _pending_payload(today),
            "recent": time_off_audit.recent_decisions(days=30),
        },
    )
```

Register it in `src/zira_dashboard/app.py` — add `time_off_approvals` to the
`from .routes import (...)` block, then add after `app.include_router(time_off.router)`:

```python
app.include_router(time_off_approvals.router)
```

- [ ] **Step 4: Run tests (the page-render test still needs the template — Task 10)**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_approvals.py -k payload -v`
Expected: PASS (the two `_pending_payload` tests). `test_approvals_page_renders_200` will fail until the template exists (Task 10).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/time_off_approvals.py src/zira_dashboard/app.py tests/test_time_off_approvals.py
git commit -m "Add time-off approvals page route and payload builder"
```

---

### Task 10: Approvals page template + JS, and nav link

**Files:**
- Create: `src/zira_dashboard/templates/time_off_approvals.html`
- Create: `src/zira_dashboard/static/time_off_approvals.js`
- Modify: `src/zira_dashboard/templates/exceptions.html` (already links here via the section href; no change) and the Staffing nav — add a link in `time_off_approvals.html`'s own header consistent with `exceptions.html`'s nav.
- Test: `tests/test_time_off_approvals.py` (the render test from Task 9 now passes)

- [ ] **Step 1: Confirm the failing render test**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_approvals.py::test_approvals_page_renders_200 -v`
Expected: FAIL — template `time_off_approvals.html` not found.

- [ ] **Step 2: Create the template**

```html
<!-- src/zira_dashboard/templates/time_off_approvals.html -->
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="/static/gpi-logo.png">
<title>Time Off Approvals — GPI Plant Manager</title>
<link rel="stylesheet" href="/static/exceptions.css?v={{ static_v('exceptions.css') }}">
</head>
<body>
<header class="app">
  <div class="brand-row">
    <a href="/recycling" class="brand">
      <img src="/static/gpi-logo.png" alt="GPI">
      <h1>Plant Manager</h1>
    </a>
    <nav>
      <a href="/recycling">Dashboards</a>
      <a href="/trophies">Trophy Case</a>
      <a href="/exceptions">Inbox</a>
      <a href="/staffing" class="active">Staffing</a>
      <a href="/settings">Settings</a>
    </nav>
  </div>
</header>

<main class="inbox-shell">
  <div class="inbox-title">
    <div>
      <h2>Time off approvals</h2>
      <p><span>{{ pending | length }}</span> pending</p>
    </div>
    <a class="refresh-btn" href="/staffing/time-off/approvals">Refresh</a>
  </div>

  <div class="section-stack">
    <section class="inbox-section info">
      <div class="section-head"><div><h3>Pending</h3></div></div>
      {% if pending %}
      <table>
        <tbody>
        {% for r in pending %}
          <tr class="exception-row" data-request-id="{{ r.id }}">
            <th scope="row">{{ r.person_name }}</th>
            <td>
              {{ r.leave_type }} · {{ r.date_from }}{% if r.date_to != r.date_from %} – {{ r.date_to }}{% endif %}
              {% if r.past_due %}<span class="priority-pill urgent">Past due</span>{% endif %}
              {% if r.awaiting_second %}<span class="priority-pill warn">Awaiting 2nd approval</span>{% endif %}
            </td>
            <td>
              {% if r.balance %}
                <span class="priority-pill {{ 'urgent' if r.over_balance else 'normal' }}">
                  {{ '%g' % r.balance.remaining }} {{ r.balance.unit }} left
                </span>
              {% else %}
                <span class="priority-pill normal">balance unknown</span>
              {% endif %}
              <span class="priority-pill {{ 'warn' if r.coverage.count else 'normal' }}">
                {% if r.coverage.count %}{{ r.coverage.count }} off{% else %}nobody off{% endif %}{% if r.coverage.scope == 'plant' %} (plant-wide){% endif %}
              </span>
            </td>
            <td class="row-actions">
              <button type="button" class="row-btn primary js-approve">Approve</button>
              <input type="text" class="inline-input js-reason" placeholder="Reason to deny" hidden>
              <button type="button" class="row-btn danger js-refuse">Deny</button>
              <span class="row-status" hidden></span>
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      {% else %}
        <div class="empty-row">All clear — nothing pending.</div>
      {% endif %}
    </section>

    <section class="inbox-section">
      <div class="section-head"><div><h3>Recently decided</h3></div></div>
      {% if recent %}
      <table>
        <tbody>
        {% for d in recent %}
          <tr class="exception-row">
            <th scope="row">{{ d.person_name }}</th>
            <td>{{ d.action | capitalize }} · {{ d.leave_type }} {{ d.date_from }}{% if d.date_to != d.date_from %}–{{ d.date_to }}{% endif %}
              {% if d.reason %}<div class="muted">"{{ d.reason }}"</div>{% endif %}
            </td>
            <td>by {{ d.actor_name or d.actor_upn or 'unknown' }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      {% else %}
        <div class="empty-row">No decisions in the last 30 days.</div>
      {% endif %}
    </section>
  </div>
</main>
{% include '_footer.html' %}
<script src="/static/time_off_approvals.js?v={{ static_v('time_off_approvals.js') }}"></script>
</body>
</html>
```

- [ ] **Step 3: Create the page JS**

```javascript
// src/zira_dashboard/static/time_off_approvals.js
(function () {
  function postJson(url, payload) {
    var fetcher = window.gpiFetch || window.fetch;
    return fetcher(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {}),
    }).then(function (r) { return r.json(); });
  }
  function status(row, text, isError) {
    var el = row.querySelector('.row-status');
    if (!el) return;
    row.classList.toggle('is-error', !!isError);
    el.hidden = false;
    el.textContent = text;
  }
  function busy(row, on) {
    row.querySelectorAll('button, input').forEach(function (el) { el.disabled = !!on; });
  }
  function done(row, text) {
    busy(row, true);
    status(row, text, false);
    row.style.opacity = '0.5';
  }
  document.addEventListener('click', function (event) {
    var btn = event.target.closest('.row-btn');
    if (!btn) return;
    var row = btn.closest('.exception-row');
    if (!row) return;
    var id = encodeURIComponent(row.dataset.requestId);

    if (btn.classList.contains('js-approve')) {
      busy(row, true);
      status(row, 'Approving...', false);
      postJson('/api/exceptions/time-off/' + id + '/approve', {source: 'page'})
        .then(function (resp) {
          if (resp && resp.ok && resp.approved === false) {
            status(row, 'Moved forward; refreshing...', false);
            setTimeout(function () { window.location.reload(); }, 600);
          } else if (resp && resp.ok) {
            done(row, 'Approved');
          } else {
            busy(row, false);
            status(row, (resp && resp.error) || 'Approval failed.', true);
          }
        }).catch(function () { busy(row, false); status(row, 'Network error.', true); });
      return;
    }

    if (btn.classList.contains('js-refuse')) {
      var input = row.querySelector('.js-reason');
      if (input && input.hidden) {
        input.hidden = false;
        input.focus();
        status(row, 'Enter a reason, then Deny again.', false);
        return;
      }
      var reason = input ? input.value.trim() : '';
      if (!reason) {
        status(row, 'A reason is required to deny.', true);
        if (input) input.focus();
        return;
      }
      busy(row, true);
      status(row, 'Denying...', false);
      postJson('/api/exceptions/time-off/' + id + '/refuse', {reason: reason, source: 'page'})
        .then(function (resp) {
          if (resp && resp.ok) done(row, 'Denied');
          else { busy(row, false); status(row, (resp && resp.error) || 'Deny failed.', true); }
        }).catch(function () { busy(row, false); status(row, 'Network error.', true); });
    }
  });
})();
```

- [ ] **Step 4: Run the full page test suite to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_approvals.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/time_off_approvals.html src/zira_dashboard/static/time_off_approvals.js
git commit -m "Add time-off approvals page template and JS"
```

---

### Task 11: Full-suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest -q`
Expected: PASS (no new failures vs. baseline; DATABASE_URL/Odoo-gated tests skip as usual).

- [ ] **Step 2: Lint**

Run: `.venv/bin/python -m ruff check src/zira_dashboard/time_off_audit.py src/zira_dashboard/time_off_context.py src/zira_dashboard/routes/time_off_approvals.py src/zira_dashboard/routes/exceptions.py`
Expected: no errors.

- [ ] **Step 3: Manual smoke (optional, requires a configured environment)**

Use the `run` skill or `verify` skill to load `/staffing/time-off/approvals`, confirm pending rows render with balance/coverage chips, deny requires a reason, and a decision appears under "Recently decided."

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -A
git commit -m "Tidy time-off approvals lint"
```

---

## Self-review notes (for the implementer)

- The `_load_time_off_request` change (Task 5) adds `person_name`/`leave_type` keys consumed by the audit calls in Tasks 6–7 and the page in Task 9. Keep those key names exact.
- The decision endpoints stay at `/api/exceptions/time-off/{id}/...`; both the inbox (`source: 'inbox'`) and the page (`source: 'page'`) POST to them.
- `record_decision` is always called with the same keyword set in Tasks 6 and 7 — do not drop any key, or the `time_off_audit.record_decision` signature (all keyword-only) will raise.
- Balance comparison only flags `over_balance` when the request unit matches the balance unit (`days` vs `hours`); a mismatch shows the balance without a warning, by design.
