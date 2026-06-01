# Block Overlapping Time-Off Requests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a kiosk time-off request that overlaps time off the worker already has from silently sticking in the errored state — catch it before posting and show a modal pop-up, with a self-healing backstop for the rare race and pre-existing stuck rows.

**Architecture:** A shared local-mirror overlap helper (`find_conflicting_request`) is called in two places: (1) a pre-check in the submit + edit route handlers that renders a modal instead of persisting/pushing on conflict, and (2) a re-check at the top of the Odoo create-push path that deletes phantom drafts that can never sync. A small `_details_context` helper removes the now-quadruplicated wizard-context dict so the conflict branch stays tiny.

**Tech Stack:** Python 3.x, FastAPI, Jinja2 templates, Postgres (via the repo's thin `db.query`/`db.execute` wrappers), pytest + `fastapi.testclient`. Spec: `docs/superpowers/specs/2026-06-01-time-off-overlap-block-design.md`.

**Local test note:** Per repo setup, the full suite can't run on the local Python (3.9; `fastapi` not importable). `tests/test_time_off_sync.py` (Tasks 1–2) may run locally; the route/integration tests (Tasks 4–6) use `TestClient` and run in **CI**. Each test step lists the command + expected result; if `fastapi` import fails locally, rely on CI for that step and verify by reasoning.

---

## File Structure

- `src/zira_dashboard/time_off_sync.py` — **modify.** Add `find_conflicting_request` (shared overlap query) and the re-check at the top of `_push_create`.
- `src/zira_dashboard/routes/timeclock_time_off.py` — **modify.** Add `_details_context` helper; route GET/error paths through it; add the conflict pre-check to `request_submit` and `mine_edit_submit`.
- `src/zira_dashboard/templates/timeclock_base.html` — **modify.** Add `.k-modal-overlay` / `.k-modal-card` styles next to `.k-error`.
- `src/zira_dashboard/templates/timeclock_time_off_request_details.html` — **modify.** Add the `{% if conflict %}` modal block.
- `src/zira_dashboard/timeclock_i18n.py` — **modify.** Add Spanish copy for the message + buttons.
- `tests/test_time_off_sync.py` — **modify.** Helper unit tests + push-path re-check tests.
- `tests/test_time_off_routes.py` — **modify.** Submit/edit conflict tests + an integration render test.

---

## Task 1: `find_conflicting_request` overlap helper

**Files:**
- Modify: `src/zira_dashboard/time_off_sync.py` (add after `_log` / the `_SYNC_ERROR_MSG_LIMIT` constant, ~line 66)
- Test: `tests/test_time_off_sync.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_time_off_sync.py` (the `fake_db` fixture already exists at the top of the file and captures `queries`/`executes`):

```python
def test_find_conflicting_request_returns_none_when_empty(fake_db):
    fake_db["query_result"] = []
    out = time_off_sync.find_conflicting_request(
        5, date(2026, 6, 1), date(2026, 6, 3))
    assert out is None
    sql, params = fake_db["queries"][-1]
    assert params[0] == 5
    assert date(2026, 6, 1) in params and date(2026, 6, 3) in params
    assert "state IN" in sql


def test_find_conflicting_request_returns_first_row(fake_db):
    fake_db["query_result"] = [{
        "id": 9, "state": "validate", "synced_to_odoo": True,
        "date_from": date(2026, 6, 2), "date_to": date(2026, 6, 2),
    }]
    out = time_off_sync.find_conflicting_request(
        5, date(2026, 6, 1), date(2026, 6, 3))
    assert out["id"] == 9


def test_find_conflicting_request_exclude_rid_in_sql_and_params(fake_db):
    fake_db["query_result"] = []
    time_off_sync.find_conflicting_request(
        5, date(2026, 6, 1), date(2026, 6, 3), exclude_rid=42)
    sql, params = fake_db["queries"][-1]
    assert "id <> %s" in sql
    assert 42 in params


def test_find_conflicting_request_established_only_clause(fake_db):
    fake_db["query_result"] = []
    time_off_sync.find_conflicting_request(
        5, date(2026, 6, 1), date(2026, 6, 3),
        exclude_rid=42, established_only=True)
    sql, params = fake_db["queries"][-1]
    assert "synced_to_odoo = TRUE OR id < %s" in sql
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_time_off_sync.py -k find_conflicting -v`
Expected: FAIL — `AttributeError: module 'zira_dashboard.time_off_sync' has no attribute 'find_conflicting_request'`.

- [ ] **Step 3: Implement the helper**

In `src/zira_dashboard/time_off_sync.py`, after the `_SYNC_ERROR_MSG_LIMIT` constant (~line 66), add:

```python
def find_conflicting_request(
    person_odoo_id: int,
    date_from: date,
    date_to: date,
    exclude_rid: int | None = None,
    established_only: bool = False,
) -> dict | None:
    """First non-rejected ``time_off_requests`` row for ``person_odoo_id``
    whose ``[date_from, date_to]`` overlaps the given range, else None.

    Date-level, type-agnostic overlap — mirrors Odoo's own "no two leaves on
    the same day for one employee" constraint, but caught locally *before* we
    post so an overlap never sticks in the errored state. Scoped to the same
    person only; never blocks against a coworker's time off.

    ``exclude_rid``      skip this row id (an edit can't conflict with itself).
    ``established_only``  push-path mode: only count a row as a conflict if it
                          is already synced (``synced_to_odoo = TRUE``) OR was
                          created earlier (``id < exclude_rid``). Stops two
                          simultaneous duplicate drafts from deleting each
                          other — the earlier/established one wins.
    """
    sql = (
        "SELECT id, state, synced_to_odoo, date_from, date_to "
        "FROM time_off_requests "
        "WHERE person_odoo_id = %s "
        "AND state IN ('draft','draft_edit','confirm','validate1','validate') "
        "AND date_to >= %s AND date_from <= %s"
    )
    params: list[Any] = [person_odoo_id, date_from, date_to]
    if exclude_rid is not None:
        sql += " AND id <> %s"
        params.append(exclude_rid)
    if established_only:
        # exclude_rid is always supplied in this mode (the row being pushed).
        sql += " AND (synced_to_odoo = TRUE OR id < %s)"
        params.append(exclude_rid)
    sql += " ORDER BY id LIMIT 1"
    rows = db.query(sql, tuple(params))
    return rows[0] if rows else None
```

(`Any` and `date` are already imported at the top of the module.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_time_off_sync.py -k find_conflicting -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/time_off_sync.py tests/test_time_off_sync.py
git commit -m "feat(time-off): add find_conflicting_request overlap helper

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Self-healing re-check on the create push path

**Files:**
- Modify: `src/zira_dashboard/time_off_sync.py:112` (`_push_create`, insert at the top of the function body)
- Test: `tests/test_time_off_sync.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_time_off_sync.py`:

```python
def test_push_create_deletes_phantom_when_established_conflict(monkeypatch, fake_db):
    """An established overlapping row exists → the create can never succeed in
    Odoo, so delete the phantom draft instead of looping on sync_error."""
    fake_db["query_result"] = [{
        "id": 7, "person_odoo_id": 5, "shape": "full_day",
        "holiday_status_id": 1, "date_from": date(2026, 6, 1),
        "date_to": date(2026, 6, 3), "hour_from": None, "hour_to": None,
        "note": None, "state": "draft", "odoo_leave_id": None,
    }]
    monkeypatch.setattr(time_off_sync, "find_conflicting_request",
                        lambda *a, **k: {"id": 99})
    mock_create = MagicMock()
    monkeypatch.setattr(time_off_sync.odoo_client, "create_leave", mock_create)
    monkeypatch.setattr(time_off_sync.odoo_client, "find_duplicate_leave",
                        MagicMock(return_value=None))

    time_off_sync.push_one(7)

    mock_create.assert_not_called()
    deletes = [e for e in fake_db["executes"]
               if "DELETE FROM time_off_requests" in e[0]]
    assert deletes, "expected the phantom row to be DELETEd"
    assert deletes[0][1] == (7,)


def test_push_create_proceeds_when_no_conflict(monkeypatch, fake_db):
    fake_db["query_result"] = [{
        "id": 7, "person_odoo_id": 5, "shape": "full_day",
        "holiday_status_id": 1, "date_from": date(2026, 6, 1),
        "date_to": date(2026, 6, 3), "hour_from": None, "hour_to": None,
        "note": None, "state": "draft", "odoo_leave_id": None,
    }]
    monkeypatch.setattr(time_off_sync, "find_conflicting_request",
                        lambda *a, **k: None)
    mock_create = MagicMock(return_value=555)
    monkeypatch.setattr(time_off_sync.odoo_client, "create_leave", mock_create)
    monkeypatch.setattr(time_off_sync.odoo_client, "find_duplicate_leave",
                        MagicMock(return_value=None))
    monkeypatch.setattr(time_off_sync.odoo_client, "confirm_leave", MagicMock())

    time_off_sync.push_one(7)

    mock_create.assert_called_once()
    assert not any("DELETE FROM time_off_requests" in e[0]
                   for e in fake_db["executes"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_time_off_sync.py -k push_create -v`
Expected: FAIL — `test_push_create_deletes_phantom_when_established_conflict` fails because `create_leave` IS called (no re-check yet) and no DELETE is issued.

- [ ] **Step 3: Implement the re-check**

In `src/zira_dashboard/time_off_sync.py`, at the very top of `_push_create` (right after the docstring, before the `hour_from = ...` line ~118), insert:

```python
    # Backstop + cleanup: if an established overlapping request now exists in
    # the local mirror, this create can never succeed in Odoo (Odoo rejects
    # overlaps). Delete the phantom draft instead of looping on sync_error —
    # this also clears rows already stuck from before the pre-check existed.
    # established_only so two simultaneous duplicate drafts don't delete each
    # other (the earlier/already-synced one wins).
    conflict = find_conflicting_request(
        row["person_odoo_id"], row["date_from"], row["date_to"],
        exclude_rid=row["id"], established_only=True,
    )
    if conflict is not None:
        _log.info(
            "push_create: row %s overlaps established row %s — deleting phantom",
            row["id"], conflict["id"],
        )
        db.execute("DELETE FROM time_off_requests WHERE id = %s", (row["id"],))
        return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_time_off_sync.py -k "push_create or find_conflicting" -v`
Expected: PASS. Also run the whole file to confirm no regression in the existing create tests: `pytest tests/test_time_off_sync.py -v` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/time_off_sync.py tests/test_time_off_sync.py
git commit -m "feat(time-off): self-healing overlap re-check on create push path

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Extract `_details_context` (behavior-preserving refactor)

The wizard-context dict is currently built four times (GET details, submit time-error re-render, edit GET, edit time-error re-render). The conflict branch (Tasks 4–5) would add a fifth. Extract it first so those branches stay tiny.

**Files:**
- Modify: `src/zira_dashboard/routes/timeclock_time_off.py` (add helper near `_refresh_and_load_balances` ~line 296; reroute four call sites)

This task is a **behavior-preserving refactor — no new test.** It's covered by the existing route tests (CI) and exercised end-to-end by the render test added in Task 6. Verify by reading the diffs against the originals.

- [ ] **Step 1: Add the `_details_context` helper**

In `src/zira_dashboard/routes/timeclock_time_off.py`, after `_refresh_and_load_balances` (ends ~line 309), add:

```python
def _details_context(
    p: dict,
    token: str,
    shape: str,
    *,
    balances: list[dict] | None = None,
    shift_from: float | None = None,
    shift_to: float | None = None,
) -> dict:
    """Build the shared render context for the request-details wizard.

    Used by the GET form, both submit/edit time-validation re-renders, and
    the overlap-conflict re-render so the context is defined in one place.
    Callers layer on ``error`` / ``conflict`` / ``edit_mode`` / ``edit_rid`` /
    ``prefill`` afterward.

    ``balances``   pass pre-loaded balances (the GET path refreshes from Odoo
                   first); when None, loads the cached balances.
    ``shift_from`` / ``shift_to``  pass when the caller already computed the
                   shift window (the time-error paths); when None, computed
                   here from the employee's shift.
    """
    odoo_id = p["odoo_id"]
    if shift_from is None or shift_to is None:
        shift_from, shift_to = _shift_window_for(odoo_id)
    if balances is None:
        balances = time_off_balances.get_for_employee(odoo_id)
    types = _fetch_visible_leave_types(shape)
    balances_by_type = {
        b["holiday_status_id"]: {
            "unit": b["unit"],
            "available": float(b["available"]),
            "available_practical": float(b["available_practical"]),
            "pending": float(b["pending"]),
        }
        for b in balances
    }
    partial_day_type = types[0] if (shape != "full_day" and types) else None
    return {
        "person": p,
        "token": token,
        "shape": shape,
        "leave_types": types,
        "partial_day_type": partial_day_type,
        "balances_by_type": balances_by_type,
        "shift_from": shift_from,
        "shift_to": shift_to,
        "today_iso": _date.today().isoformat(),
        "work_weekdays": sorted(schedule_store.current().work_weekdays),
        "bilingual": bool(p.get("spanish_speaker")),
    }
```

- [ ] **Step 2: Reroute the GET details handler**

In `request_details` (~lines 364–403), replace everything from `fresh = _mint_token(person_id)` through the `return templates.TemplateResponse(...)` block with:

```python
    fresh = _mint_token(person_id)
    balances = _refresh_and_load_balances(p["odoo_id"])
    ctx = _details_context(p, fresh, shape, balances=balances)
    return templates.TemplateResponse(
        request, "timeclock_time_off_request_details.html", ctx,
    )
```

- [ ] **Step 3: Reroute the submit time-error re-render**

In `request_submit`, the `if err:` block (~lines 620–656) becomes:

```python
    if err:
        # Re-render the details form with the error in the existing k-error
        # banner, reusing the shift window already computed above.
        ctx = _details_context(
            p, _mint_token(person_id), shape,
            shift_from=shift_from, shift_to=shift_to,
        )
        ctx["error"] = err
        return templates.TemplateResponse(
            request, "timeclock_time_off_request_details.html", ctx,
            status_code=422,
        )
```

- [ ] **Step 4: Reroute the edit GET handler**

In `mine_edit` (~lines 937–992), replace from `fresh = _mint_token(person_id)` through the `return templates.TemplateResponse(...)` block with:

```python
    fresh = _mint_token(person_id)
    balances = _refresh_and_load_balances(p["odoo_id"])
    ctx = _details_context(p, fresh, row["shape"], balances=balances)
    ctx.update({
        "edit_mode": True,
        "edit_rid": rid,
        "prefill": {
            "holiday_status_id": row["holiday_status_id"],
            "date_from": row["date_from"].isoformat() if row["date_from"] else "",
            "date_to": row["date_to"].isoformat() if row["date_to"] else "",
            "hour_from": (
                float(row["hour_from"]) if row["hour_from"] is not None else None
            ),
            "hour_to": (
                float(row["hour_to"]) if row["hour_to"] is not None else None
            ),
            "note": row["note"] or "",
        },
    })
    return templates.TemplateResponse(
        request, "timeclock_time_off_request_details.html", ctx,
    )
```

- [ ] **Step 5: Reroute the edit time-error re-render**

In `mine_edit_submit`, the `if err:` block (~lines 1057–1096) becomes:

```python
    if err:
        ctx = _details_context(
            p, _mint_token(person_id), shape,
            shift_from=shift_from, shift_to=shift_to,
        )
        ctx.update({"edit_mode": True, "edit_rid": rid, "error": err})
        return templates.TemplateResponse(
            request, "timeclock_time_off_request_details.html", ctx,
            status_code=422,
        )
```

- [ ] **Step 6: Verify no regression**

Run: `pytest tests/test_time_off_routes.py -v`
Expected: PASS (existing tests unchanged — gate-fail redirects, the edit happy-path, etc.). If `fastapi` won't import locally, this runs in CI; verify by confirming each rerouted block produces the same context keys as the original (`person, token, shape, leave_types, partial_day_type, balances_by_type, shift_from, shift_to, today_iso, work_weekdays, bilingual` + the per-site extras).

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/routes/timeclock_time_off.py
git commit -m "refactor(time-off): extract _details_context for the request wizard

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Block overlapping requests at submit

**Files:**
- Modify: `src/zira_dashboard/routes/timeclock_time_off.py` (`request_submit`, insert after the date-normalization block ~line 614)
- Test: `tests/test_time_off_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_time_off_routes.py`:

```python
def test_submit_blocks_overlapping_request(monkeypatch):
    """A submit that overlaps an existing request posts nothing, queues no
    push, and re-renders with conflict=True at HTTP 409."""
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._verify_token", lambda t: 1)
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._person_by_id",
        lambda pid: {"id": 1, "name": "T", "odoo_id": 5, "spanish_speaker": False})
    # A conflicting request exists in the mirror.
    monkeypatch.setattr(
        "zira_dashboard.time_off_sync.find_conflicting_request",
        lambda *a, **k: {"id": 99})
    # Capture the render context instead of rendering Jinja.
    captured = {}

    def fake_tr(request, name, context, status_code=200):
        captured["name"] = name
        captured["context"] = context
        captured["status"] = status_code
        from fastapi.responses import HTMLResponse
        return HTMLResponse("conflict", status_code=status_code)

    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off.templates.TemplateResponse",
        fake_tr)
    # These MUST NOT run on the conflict path.
    inserted = []
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._insert_request_row",
        lambda **kw: inserted.append(kw) or 1)
    queued = []
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._queue_push",
        lambda rid: queued.append(rid))
    # _details_context dependencies — stub so no real DB / Odoo.
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._shift_window_for",
        lambda pid: (6.0, 14.5))
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._fetch_visible_leave_types",
        lambda shape: [{"id": 1, "name": "PTO",
                        "request_unit": "day", "requires_allocation": "no"}])
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off.time_off_balances.get_for_employee",
        lambda pid: [])
    import types as _types
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off.schedule_store.current",
        lambda: _types.SimpleNamespace(work_weekdays=[0, 1, 2, 3, 4]))

    client = TestClient(app)
    r = client.post(
        "/timeclock/time-off/request/anytoken/submit",
        data={"shape": "full_day", "holiday_status_id": "1",
              "date_from": "2026-06-01", "date_to": "2026-06-03", "note": ""},
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert captured["context"].get("conflict") is True
    assert inserted == []
    assert queued == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_time_off_routes.py::test_submit_blocks_overlapping_request -v`
Expected: FAIL — no conflict check yet, so the row is inserted (`inserted != []`) / push queued and the status is the success render, not 409.

- [ ] **Step 3: Add the pre-check**

In `request_submit`, immediately after the date-normalization block (the `if shape != "full_day": dt = df` / `elif dt < df: df, dt = dt, df` block ending ~line 614) and **before** `shift_from, shift_to = _shift_window_for(p["odoo_id"])`, insert:

```python
    # Block a request that overlaps time off this person already has. Don't
    # post it — Odoo would reject the overlap and the row would stick in the
    # errored state. Re-render with a modal instead. Checked against the local
    # mirror, which also catches the worker's own just-submitted draft that
    # hasn't synced yet (a rapid double-tap).
    if time_off_sync.find_conflicting_request(p["odoo_id"], df, dt) is not None:
        ctx = _details_context(p, _mint_token(person_id), shape)
        ctx["conflict"] = True
        return templates.TemplateResponse(
            request, "timeclock_time_off_request_details.html", ctx,
            status_code=409,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_time_off_routes.py::test_submit_blocks_overlapping_request -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/timeclock_time_off.py tests/test_time_off_routes.py
git commit -m "feat(time-off): block overlapping requests at submit with a modal

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Block overlapping edits

**Files:**
- Modify: `src/zira_dashboard/routes/timeclock_time_off.py` (`mine_edit_submit`, insert after the date-normalization block ~line 1051)
- Test: `tests/test_time_off_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_time_off_routes.py`:

```python
def test_edit_blocks_overlapping_request(monkeypatch):
    """Editing a request onto dates that overlap a DIFFERENT request blocks
    with conflict=True at 409 and does not update or queue a push. The check
    passes exclude_rid so a request never conflicts with itself."""
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._verify_token", lambda t: 1)
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._person_by_id",
        lambda pid: {"id": 1, "name": "T", "odoo_id": 5, "spanish_speaker": False})
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._load_request",
        lambda rid, pid: {"id": rid, "person_odoo_id": pid,
                          "shape": "full_day", "state": "confirm",
                          "odoo_leave_id": 999, "holiday_status_id": 1})
    seen_exclude = {}

    def fake_conflict(person_odoo_id, date_from, date_to,
                      exclude_rid=None, established_only=False):
        seen_exclude["rid"] = exclude_rid
        return {"id": 77}

    monkeypatch.setattr(
        "zira_dashboard.time_off_sync.find_conflicting_request", fake_conflict)
    captured = {}

    def fake_tr(request, name, context, status_code=200):
        captured["context"] = context
        captured["status"] = status_code
        from fastapi.responses import HTMLResponse
        return HTMLResponse("conflict", status_code=status_code)

    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off.templates.TemplateResponse",
        fake_tr)
    updated = []
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._update_request_row",
        lambda **kw: updated.append(kw))
    queued = []
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._queue_push",
        lambda rid: queued.append(rid))
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._shift_window_for",
        lambda pid: (6.0, 14.5))
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._fetch_visible_leave_types",
        lambda shape: [{"id": 1, "name": "PTO",
                        "request_unit": "day", "requires_allocation": "no"}])
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off.time_off_balances.get_for_employee",
        lambda pid: [])
    import types as _types
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off.schedule_store.current",
        lambda: _types.SimpleNamespace(work_weekdays=[0, 1, 2, 3, 4]))

    client = TestClient(app)
    r = client.post(
        "/timeclock/time-off/mine/anytoken/42/edit",
        data={"shape": "full_day", "holiday_status_id": "1",
              "date_from": "2026-06-10", "date_to": "2026-06-12", "note": ""},
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert captured["context"].get("conflict") is True
    assert captured["context"].get("edit_mode") is True
    assert seen_exclude["rid"] == 42
    assert updated == []
    assert queued == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_time_off_routes.py::test_edit_blocks_overlapping_request -v`
Expected: FAIL — no edit conflict check yet, so the row updates / push queues and the status isn't 409.

- [ ] **Step 3: Add the pre-check**

In `mine_edit_submit`, immediately after the date-normalization block (the `if shape != "full_day": dt = df` / `elif dt < df: df, dt = dt, df` block ending ~line 1051) and **before** `shift_from, shift_to = _shift_window_for(p["odoo_id"])`, insert:

```python
    # Same overlap guard as the new-request submit, excluding this row so an
    # edit never conflicts with itself.
    if time_off_sync.find_conflicting_request(
            p["odoo_id"], df, dt, exclude_rid=rid) is not None:
        ctx = _details_context(p, _mint_token(person_id), shape)
        ctx.update({"edit_mode": True, "edit_rid": rid, "conflict": True})
        return templates.TemplateResponse(
            request, "timeclock_time_off_request_details.html", ctx,
            status_code=409,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_time_off_routes.py::test_edit_blocks_overlapping_request -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/timeclock_time_off.py tests/test_time_off_routes.py
git commit -m "feat(time-off): block overlapping edits with the same modal

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Modal markup, styles, and Spanish copy

**Files:**
- Modify: `src/zira_dashboard/templates/timeclock_base.html` (CSS after the `.k-warning` block ~line 116)
- Modify: `src/zira_dashboard/templates/timeclock_time_off_request_details.html` (modal block after the `{% if error %}` block ~line 31)
- Modify: `src/zira_dashboard/timeclock_i18n.py` (after `"Cancel This Request": "Cancelar esta solicitud",` ~line 106)
- Test: `tests/test_time_off_routes.py`

- [ ] **Step 1: Write the failing integration test**

This renders the **real** template (no `TemplateResponse` stub) to prove the modal block, context, and English copy wire together. Add to `tests/test_time_off_routes.py`:

```python
def test_submit_conflict_renders_modal(monkeypatch):
    """Real-render path: the conflict response contains the modal message and
    a My Requests link."""
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._verify_token", lambda t: 1)
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._person_by_id",
        lambda pid: {"id": 1, "name": "T", "odoo_id": 5, "spanish_speaker": False})
    monkeypatch.setattr(
        "zira_dashboard.time_off_sync.find_conflicting_request",
        lambda *a, **k: {"id": 99})
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._shift_window_for",
        lambda pid: (6.0, 14.5))
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._fetch_visible_leave_types",
        lambda shape: [{"id": 1, "name": "PTO",
                        "request_unit": "day", "requires_allocation": "no"}])
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off.time_off_balances.get_for_employee",
        lambda pid: [])
    import types as _types
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off.schedule_store.current",
        lambda: _types.SimpleNamespace(work_weekdays=[0, 1, 2, 3, 4]))
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._insert_request_row",
        lambda **kw: 1)
    monkeypatch.setattr(
        "zira_dashboard.routes.timeclock_time_off._queue_push", lambda rid: None)

    client = TestClient(app)
    r = client.post(
        "/timeclock/time-off/request/anytoken/submit",
        data={"shape": "full_day", "holiday_status_id": "1",
              "date_from": "2026-06-01", "date_to": "2026-06-03", "note": ""},
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert "You already have time off for this time" in r.text
    assert "/timeclock/time-off/mine/" in r.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_time_off_routes.py::test_submit_conflict_renders_modal -v`
Expected: FAIL — the template has no modal block yet, so the message string isn't in `r.text`.

- [ ] **Step 3: Add the modal CSS**

In `src/zira_dashboard/templates/timeclock_base.html`, find the `.k-warning` block (~lines 111–116):

```css
  .k-warning {
    background: #fef3c7; color: #92400e;
    padding: 1rem 1.5rem; border-radius: 0.5rem;
    font-size: 1.25rem; margin-bottom: 1.5rem;
    border: 1px solid #fde68a;
  }
```

Insert immediately **after** it:

```css
  .k-modal-overlay {
    position: fixed; inset: 0; z-index: 1000;
    background: rgba(15, 23, 42, 0.55);
    display: flex; align-items: center; justify-content: center;
    padding: 1.5rem;
  }
  .k-modal-card {
    background: #ffffff; border-radius: 0.75rem;
    padding: 2rem; max-width: 560px; width: 100%;
    box-shadow: 0 10px 40px rgba(15, 23, 42, 0.3);
    text-align: center;
  }
  .k-modal-card p {
    font-size: 1.4rem; color: #0f172a; line-height: 1.4;
    margin: 0 0 1.75rem 0;
  }
  .k-modal-actions {
    display: flex; flex-direction: column; gap: 0.75rem;
  }
```

- [ ] **Step 4: Add the modal block to the details template**

In `src/zira_dashboard/templates/timeclock_time_off_request_details.html`, find the error banner block (~lines 29–31):

```html
    {% if error %}
      <div class="k-error">{{ error }}</div>
    {% endif %}
```

Insert immediately **after** it:

```html
    {% if conflict %}
      <div class="k-modal-overlay" id="conflict-modal">
        <div class="k-modal-card">
          <p>{{ t("You already have time off for this time so we can't add a second. Either cancel your request via the My Requests button or contact management for help.") }}</p>
          <div class="k-modal-actions">
            <a class="k-btn" href="/timeclock/time-off/mine/{{ token }}">{{ t("Go to My Requests") }}</a>
            <button type="button" class="k-btn secondary"
                    onclick="document.getElementById('conflict-modal').style.display='none'">
              {{ t("OK") }}
            </button>
          </div>
        </div>
      </div>
    {% endif %}
```

- [ ] **Step 5: Add the Spanish copy**

In `src/zira_dashboard/timeclock_i18n.py`, find (~line 106):

```python
    "Cancel This Request": "Cancelar esta solicitud",
```

Insert immediately **after** it:

```python
    # --- time off: overlap conflict modal ---
    "You already have time off for this time so we can't add a second. Either cancel your request via the My Requests button or contact management for help.":
        "Ya tienes tiempo libre para estas fechas, así que no podemos agregar otro. Cancela tu solicitud con el botón Mis solicitudes o comunícate con la gerencia para obtener ayuda.",
    "Go to My Requests": "Ir a Mis solicitudes",
    "OK": "Aceptar",
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_time_off_routes.py::test_submit_conflict_renders_modal -v`
Expected: PASS.

- [ ] **Step 7: Run the full touched test files**

Run: `pytest tests/test_time_off_routes.py tests/test_time_off_sync.py -v`
Expected: all PASS (no regressions). If `fastapi` won't import locally, run in CI.

- [ ] **Step 8: Commit**

```bash
git add src/zira_dashboard/templates/timeclock_base.html \
        src/zira_dashboard/templates/timeclock_time_off_request_details.html \
        src/zira_dashboard/timeclock_i18n.py tests/test_time_off_routes.py
git commit -m "feat(time-off): overlap modal markup, styles, and Spanish copy

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Manual verification (after all tasks)

On the kiosk (or a Railway preview):

1. As a worker with an existing approved/pending request, start a new request over the same date(s) → the **modal pops**, nothing is saved, the My Requests badge count is unchanged.
2. Tap **Go to My Requests** → lands on the list; open the original and **Cancel** it.
3. Re-submit the same dates → now **succeeds** (success page), and it appears in My Requests as Pending.
4. Repeat for the **Edit** flow: edit a request onto a date that collides with a different request → modal; edit it back to its own dates → allowed.
5. As a Spanish-speaking worker, confirm the modal renders the Spanish copy stacked under the English.

---

## Self-Review

**1. Spec coverage:**
- Pre-check at submit → Task 4. Pre-check at edit (with `exclude_rid`) → Task 5. ✓
- Modal pop-up + "Go to My Requests"/"OK" → Task 6. ✓
- Verbatim bilingual message via `t()` → Task 6 (template + i18n). ✓
- Local-mirror, non-rejected states, date-level overlap → Task 1 helper. ✓
- Self-healing push-path re-check (race backstop) + cleanup of existing stuck rows → Task 2 (`established_only` delete-phantom; existing stuck rows clear on next sweep because the leave they collided with is already mirrored). ✓
- "Surgical" (transient failures untouched) → Task 2 only deletes when a real local conflict exists; otherwise `_push_create` proceeds. ✓
- Tests for helper + both flows → Tasks 1, 2, 4, 5, 6. ✓
- `_details_context` tidy (avoid a 5th context copy) → Task 3. ✓
- Out-of-scope items (other Odoo rejections keep retrying; rare-race silent delete; no input prefill on re-render) → unchanged by this plan. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to" — every code and test step is complete. ✓

**3. Type/name consistency:** `find_conflicting_request(person_odoo_id, date_from, date_to, exclude_rid=None, established_only=False)` — same signature in Task 1 (def), Task 2 (`established_only=True`, `exclude_rid=row["id"]`), Task 4 (positional dates), Task 5 (`exclude_rid=rid`). `_details_context(p, token, shape, *, balances=None, shift_from=None, shift_to=None)` — same across Tasks 3–5. Context flags `conflict` / `edit_mode` / `edit_rid` match between the route handlers (Tasks 4–5) and the template block (Task 6). The template `{% if conflict %}` reads `token` (provided by `_details_context`). ✓
