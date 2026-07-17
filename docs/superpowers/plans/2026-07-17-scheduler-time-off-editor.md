# Scheduler Time-Off Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Allow supervisors to edit a scheduler-visible time-off date/time or cancel it from the scheduler, synchronize the same Odoo leave, and immediately refresh availability.

**Architecture:** The scheduler read model gains a request id and minimal date/time metadata. Narrow FastAPI endpoints stage the existing \`draft_edit\` / \`draft_cancel\` lifecycle and reuse \`time_off_sync.push_one\`; a native scheduler dialog calls those endpoints. The Odoo poller also performs a bounded full reconciliation for today, so a hard-deleted leave is removed within the next 60-second tick.

**Tech Stack:** FastAPI, Jinja, vanilla JavaScript/CSS, PostgreSQL, Odoo XML-RPC, pytest.

## Global Constraints

- Edit only date range and partial-day time window; never edit employee, leave type, note, or approval workflow.
- Scheduler browser payloads must not expose leave type or note.
- Only Odoo-backed, currently scheduler-visible requests overlapping the selected day are editable.
- Preserve kiosk self-service routes and the existing 60-second retry worker.
- Successful local mutations reload the current scheduler day; Odoo failure remains queued and visibly pending.

---

## File Structure

- Modify \`src/zira_dashboard/scheduler_time_off.py\`: add safe identity/edit metadata to time-off entries.
- Modify \`src/zira_dashboard/routes/staffing.py\`: validate and stage supervisor edit/cancel JSON endpoints.
- Modify \`src/zira_dashboard/time_off_sync.py\`: reconcile today’s hard-deleted leaves every polling tick.
- Modify \`src/zira_dashboard/templates/staffing.html\`, \`static/staffing.js\`, and \`static/staffing.css\`: Time Off editor dialog and interaction.
- Modify \`tests/test_scheduler_time_off.py\`, \`tests/test_time_off_sync.py\`; create \`tests/test_staffing_time_off_editor.py\`; modify \`tests/test_staffing_static.py\`.

### Task 1: Add safe scheduler editor metadata

**Files:**
- Modify: \`src/zira_dashboard/scheduler_time_off.py:52-105\`
- Test: \`tests/test_scheduler_time_off.py\`

**Interfaces:**
- Produces \`request_id: int\`, \`date_from: str\`, \`date_to: str\`, \`hour_from: float | None\`, \`hour_to: float | None\`, and \`editable: bool\` per time-off entry.

- [ ] **Step 1: Write the failing test**

\`\`\`python
def test_scheduler_entry_exposes_editor_metadata_without_note(monkeypatch):
    monkeypatch.setattr(sto, "_cleared_partial_names", lambda _day: set())
    monkeypatch.setattr(sto, "_rows_for_day", lambda _day: [{
        "request_id": 91, "name": "Jose Luis", "shape": "midday_gap",
        "hour_from": 9.0, "hour_to": 11.0, "state": "validate",
        "date_from": date(2026, 7, 17), "date_to": date(2026, 7, 17),
        "odoo_leave_id": 701, "local_record": False, "note": "private",
        "pay_type": "Vacation",
    }])
    entry = sto.time_off_entries_for_day(date(2026, 7, 17))[0]
    assert entry["request_id"] == 91
    assert entry["date_from"] == "2026-07-17"
    assert entry["hour_to"] == 11.0
    assert entry["editable"] is True
    assert "note" not in entry
\`\`\`

- [ ] **Step 2: Verify red**

Run: \`pytest tests/test_scheduler_time_off.py::test_scheduler_entry_exposes_editor_metadata_without_note -v\`

Expected: FAIL because the query and output omit editor metadata.

- [ ] **Step 3: Implement the minimal read-model change**

Extend \`_rows_for_day\` with \`r.id AS request_id, r.date_from, r.date_to, r.odoo_leave_id, r.local_record\`. Add only this output:

\`\`\`python
"request_id": int(r["request_id"]),
"date_from": r["date_from"].isoformat(),
"date_to": r["date_to"].isoformat(),
"hour_from": float(r["hour_from"]) if r["hour_from"] is not None else None,
"hour_to": float(r["hour_to"]) if r["hour_to"] is not None else None,
"editable": bool(r["odoo_leave_id"]) and not bool(r["local_record"]),
\`\`\`

Never copy \`note\` into the output dict.

- [ ] **Step 4: Verify green**

Run: \`pytest tests/test_scheduler_time_off.py -v\`

Expected: PASS.

- [ ] **Step 5: Commit**

\`\`\`bash
git add src/zira_dashboard/scheduler_time_off.py tests/test_scheduler_time_off.py
git commit -m "feat: expose scheduler time-off editor metadata"
\`\`\`

### Task 2: Add supervisor edit and cancel endpoints

**Files:**
- Modify: \`src/zira_dashboard/routes/staffing.py:1-70, 2050-2180\`
- Create: \`tests/test_staffing_time_off_editor.py\`

**Interfaces:**
- Consumes \`{day, date_from, date_to, time_from?, time_to?}\`.
- Produces \`POST /api/staffing/time-off/{request_id}/edit\` and \`POST /api/staffing/time-off/{request_id}/cancel\`.
- Uses \`_queue_time_off_push(request_id)\` as a testable \`time_off_sync.push_one\` wrapper.

- [ ] **Step 1: Write failing route tests**

\`\`\`python
def test_supervisor_edit_stages_same_odoo_leave_and_queues_push(monkeypatch):
    staged, queued = [], []
    monkeypatch.setattr(staffing_routes, "_editable_time_off_for_day", lambda rid, day: {
        "id": rid, "shape": "midday_gap", "holiday_status_id": 5,
        "odoo_leave_id": 701, "date_from": date(2026, 7, 17),
        "date_to": date(2026, 7, 17),
    })
    monkeypatch.setattr(staffing_routes, "_stage_supervisor_time_off_edit",
                        lambda **kwargs: staged.append(kwargs))
    monkeypatch.setattr(staffing_routes, "_queue_time_off_push", queued.append)
    response = staffing_routes._edit_scheduler_time_off(91, {
        "day": "2026-07-17", "date_from": "2026-07-18", "date_to": "2026-07-18",
        "time_from": "09:00", "time_to": "11:00",
    })
    assert response.status_code == 200
    assert staged[0]["request_id"] == 91
    assert staged[0]["holiday_status_id"] == 5
    assert queued == [91]

def test_supervisor_cancel_stages_cancel_and_queues_push(monkeypatch):
    staged, queued = [], []
    monkeypatch.setattr(staffing_routes, "_editable_time_off_for_day",
                        lambda rid, day: {"id": rid, "odoo_leave_id": 701})
    monkeypatch.setattr(staffing_routes, "_stage_supervisor_time_off_cancel", staged.append)
    monkeypatch.setattr(staffing_routes, "_queue_time_off_push", queued.append)
    response = staffing_routes._cancel_scheduler_time_off(91, {"day": "2026-07-17"})
    assert response.status_code == 200
    assert staged == [91]
    assert queued == [91]
\`\`\`

- [ ] **Step 2: Verify red**

Run: \`pytest tests/test_staffing_time_off_editor.py -v\`

Expected: FAIL because helpers and endpoints do not exist.

- [ ] **Step 3: Implement the narrow backend contract**

Import \`BackgroundTasks\`, \`time_off_sync\`, \`scheduler_time_off\`, and \`shape_to_hour_bounds\`. Add:

\`\`\`python
def _queue_time_off_push(request_id: int) -> None:
    time_off_sync.push_one(request_id)

def _editable_time_off_for_day(request_id: int, day: date) -> dict | None:
    rows = db.query(
        "SELECT id, shape, holiday_status_id, date_from, date_to, hour_from, hour_to, "
        "odoo_leave_id FROM time_off_requests WHERE id = %s "
        "AND odoo_leave_id IS NOT NULL AND NOT local_record "
        "AND state = ANY(%s) AND date_from <= %s AND date_to >= %s",
        (request_id, list(scheduler_time_off._VISIBLE_STATES), day, day),
    )
    return rows[0] if rows else None
\`\`\`

The edit helper writes the existing shape, changed dates/times, \`state='draft_edit'\`, \`synced_to_odoo=FALSE\`, and \`sync_error=NULL\`; it preserves leave type, note, and Odoo id. Parse JSON ISO dates, reject reversed dates or invalid partial times with 422, validate partials with \`shape_to_hour_bounds\` against the configured shift, enqueue \`_queue_time_off_push\` in \`BackgroundTasks\`, and invalidate the today cache.

The cancel helper changes a valid row to \`draft_cancel\`, marks it unsynced, clears \`sync_error\`, queues the same push wrapper, and invalidates cache. Return 404 for local/non-Odoo/out-of-day/non-visible records.

- [ ] **Step 4: Add failure-boundary tests and verify green**

\`\`\`python
def test_supervisor_edit_rejects_invalid_partial_window(client):
    response = client.post("/api/staffing/time-off/91/edit", json={
        "day": "2026-07-17", "date_from": "2026-07-17", "date_to": "2026-07-17",
        "time_from": "12:00", "time_to": "09:00",
    })
    assert response.status_code == 422

def test_supervisor_endpoints_reject_local_or_out_of_day_record(client):
    response = client.post("/api/staffing/time-off/91/cancel", json={"day": "2026-07-17"})
    assert response.status_code == 404
\`\`\`

Run: \`pytest tests/test_staffing_time_off_editor.py tests/test_time_off_routes.py -v\`

Expected: PASS.

- [ ] **Step 5: Commit**

\`\`\`bash
git add src/zira_dashboard/routes/staffing.py tests/test_staffing_time_off_editor.py
git commit -m "feat: edit scheduler time off"
\`\`\`

### Task 3: Reconcile today’s hard-deleted Odoo leaves every minute

**Files:**
- Modify: \`src/zira_dashboard/time_off_sync.py:335-397\`
- Test: \`tests/test_time_off_sync.py\`

**Interfaces:**
- \`poll_odoo_leaves()\` continues its rolling incremental/full process and additionally fully fetches today, then calls \`_delete_missing_from_odoo(today_seen_ids, today, today)\`.

- [ ] **Step 1: Write the failing regression**

\`\`\`python
def test_incremental_poll_reconciles_hard_deleted_current_day_leave(monkeypatch):
    _reset_poller_state()
    calls, deletions = [], []
    monkeypatch.setattr(time_off_sync.odoo_client, "fetch_leave_types", lambda: [])
    monkeypatch.setattr(time_off_sync.odoo_client, "fetch_leaves_for_range",
                        lambda start, end, modified_since=None:
                        calls.append((start, end, modified_since)) or [])
    monkeypatch.setattr(time_off_sync, "_existing_rows_by_leave_id", lambda _ids: {})
    monkeypatch.setattr(time_off_sync, "_delete_missing_from_odoo",
                        lambda ids, start, end: deletions.append((ids, start, end)))
    time_off_sync.poll_odoo_leaves()
    today = date.today()
    assert (today, today, None) in calls
    assert (set(), today, today) in deletions
\`\`\`

- [ ] **Step 2: Verify red**

Run: \`pytest tests/test_time_off_sync.py::test_incremental_poll_reconciles_hard_deleted_current_day_leave -v\`

Expected: FAIL because no dedicated current-day full reconciliation exists.

- [ ] **Step 3: Add bounded reconciliation**

After the rolling upsert loop in \`poll_odoo_leaves()\`, add:

\`\`\`python
today_leaves = odoo_client.fetch_leaves_for_range(today, today)
today_existing = _existing_rows_by_leave_id([leave["id"] for leave in today_leaves])
today_seen_ids: set[int] = set()
for leave in today_leaves:
    today_seen_ids.add(leave["id"])
    _upsert_one(leave, today_existing.get(leave["id"]))
_delete_missing_from_odoo(today_seen_ids, today, today)
\`\`\`

Retain the rolling full-pass deletion detection for dates outside today.

- [ ] **Step 4: Verify green**

Run: \`pytest tests/test_time_off_sync.py -v\`

Expected: PASS.

- [ ] **Step 5: Commit**

\`\`\`bash
git add src/zira_dashboard/time_off_sync.py tests/test_time_off_sync.py
git commit -m "fix: refresh deleted time off for today"
\`\`\`

### Task 4: Add the scheduler editor dialog

**Files:**
- Modify: \`src/zira_dashboard/templates/staffing.html:78-112, 432-466\`
- Modify: \`src/zira_dashboard/static/staffing.js:410-620\`
- Modify: \`src/zira_dashboard/static/staffing.css:63-140, 730-760\`
- Test: \`tests/test_staffing_static.py\`

**Interfaces:**
- Editable rows consume the metadata from Task 1.
- One native \`<dialog id="scheduler-time-off-editor">\` posts Task 2 JSON actions.
- Success reloads the currently displayed scheduler day using \`window.SCHEDULE_DAY\`.

- [ ] **Step 1: Write failing static tests**

\`\`\`python
def test_scheduler_time_off_rows_expose_editor_data_and_dialog():
    html = _template()
    assert 'data-request-id="{{ e.request_id }}"' in html
    assert 'id="scheduler-time-off-editor"' in html
    assert 'id="scheduler-time-off-save"' in html
    assert 'id="scheduler-time-off-cancel"' in html

def test_scheduler_script_posts_editor_actions_and_restores_focus():
    js = _script()
    assert "/api/staffing/time-off/" in js
    assert "showModal()" in js
    assert "scheduler-time-off-editor" in js
    assert "window.location.href = '/staffing?day='" in js
\`\`\`

- [ ] **Step 2: Verify red**

Run: \`pytest tests/test_staffing_static.py::test_scheduler_time_off_rows_expose_editor_data_and_dialog tests/test_staffing_static.py::test_scheduler_script_posts_editor_actions_and_restores_focus -v\`

Expected: FAIL because no editor metadata or dialog exists.

- [ ] **Step 3: Render accessible dialog markup**

Add \`data-request-id\`, \`data-date-from\`, \`data-date-to\`, \`data-hour-from\`, and \`data-hour-to\` only to \`e.editable\` Time Off rows. Render one dialog with date inputs, a hidden partial-time group, an error element with \`role="alert"\`, Close, Cancel time off, and Save changes buttons. Keep names inside the scheduler instead of turning them into player-card links; do not render a dialog for non-editable/local records.

- [ ] **Step 4: Implement interaction and styles**

\`\`\`javascript
async function submitSchedulerTimeOff(action, payload) {
  const response = await fetch(
    '/api/staffing/time-off/' + encodeURIComponent(activeRequestId) + '/' + action,
    {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
     body: JSON.stringify(payload)}
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Could not update time off.');
  window.location.href = '/staffing?day=' + encodeURIComponent(window.SCHEDULE_DAY);
}
\`\`\`

Bind mouse and Enter/Space activation for editable rows; track and restore the opener; focus the first date input on \`showModal()\`; support Escape; hide partial inputs for a full-day request; disable only the clicked action while awaiting its response; render errors in the dialog. Add visible keyboard focus, pointer hover, mobile-sized spacing, and a red destructive cancel action.

- [ ] **Step 5: Verify green and commit**

Run: \`pytest tests/test_staffing_static.py -v\`

Expected: PASS.

\`\`\`bash
git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css tests/test_staffing_static.py
git commit -m "feat: edit time off from scheduler"
\`\`\`

### Task 5: Complete verification

**Files:**
- No production changes.

- [ ] **Step 1: Run affected suites**

Run: \`pytest tests/test_scheduler_time_off.py tests/test_staffing_time_off_editor.py tests/test_time_off_sync.py tests/test_time_off_routes.py tests/test_staffing_static.py tests/test_staffing_view.py -v\`

Expected: PASS.

- [ ] **Step 2: Run the full suite**

Run: \`pytest -q\`

Expected: PASS with no new failures.

- [ ] **Step 3: Inspect final repository state**

Run: \`git diff --check && git status --short\`

Expected: no whitespace errors and no staged user-owned files.

- [ ] **Step 4: Commit any verification correction with its exact affected files**

Run: \`git status --short\`

Expected: if verification required a correction, stage only the named files shown by this command and create a separate commit whose subject describes that correction; otherwise make no additional commit.
