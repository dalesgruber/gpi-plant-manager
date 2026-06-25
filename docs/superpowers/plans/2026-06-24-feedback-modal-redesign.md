# Feedback Modal Redesign → Odoo Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inline "Send feedback" form with a green modal (Bug/Feature toggle, description, file picker + paste-screenshot) that files each submission as an Odoo `project.task` assigned to the owner and due today, plus a "View Feedback" panel showing each submitter's items with Open/Done/Rejected status.

**Architecture:** A multipart `POST /feedback` route resolves a find-or-created "Plant Manager" Odoo project, creates a task (assignee = authenticated Odoo uid, due today, tagged Bug/Feature, attachments pushed as `ir.attachment`), and writes a local index row linking submitter → task id. `GET /api/feedback/mine` reads each task's live Odoo stage and collapses it to Open/Done/Rejected. The legacy `/admin/feedback` page + nav tab are removed.

**Tech Stack:** FastAPI (multipart via `python-multipart`), psycopg2/Postgres (`feedback` table), Odoo XML-RPC via `odoo_client.execute`, vanilla JS/CSS in `static/footer.*`.

---

## Spec

See `docs/superpowers/specs/2026-06-24-feedback-modal-redesign-design.md`.

## Testing notes (read before starting)

- Run tests with the venv python by path and `ZIRA_API_KEY`:
  `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/<file> -v`
- `tests/conftest.py` sets `AUTH_DISABLED=1`, so `TestClient` requests have no
  user; `request.state.user_upn` is `None` in route tests (assert accordingly or
  monkeypatch the store).
- Odoo unit tests use the `_stub_execute(monkeypatch, responses)` helper in
  `tests/test_odoo_client.py`: it replaces `odoo_client.execute` with a function
  that pops queued responses and records calls. New Odoo tests follow the same
  shape (define a local stub if cross-file).
- Route tests monkeypatch the high-level `odoo_client` helpers
  (`ensure_feedback_project`, etc.) and `feedback_store` functions — no live Odoo
  or Postgres.

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/zira_dashboard/_schema.py` | `feedback` table DDL | Modify: add `task_type`, `odoo_task_id` (CREATE + ALTER) |
| `src/zira_dashboard/feedback_store.py` | feedback persistence | Modify: extend `insert`, add `for_submitter`, drop `recent` |
| `src/zira_dashboard/odoo_client.py` | Odoo XML-RPC | Modify: add feedback project/tag/task/attachment/stage helpers + status bucket |
| `src/zira_dashboard/routes/feedback.py` | feedback HTTP routes | Modify: multipart POST → Odoo; add `/api/feedback/mine`; remove `/admin/feedback` |
| `src/zira_dashboard/templates/admin_feedback.html` | admin table | Delete |
| `src/zira_dashboard/templates/settings.html` | settings nav | Modify: remove `Feedback` nav tab |
| `src/zira_dashboard/templates/_footer.html` | global panel markup | Modify: new Send-feedback + View-feedback modals |
| `src/zira_dashboard/static/footer.css` | panel styles | Modify: green modal/toggle/chip/status styles |
| `src/zira_dashboard/static/footer.js` | panel behavior | Modify: new modal logic, multipart submit, paste capture, View list |
| `tests/test_feedback_schema.py` | schema columns | Modify |
| `tests/test_feedback_store_unit.py` | store unit | Modify |
| `tests/test_feedback_store.py` | store round-trip (PG) | Modify |
| `tests/test_feedback_routes.py` | POST/admin routes | Modify (rewrite for multipart, drop admin test) |
| `tests/test_feedback_odoo.py` | Odoo helper units | Create |
| `tests/test_feedback_mine_route.py` | `/api/feedback/mine` | Create |
| `tests/test_whatsnew_panel_static.py` | footer static asserts | Modify |

---

## Task 1: Schema — add `task_type` and `odoo_task_id`

**Files:**
- Modify: `src/zira_dashboard/_schema.py` (the `CREATE TABLE IF NOT EXISTS feedback` block, ~line 865)
- Test: `tests/test_feedback_schema.py`

- [ ] **Step 1: Update the schema test to require the new columns**

Replace the body of `tests/test_feedback_schema.py` with:

```python
from zira_dashboard._schema import SCHEMA_DDL


def test_schema_defines_feedback_table():
    assert "CREATE TABLE IF NOT EXISTS feedback" in SCHEMA_DDL
    for col in (
        "id", "created_at", "submitter", "page_url", "category", "message",
        "task_type", "odoo_task_id",
    ):
        assert col in SCHEMA_DDL, f"missing column {col}"


def test_schema_has_idempotent_alters_for_new_feedback_columns():
    assert "ADD COLUMN IF NOT EXISTS task_type" in SCHEMA_DDL
    assert "ADD COLUMN IF NOT EXISTS odoo_task_id" in SCHEMA_DDL
```

- [ ] **Step 2: Run it to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_schema.py -v`
Expected: FAIL — `missing column task_type` / `ADD COLUMN IF NOT EXISTS task_type` not found.

- [ ] **Step 3: Update the DDL**

In `src/zira_dashboard/_schema.py`, change the feedback `CREATE TABLE` to:

```sql
CREATE TABLE IF NOT EXISTS feedback (
  id           SERIAL PRIMARY KEY,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  submitter    TEXT,
  page_url     TEXT,
  category     TEXT,
  message      TEXT NOT NULL,
  task_type    TEXT,
  odoo_task_id BIGINT
);
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS task_type TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS odoo_task_id BIGINT;
```

(The `ALTER` lines migrate existing prod installs; the extended `CREATE` covers fresh installs. Both name the columns so the grep-based test passes.)

- [ ] **Step 4: Run it to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_schema.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/_schema.py tests/test_feedback_schema.py
git commit -m "feat(feedback): add task_type + odoo_task_id columns"
```

---

## Task 2: `feedback_store` — extend insert, add `for_submitter`, drop `recent`

**Files:**
- Modify: `src/zira_dashboard/feedback_store.py`
- Test: `tests/test_feedback_store_unit.py`

- [ ] **Step 1: Rewrite the unit tests**

Replace `tests/test_feedback_store_unit.py` with:

```python
"""DB-free unit tests for feedback_store helpers."""

from zira_dashboard import feedback_store


def test_insert_passes_all_columns(monkeypatch):
    seen = {}

    def fake_cursor():
        class _Cur:
            def __enter__(self_):
                return self_
            def __exit__(self_, *a):
                return False
            def execute(self_, sql, params):
                seen["sql"] = sql
                seen["params"] = params
            def fetchone(self_):
                return {"id": 42}
        return _Cur()

    monkeypatch.setattr(feedback_store.db, "cursor", fake_cursor)

    new_id = feedback_store.insert(
        message="hi",
        submitter="dale@x.com",
        page_url="/p",
        task_type="bug",
        odoo_task_id=7,
    )

    assert new_id == 42
    assert seen["params"] == ("dale@x.com", "/p", "bug", 7, "hi")


def test_for_submitter_clamps_limit_and_filters(monkeypatch):
    seen = []

    def fake_query(sql, params):
        seen.append((sql, params))
        return []

    monkeypatch.setattr(feedback_store.db, "query", fake_query)

    feedback_store.for_submitter("dale@x.com", limit=0)
    feedback_store.for_submitter("dale@x.com", limit=9999)

    assert "WHERE submitter = %s" in seen[0][0]
    assert seen[0][1] == ("dale@x.com", 1)
    assert seen[1][1] == ("dale@x.com", 500)


def test_for_submitter_uses_default_limit_for_invalid_values(monkeypatch):
    seen = []
    monkeypatch.setattr(
        feedback_store.db, "query",
        lambda sql, params: seen.append((sql, params)) or [],
    )
    feedback_store.for_submitter("dale@x.com", limit="nope")
    assert seen[0][1] == ("dale@x.com", 100)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_store_unit.py -v`
Expected: FAIL — `insert()` signature lacks `task_type`/`odoo_task_id`; `for_submitter` undefined.

- [ ] **Step 3: Rewrite `feedback_store.py`**

```python
"""Persistence for user-submitted feedback (index linking submitter → Odoo task)."""

from __future__ import annotations

from . import db


def _clamp_limit(limit, default: int = 100) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, 500))


def insert(
    message: str,
    submitter: str | None = None,
    page_url: str | None = None,
    task_type: str | None = None,
    odoo_task_id: int | None = None,
) -> int:
    """Insert one feedback row; return its new id."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO feedback (submitter, page_url, task_type, odoo_task_id, message) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (submitter, page_url, task_type, odoo_task_id, message),
        )
        return cur.fetchone()["id"]


def for_submitter(submitter: str | None, limit: int = 100) -> list[dict]:
    """Return one submitter's feedback rows, newest first."""
    return db.query(
        "SELECT id, created_at, submitter, page_url, task_type, odoo_task_id, message "
        "FROM feedback WHERE submitter = %s ORDER BY id DESC LIMIT %s",
        (submitter, _clamp_limit(limit)),
    )
```

- [ ] **Step 4: Run it to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_store_unit.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Update the Postgres round-trip test**

Replace `tests/test_feedback_store.py` with:

```python
"""Round-trip test for feedback_store (needs Postgres)."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres"
)

from zira_dashboard import db, feedback_store


@pytest.fixture(autouse=True)
def _schema():
    db.init_pool()
    db.bootstrap_schema()
    yield


def test_insert_then_for_submitter_round_trip():
    new_id = feedback_store.insert(
        message="Round-trip test message",
        submitter="tester@gruberpallets.com",
        page_url="/recycling",
        task_type="bug",
        odoo_task_id=999001,
    )
    assert isinstance(new_id, int)
    rows = feedback_store.for_submitter("tester@gruberpallets.com", limit=50)
    match = next((r for r in rows if r["id"] == new_id), None)
    assert match is not None
    assert match["message"] == "Round-trip test message"
    assert match["task_type"] == "bug"
    assert match["odoo_task_id"] == 999001
    db.execute("DELETE FROM feedback WHERE id = %s", (new_id,))
```

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/feedback_store.py tests/test_feedback_store_unit.py tests/test_feedback_store.py
git commit -m "feat(feedback): store task_type/odoo_task_id; add for_submitter"
```

---

## Task 3: Odoo — find-or-create project (+ stages) and tags

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py`
- Test: `tests/test_feedback_odoo.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_feedback_odoo.py`:

```python
"""Unit tests for the Odoo feedback-task helpers (execute is stubbed)."""

from zira_dashboard import odoo_client


def _stub(monkeypatch):
    calls = []
    responses = []

    def fake(model, method, *args, **kwargs):
        calls.append((model, method, args, kwargs))
        return responses.pop(0) if responses else None

    monkeypatch.setattr(odoo_client, "execute", fake)
    odoo_client._reset_cache_for_tests()
    return calls, responses


def test_ensure_feedback_project_reuses_existing(monkeypatch):
    calls, responses = _stub(monkeypatch)
    responses.extend([
        [{"id": 7}],                       # project search_read → found
        [{"name": "New"}, {"name": "In Progress"},
         {"name": "Done"}, {"name": "Rejected"}],  # stages search_read → all present
    ])

    pid = odoo_client.ensure_feedback_project()

    assert pid == 7
    assert calls[0][0:2] == ("project.project", "search_read")
    assert all(c[1] != "create" or c[0] != "project.project" for c in calls)


def test_ensure_feedback_project_creates_when_absent(monkeypatch):
    calls, responses = _stub(monkeypatch)
    responses.extend([
        [],        # project search_read → none
        11,        # project create → id
        [],        # stages search_read → none present
        101, 102, 103, 104,  # create the 4 stages
    ])

    pid = odoo_client.ensure_feedback_project()

    assert pid == 11
    creates = [c for c in calls if c[0] == "project.task.type" and c[1] == "create"]
    assert len(creates) == 4
    names = [c[2][0]["name"] for c in creates]
    assert names == ["New", "In Progress", "Done", "Rejected"]
    rejected = next(c[2][0] for c in creates if c[2][0]["name"] == "Rejected")
    assert rejected["fold"] is True


def test_ensure_feedback_tag_finds_then_creates(monkeypatch):
    calls, responses = _stub(monkeypatch)
    responses.extend([[], 55])  # search_read → none, create → 55

    tag_id = odoo_client.ensure_feedback_tag("Bug")

    assert tag_id == 55
    assert calls[0][0:2] == ("project.tags", "search_read")
    assert calls[1][0:2] == ("project.tags", "create")
    assert calls[1][2][0]["name"] == "Bug"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_odoo.py -v`
Expected: FAIL — helpers undefined.

- [ ] **Step 3: Implement in `odoo_client.py`**

Add near the other module constants (after `SKILL_TYPE_NAMES`, ~line 146):

```python
FEEDBACK_PROJECT_NAME = "Plant Manager"
FEEDBACK_STAGES = ("New", "In Progress", "Done", "Rejected")
FEEDBACK_DONE_STAGE = "Done"
FEEDBACK_REJECTED_STAGE = "Rejected"

_feedback_project_id: int | None = None
```

Extend `_reset_cache_for_tests()` to also clear the project cache:

```python
def _reset_cache_for_tests() -> None:
    """Clear cached uid + per-thread object proxy; tests call this between cases."""
    global _uid_cache, _feedback_project_id
    _uid_cache = None
    _feedback_project_id = None
    if hasattr(_thread_local, "object_proxy"):
        del _thread_local.object_proxy
```

Add the helpers (anywhere after `execute`, e.g. end of file):

```python
def _ensure_feedback_stages(project_id: int) -> None:
    existing = execute(
        "project.task.type", "search_read",
        [("project_ids", "in", [project_id])], fields=["name"],
    ) or []
    have = {r["name"] for r in existing}
    for seq, name in enumerate(FEEDBACK_STAGES):
        if name in have:
            continue
        execute("project.task.type", "create", {
            "name": name,
            "sequence": seq,
            "fold": name in (FEEDBACK_DONE_STAGE, FEEDBACK_REJECTED_STAGE),
            "project_ids": [(4, project_id)],
        })


def ensure_feedback_project() -> int:
    """Find-or-create the 'Plant Manager' project (+ its stages); cache the id."""
    global _feedback_project_id
    if _feedback_project_id is not None:
        return _feedback_project_id
    found = execute(
        "project.project", "search_read",
        [("name", "=", FEEDBACK_PROJECT_NAME)], fields=["id"], limit=1,
    )
    if found:
        project_id = found[0]["id"]
    else:
        project_id = execute("project.project", "create", {"name": FEEDBACK_PROJECT_NAME})
    _ensure_feedback_stages(project_id)
    _feedback_project_id = project_id
    return project_id


def ensure_feedback_tag(name: str) -> int:
    """Find-or-create a project.tags row by name; return its id."""
    found = execute(
        "project.tags", "search_read",
        [("name", "=", name)], fields=["id"], limit=1,
    )
    if found:
        return found[0]["id"]
    return execute("project.tags", "create", {"name": name})
```

- [ ] **Step 4: Run it to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_odoo.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/odoo_client.py tests/test_feedback_odoo.py
git commit -m "feat(odoo): find-or-create Plant Manager project, stages, tags"
```

---

## Task 4: Odoo — create task, push attachment

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py`
- Test: `tests/test_feedback_odoo.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_feedback_odoo.py`:

```python
import xmlrpc.client


def test_create_feedback_task_uses_user_ids_and_tag_and_deadline(monkeypatch):
    calls, responses = _stub(monkeypatch)
    responses.append(900)  # create → task id

    task_id = odoo_client.create_feedback_task(
        project_id=7, name="[Bug] x", description_html="<p>x</p>",
        assignee_uid=3, tag_id=55, deadline="2026-06-24",
    )

    assert task_id == 900
    model, method, args, kwargs = calls[0]
    assert (model, method) == ("project.task", "create")
    vals = args[0]
    assert vals["name"] == "[Bug] x"
    assert vals["project_id"] == 7
    assert vals["date_deadline"] == "2026-06-24"
    assert vals["user_ids"] == [(6, 0, [3])]
    assert vals["tag_ids"] == [(6, 0, [55])]


def test_create_feedback_task_falls_back_to_user_id(monkeypatch):
    calls = []
    state = {"first": True}

    def fake(model, method, *args, **kwargs):
        calls.append((model, method, args, kwargs))
        if state["first"]:
            state["first"] = False
            raise xmlrpc.client.Fault(2, "Invalid field 'user_ids'")
        return 901

    monkeypatch.setattr(odoo_client, "execute", fake)
    odoo_client._reset_cache_for_tests()

    task_id = odoo_client.create_feedback_task(
        project_id=7, name="x", description_html="x",
        assignee_uid=3, tag_id=None, deadline="2026-06-24",
    )

    assert task_id == 901
    assert "user_ids" in calls[0][2][0]
    assert calls[1][2][0]["user_id"] == 3
    assert "tag_ids" not in calls[1][2][0]


def test_add_task_attachment_creates_ir_attachment(monkeypatch):
    calls, responses = _stub(monkeypatch)
    responses.append(500)

    att_id = odoo_client.add_task_attachment(
        task_id=900, filename="shot.png", mimetype="image/png", raw_bytes=b"abc",
    )

    assert att_id == 500
    model, method, args, kwargs = calls[0]
    assert (model, method) == ("ir.attachment", "create")
    vals = args[0]
    assert vals["name"] == "shot.png"
    assert vals["res_model"] == "project.task"
    assert vals["res_id"] == 900
    assert vals["mimetype"] == "image/png"
    import base64
    assert base64.b64decode(vals["datas"]) == b"abc"
```

- [ ] **Step 2: Run to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_odoo.py -v`
Expected: FAIL — `create_feedback_task` / `add_task_attachment` undefined.

- [ ] **Step 3: Implement**

Add to `odoo_client.py` (the file already imports `xmlrpc.client`; add `import base64` near the top imports):

```python
def create_feedback_task(
    project_id: int,
    name: str,
    description_html: str,
    assignee_uid: int,
    tag_id: int | None,
    deadline: str,
) -> int:
    """Create a project.task. Tries Odoo 16/17 `user_ids` (m2m), falls back to
    legacy `user_id` (m2o) if the field is rejected."""
    base = {
        "name": name,
        "project_id": project_id,
        "description": description_html,
        "date_deadline": deadline,
    }
    if tag_id:
        base["tag_ids"] = [(6, 0, [tag_id])]
    try:
        return execute("project.task", "create",
                       dict(base, user_ids=[(6, 0, [assignee_uid])]))
    except xmlrpc.client.Fault:
        return execute("project.task", "create",
                       dict(base, user_id=assignee_uid))


def add_task_attachment(
    task_id: int, filename: str, mimetype: str | None, raw_bytes: bytes
) -> int:
    """Attach a file to a project.task as an ir.attachment."""
    return execute("ir.attachment", "create", {
        "name": filename,
        "datas": base64.b64encode(raw_bytes).decode("ascii"),
        "res_model": "project.task",
        "res_id": task_id,
        "mimetype": mimetype or "application/octet-stream",
    })
```

- [ ] **Step 4: Run to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_odoo.py -v`
Expected: PASS (6 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/odoo_client.py tests/test_feedback_odoo.py
git commit -m "feat(odoo): create feedback task + push attachment"
```

---

## Task 5: Odoo — stage lookup + status bucketing

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py`
- Test: `tests/test_feedback_odoo.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_feedback_odoo.py`:

```python
def test_fetch_task_stage_names_maps_id_to_name(monkeypatch):
    calls, responses = _stub(monkeypatch)
    responses.append([
        {"id": 900, "stage_id": [3, "In Progress"]},
        {"id": 901, "stage_id": [4, "Done"]},
        {"id": 902, "stage_id": False},
    ])

    out = odoo_client.fetch_task_stage_names([900, 901, 902])

    assert out == {900: "In Progress", 901: "Done", 902: None}
    assert calls[0][0:2] == ("project.task", "read")


def test_fetch_task_stage_names_empty_input_skips_call(monkeypatch):
    calls, _ = _stub(monkeypatch)
    assert odoo_client.fetch_task_stage_names([]) == {}
    assert calls == []


def test_feedback_status_bucket():
    assert odoo_client.feedback_status_bucket("Done") == "done"
    assert odoo_client.feedback_status_bucket("Rejected") == "rejected"
    assert odoo_client.feedback_status_bucket("New") == "open"
    assert odoo_client.feedback_status_bucket("In Progress") == "open"
    assert odoo_client.feedback_status_bucket(None) == "open"
```

- [ ] **Step 2: Run to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_odoo.py -v`
Expected: FAIL — `fetch_task_stage_names` / `feedback_status_bucket` undefined.

- [ ] **Step 3: Implement**

Add to `odoo_client.py`:

```python
def fetch_task_stage_names(task_ids) -> dict[int, str | None]:
    """Return {task_id: stage name} for the given project.task ids."""
    ids = [int(t) for t in task_ids if t]
    if not ids:
        return {}
    rows = execute("project.task", "read", ids, fields=["id", "stage_id"]) or []
    out: dict[int, str | None] = {}
    for r in rows:
        stage = r.get("stage_id")
        out[r["id"]] = stage[1] if isinstance(stage, (list, tuple)) and len(stage) > 1 else None
    return out


def feedback_status_bucket(stage_name: str | None) -> str:
    """Collapse an Odoo stage name to open / done / rejected."""
    if stage_name == FEEDBACK_DONE_STAGE:
        return "done"
    if stage_name == FEEDBACK_REJECTED_STAGE:
        return "rejected"
    return "open"
```

- [ ] **Step 4: Run to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_odoo.py -v`
Expected: PASS (9 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/odoo_client.py tests/test_feedback_odoo.py
git commit -m "feat(odoo): task stage lookup + status bucketing"
```

---

## Task 6: Route — multipart `POST /feedback` → Odoo task + local row

**Files:**
- Modify: `src/zira_dashboard/routes/feedback.py`
- Test: `tests/test_feedback_routes.py` (rewrite)

- [ ] **Step 1: Rewrite the route tests**

Replace `tests/test_feedback_routes.py` with:

```python
"""Feedback POST route tests; Odoo + store are monkeypatched (no PG/Odoo)."""

from fastapi.testclient import TestClient

from zira_dashboard import feedback_store, odoo_client
from zira_dashboard.app import app

client = TestClient(app)


def _patch_odoo(monkeypatch, created=None):
    calls = {"task": None, "attachments": [], "tags": []}

    monkeypatch.setattr(odoo_client, "ensure_feedback_project", lambda: 7)
    monkeypatch.setattr(odoo_client, "authenticate", lambda: 3)

    def fake_tag(name):
        calls["tags"].append(name)
        return 55

    def fake_task(**kwargs):
        calls["task"] = kwargs
        return created or 900

    def fake_att(**kwargs):
        calls["attachments"].append(kwargs)
        return len(calls["attachments"])

    monkeypatch.setattr(odoo_client, "ensure_feedback_tag", fake_tag)
    monkeypatch.setattr(
        odoo_client, "create_feedback_task",
        lambda **kw: fake_task(**kw),
    )
    monkeypatch.setattr(
        odoo_client, "add_task_attachment",
        lambda **kw: fake_att(**kw),
    )
    return calls


def test_post_feedback_creates_task_and_local_row(monkeypatch):
    calls = _patch_odoo(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        feedback_store, "insert",
        lambda **kw: captured.update(kw) or 12,
    )

    resp = client.post(
        "/feedback",
        data={"type": "bug", "description": "  It broke  ", "page_url": "/recycling"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["task_id"] == 900
    assert calls["task"]["project_id"] == 7
    assert calls["task"]["assignee_uid"] == 3
    assert calls["task"]["tag_id"] == 55
    assert calls["task"]["name"].startswith("[Bug] It broke")
    assert calls["tags"] == ["Bug"]
    assert captured["task_type"] == "bug"
    assert captured["odoo_task_id"] == 900
    assert captured["message"] == "It broke"
    assert captured["page_url"] == "/recycling"


def test_post_feedback_feature_uses_feature_tag(monkeypatch):
    calls = _patch_odoo(monkeypatch)
    monkeypatch.setattr(feedback_store, "insert", lambda **kw: 1)

    resp = client.post(
        "/feedback",
        data={"type": "feature", "description": "Add dark mode"},
    )

    assert resp.status_code == 200
    assert calls["tags"] == ["Feature request"]
    assert calls["task"]["name"].startswith("[Feature] Add dark mode")


def test_post_feedback_uploads_attachments(monkeypatch):
    calls = _patch_odoo(monkeypatch)
    monkeypatch.setattr(feedback_store, "insert", lambda **kw: 1)

    resp = client.post(
        "/feedback",
        data={"type": "bug", "description": "see image"},
        files=[("files", ("shot.png", b"\x89PNG\r\n", "image/png"))],
    )

    assert resp.status_code == 200
    assert len(calls["attachments"]) == 1
    assert calls["attachments"][0]["task_id"] == 900
    assert calls["attachments"][0]["filename"] == "shot.png"
    assert calls["attachments"][0]["raw_bytes"] == b"\x89PNG\r\n"


def test_post_feedback_rejects_empty_description(monkeypatch):
    _patch_odoo(monkeypatch)
    called = {"n": 0}
    monkeypatch.setattr(
        feedback_store, "insert",
        lambda **kw: called.__setitem__("n", called["n"] + 1) or 1,
    )

    resp = client.post("/feedback", data={"type": "bug", "description": "   "})

    assert resp.status_code == 400
    assert resp.json()["ok"] is False
    assert called["n"] == 0


def test_post_feedback_drops_unsafe_page_url(monkeypatch):
    calls = _patch_odoo(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        feedback_store, "insert",
        lambda **kw: captured.update(kw) or 1,
    )

    resp = client.post(
        "/feedback",
        data={"type": "bug", "description": "x", "page_url": "javascript:alert(1)"},
    )

    assert resp.status_code == 200
    assert captured["page_url"] is None


def test_post_feedback_returns_502_and_skips_local_row_on_odoo_failure(monkeypatch):
    monkeypatch.setattr(odoo_client, "authenticate", lambda: 3)
    monkeypatch.setattr(odoo_client, "ensure_feedback_tag", lambda name: 55)

    def boom():
        raise RuntimeError("odoo down")

    monkeypatch.setattr(odoo_client, "ensure_feedback_project", boom)
    inserted = {"n": 0}
    monkeypatch.setattr(
        feedback_store, "insert",
        lambda **kw: inserted.__setitem__("n", inserted["n"] + 1) or 1,
    )

    resp = client.post("/feedback", data={"type": "bug", "description": "x"})

    assert resp.status_code == 502
    assert resp.json()["ok"] is False
    assert inserted["n"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_routes.py -v`
Expected: FAIL — route still expects JSON / lacks Odoo wiring.

- [ ] **Step 3: Rewrite the POST handler in `routes/feedback.py`**

Replace the file's imports and `submit_feedback` (keep `_optional_text` / `_safe_page_url`; drop the `FeedbackIn` pydantic model and the JSON handler). New top + handler:

```python
"""User feedback submission → Odoo task, and a per-user status list."""

from __future__ import annotations

import logging
from datetime import date
from urllib.parse import urlparse

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from .. import feedback_store, odoo_client

router = APIRouter()
log = logging.getLogger(__name__)

_TYPE_TAG = {"bug": "Bug", "feature": "Feature request"}
_TYPE_TITLE = {"bug": "Bug", "feature": "Feature"}
_TITLE_MAX = 70
_MAX_FILE_BYTES = 10 * 1024 * 1024
_ALLOWED_PREFIXES = ("image/",)
_ALLOWED_TYPES = ("application/pdf",)


def _optional_text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _safe_page_url(value: str | None) -> str | None:
    value = _optional_text(value)
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https"):
        return value
    if not parsed.scheme and value.startswith("/") and not value.startswith("//"):
        return value
    return None


def _title_from(kind: str, description: str) -> str:
    first = description.strip().splitlines()[0] if description.strip() else "feedback"
    if len(first) > _TITLE_MAX:
        first = first[: _TITLE_MAX - 1].rstrip() + "…"
    return f"[{_TYPE_TITLE.get(kind, 'Bug')}] {first}"


def _allowed_upload(upload: UploadFile) -> bool:
    ct = (upload.content_type or "").lower()
    return ct.startswith(_ALLOWED_PREFIXES) or ct in _ALLOWED_TYPES


def _description_html(description: str, submitter: str | None,
                      name: str | None, page_url: str | None) -> str:
    who = name or submitter or "unknown"
    if name and submitter:
        who = f"{name} ({submitter})"
    parts = [f"<p>{description.strip()}</p>".replace("\n", "<br>")]
    meta = [f"Submitted by {who}"]
    if page_url:
        meta.append(f'Page: <a href="{page_url}">{page_url}</a>')
    parts.append("<p><small>" + " · ".join(meta) + "</small></p>")
    return "".join(parts)


@router.post("/feedback")
async def submit_feedback(
    request: Request,
    type: str = Form("bug"),
    description: str = Form(...),
    page_url: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
) -> JSONResponse:
    kind = "feature" if type == "feature" else "bug"
    text = (description or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "Description is required."},
                            status_code=400)

    submitter = getattr(request.state, "user_upn", None)
    name = getattr(request.state, "user_name", None)
    safe_url = _safe_page_url(page_url)

    # Read allowed uploads up front (so an Odoo outage doesn't half-consume them).
    blobs: list[tuple[str, str | None, bytes]] = []
    for upload in files or []:
        if not upload.filename or not _allowed_upload(upload):
            continue
        raw = await upload.read()
        if not raw or len(raw) > _MAX_FILE_BYTES:
            continue
        blobs.append((upload.filename, upload.content_type, raw))

    try:
        project_id = odoo_client.ensure_feedback_project()
        tag_id = odoo_client.ensure_feedback_tag(_TYPE_TAG[kind])
        task_id = odoo_client.create_feedback_task(
            project_id=project_id,
            name=_title_from(kind, text),
            description_html=_description_html(text, submitter, name, safe_url),
            assignee_uid=odoo_client.authenticate(),
            tag_id=tag_id,
            deadline=date.today().isoformat(),
        )
    except Exception:
        log.exception("feedback: failed to create Odoo task")
        return JSONResponse(
            {"ok": False, "error": "Couldn't reach Odoo — please try again."},
            status_code=502,
        )

    for filename, mimetype, raw in blobs:
        try:
            odoo_client.add_task_attachment(
                task_id=task_id, filename=filename, mimetype=mimetype, raw_bytes=raw,
            )
        except Exception:
            log.exception("feedback: attachment upload failed for task %s", task_id)

    new_id = feedback_store.insert(
        message=text,
        submitter=submitter,
        page_url=safe_url,
        task_type=kind,
        odoo_task_id=task_id,
    )
    return JSONResponse({"ok": True, "id": new_id, "task_id": task_id})
```

- [ ] **Step 4: Run to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_routes.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/feedback.py tests/test_feedback_routes.py
git commit -m "feat(feedback): multipart POST creates Odoo task + attachments"
```

---

## Task 7: Route — `GET /api/feedback/mine`

**Files:**
- Modify: `src/zira_dashboard/routes/feedback.py`
- Test: `tests/test_feedback_mine_route.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_feedback_mine_route.py`:

```python
"""Tests for GET /api/feedback/mine (store + Odoo monkeypatched)."""

from fastapi.testclient import TestClient

from zira_dashboard import feedback_store, odoo_client
from zira_dashboard.app import app

client = TestClient(app)


def _rows():
    return [
        {"id": 2, "created_at": "2026-06-24 10:00", "submitter": None,
         "page_url": "/p", "task_type": "bug", "odoo_task_id": 901,
         "message": "Totals wrong\nmore detail"},
        {"id": 1, "created_at": "2026-06-23 09:00", "submitter": None,
         "page_url": None, "task_type": "feature", "odoo_task_id": 902,
         "message": "Add export"},
    ]


def test_mine_merges_live_status(monkeypatch):
    monkeypatch.setattr(feedback_store, "for_submitter", lambda upn, limit=100: _rows())
    monkeypatch.setattr(
        odoo_client, "fetch_task_stage_names",
        lambda ids: {901: "Done", 902: "Rejected"},
    )

    resp = client.get("/api/feedback/mine")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0]["type"] == "bug"
    assert items[0]["title"] == "Totals wrong"
    assert items[0]["status"] == "done"
    assert items[1]["status"] == "rejected"


def test_mine_defaults_open_when_odoo_unavailable(monkeypatch):
    monkeypatch.setattr(feedback_store, "for_submitter", lambda upn, limit=100: _rows())

    def boom(ids):
        raise RuntimeError("odoo down")

    monkeypatch.setattr(odoo_client, "fetch_task_stage_names", boom)

    resp = client.get("/api/feedback/mine")

    assert resp.status_code == 200
    body = resp.json()
    assert all(it["status"] == "open" for it in body["items"])
    assert body["status_available"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_mine_route.py -v`
Expected: FAIL — route 404 / undefined.

- [ ] **Step 3: Implement**

Append to `routes/feedback.py`:

```python
@router.get("/api/feedback/mine")
def my_feedback(request: Request) -> JSONResponse:
    submitter = getattr(request.state, "user_upn", None)
    rows = feedback_store.for_submitter(submitter)
    task_ids = [r["odoo_task_id"] for r in rows if r.get("odoo_task_id")]
    status_available = True
    try:
        stages = odoo_client.fetch_task_stage_names(task_ids) if task_ids else {}
    except Exception:
        log.exception("feedback: could not read task stages")
        stages = {}
        status_available = False

    items = []
    for r in rows:
        message = (r.get("message") or "").strip()
        title = message.splitlines()[0] if message else "(no description)"
        if len(title) > _TITLE_MAX:
            title = title[: _TITLE_MAX - 1].rstrip() + "…"
        items.append({
            "type": r.get("task_type") or "bug",
            "title": title,
            "created_at": str(r.get("created_at") or ""),
            "page_url": r.get("page_url"),
            "status": odoo_client.feedback_status_bucket(stages.get(r.get("odoo_task_id"))),
        })

    return JSONResponse({"ok": True, "items": items, "status_available": status_available})
```

- [ ] **Step 4: Run to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_mine_route.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/feedback.py tests/test_feedback_mine_route.py
git commit -m "feat(feedback): GET /api/feedback/mine with live Odoo status"
```

---

## Task 8: Remove the legacy admin feedback page + nav tab

**Files:**
- Modify: `src/zira_dashboard/routes/feedback.py` (remove `admin_feedback`, drop `templates`/`HTMLResponse` imports if now unused)
- Delete: `src/zira_dashboard/templates/admin_feedback.html`
- Modify: `src/zira_dashboard/templates/settings.html:21` (remove the nav tab)
- Test: `tests/test_feedback_routes.py`

- [ ] **Step 1: Add a test that the admin route is gone**

Append to `tests/test_feedback_routes.py`:

```python
def test_admin_feedback_route_removed():
    resp = client.get("/admin/feedback")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_routes.py::test_admin_feedback_route_removed -v`
Expected: FAIL — route still returns 200.

- [ ] **Step 3: Remove the route + template + nav tab**

- In `routes/feedback.py`, delete the `admin_feedback` handler. The redesigned
  file (Task 6) already dropped `templates` and `HTMLResponse` imports — confirm
  none remain referenced.
- Delete the template:

```bash
git rm src/zira_dashboard/templates/admin_feedback.html
```

- In `src/zira_dashboard/templates/settings.html`, delete line 21:

```html
    <a href="/admin/feedback">Feedback</a>
```

- [ ] **Step 4: Run to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_feedback_routes.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/feedback.py src/zira_dashboard/templates/settings.html
git commit -m "feat(feedback): remove legacy admin feedback page + nav tab"
```

---

## Task 9: Footer template — Send-feedback + View-feedback modals

**Files:**
- Modify: `src/zira_dashboard/templates/_footer.html`
- Test: `tests/test_whatsnew_panel_static.py`

- [ ] **Step 1: Update the footer static template test**

In `tests/test_whatsnew_panel_static.py`, replace `test_footer_template_uses_panel_without_old_text_link` with:

```python
def test_footer_template_uses_panel_without_old_text_link():
    html = TEMPLATE.read_text(encoding="utf-8")

    assert "app-footer" not in html
    assert "changelog-open" not in html
    assert "changelog-markall" in html
    # Old inline feedback form is gone; new modals + buttons present.
    assert "changelog-feedback-toggle" not in html
    assert 'id="fb-open"' in html
    assert 'id="fb-view-open"' in html
    assert 'id="fb-modal"' in html
    assert 'id="fb-view-modal"' in html
    assert 'id="fb-desc"' in html
    assert 'data-type="bug"' in html
    assert 'data-type="feature"' in html
    assert 'id="fb-file-input"' in html
```

- [ ] **Step 2: Run to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_whatsnew_panel_static.py::test_footer_template_uses_panel_without_old_text_link -v`
Expected: FAIL — new ids absent.

- [ ] **Step 3: Edit `_footer.html`**

Replace the header-actions block + the inline feedback `<form>` (current lines 9–29) so the head actions are:

```html
      <div class="changelog-head-actions">
        <button type="button" id="fb-open" class="changelog-feedback-btn">Send feedback</button>
        <button type="button" id="fb-view-open" class="changelog-feedback-btn">View Feedback</button>
        <button type="button" id="changelog-close" class="changelog-close" aria-label="Close">Close</button>
      </div>
```

Delete the `<form id="changelog-feedback">…</form>` block entirely.

Then, after the closing `</div>` of `#changelog-modal` (before the `<link>`/`<script>` tags at the bottom), add the two new modals:

```html
<div id="fb-modal" class="fb-modal" hidden>
  <div class="fb-backdrop" id="fb-backdrop"></div>
  <div class="fb-card" role="dialog" aria-modal="true" aria-label="Send feedback">
    <div class="fb-head">
      <h3>Send feedback</h3>
      <button type="button" id="fb-close" class="fb-close" aria-label="Close">Close</button>
    </div>
    <div class="fb-type" role="group" aria-label="Feedback type">
      <button type="button" class="fb-type-btn is-active" data-type="bug" aria-pressed="true">Bug</button>
      <button type="button" class="fb-type-btn" data-type="feature" aria-pressed="false">Feature request</button>
    </div>
    <label class="fb-label" for="fb-desc">Description</label>
    <textarea id="fb-desc" class="fb-desc" rows="5"
              placeholder="What broke, and what did you expect?"></textarea>
    <div class="fb-attachments" id="fb-attachments"></div>
    <div class="fb-actions-row">
      <button type="button" id="fb-upload-btn" class="fb-upload">Upload files</button>
      <input type="file" id="fb-file-input" class="fb-file-input" multiple
             accept="image/*,application/pdf" hidden>
      <span class="fb-hint">or paste a screenshot</span>
    </div>
    <div class="fb-footer">
      <span id="fb-status" class="fb-status" hidden></span>
      <button type="button" id="fb-cancel" class="fb-cancel">Cancel</button>
      <button type="button" id="fb-submit" class="fb-submit">Send feedback</button>
    </div>
  </div>
</div>

<div id="fb-view-modal" class="fb-modal" hidden>
  <div class="fb-backdrop" id="fb-view-backdrop"></div>
  <div class="fb-card" role="dialog" aria-modal="true" aria-label="Your feedback">
    <div class="fb-head">
      <h3>Your feedback</h3>
      <button type="button" id="fb-view-close" class="fb-close" aria-label="Close">Close</button>
    </div>
    <div id="fb-view-body" class="fb-view-body">Loading…</div>
  </div>
</div>
```

- [ ] **Step 4: Run to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_whatsnew_panel_static.py -v`
Expected: the template test PASSES. The CSS/JS tests in this file still fail (fixed in Tasks 10–11) — that's expected; do not commit until they pass, but you can proceed.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/_footer.html tests/test_whatsnew_panel_static.py
git commit -m "feat(feedback): new Send/View feedback modal markup"
```

---

## Task 10: Footer CSS — green modal, toggle, chips, status pills

**Files:**
- Modify: `src/zira_dashboard/static/footer.css`
- Test: `tests/test_whatsnew_panel_static.py`

- [ ] **Step 1: Update the CSS static test**

In `tests/test_whatsnew_panel_static.py`, replace `test_footer_css_has_whatsnew_trigger_and_card_styles` with:

```python
def test_footer_css_has_whatsnew_trigger_and_card_styles():
    css = CSS.read_text(encoding="utf-8")

    assert ".app-footer" not in css
    assert ".changelog-deploy" not in css
    assert ".whatsnew-btn" in css
    assert ".whatsnew-dot" in css
    assert ".cl-entry" in css
    assert ".cl-badge" in css
    # New feedback modal styles.
    assert ".fb-modal" in css
    assert ".fb-card" in css
    assert ".fb-type-btn" in css
    assert ".fb-submit" in css
    assert ".fb-attachment-chip" in css
    assert ".fb-status-pill" in css
```

- [ ] **Step 2: Run to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_whatsnew_panel_static.py::test_footer_css_has_whatsnew_trigger_and_card_styles -v`
Expected: FAIL — `.fb-modal` etc. absent.

- [ ] **Step 3: Append styles to `footer.css`**

Add (uses the app's existing green-accent CSS vars with hard-coded fallbacks matching the current palette, e.g. `#16a34a` / `#dcfce7`):

```css
/* ---------- Feedback modals (Send + View) ---------- */
.fb-modal { position: fixed; inset: 0; z-index: 1000; display: flex;
  align-items: center; justify-content: center; }
.fb-modal[hidden] { display: none; }
.fb-backdrop { position: absolute; inset: 0; background: rgba(15, 23, 42, 0.45); }
.fb-card { position: relative; width: 480px; max-width: calc(100vw - 2rem);
  max-height: calc(100vh - 2rem); overflow: auto; background: #fff;
  border-radius: 14px; padding: 1.25rem 1.5rem;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.25); }
.fb-head { display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 1.1rem; }
.fb-head h3 { margin: 0; font-size: 1.2rem; }
.fb-close { border: none; background: none; font-size: 1rem; font-weight: 600;
  color: #6b7280; cursor: pointer; }
.fb-close:hover { color: #111827; }

.fb-type { display: inline-flex; background: #f1f5f9; border-radius: 9px;
  padding: 3px; margin-bottom: 1.1rem; gap: 2px; }
.fb-type-btn { border: none; background: none; font-size: 0.9rem; font-weight: 600;
  padding: 6px 18px; border-radius: 7px; color: #6b7280; cursor: pointer; }
.fb-type-btn.is-active { background: var(--accent, #16a34a); color: #fff; }

.fb-label { display: block; font-size: 0.9rem; font-weight: 600; margin-bottom: 6px; }
.fb-desc { width: 100%; box-sizing: border-box; border: 1px solid #d1d5db;
  border-radius: 8px; padding: 10px 12px; font: inherit; resize: vertical;
  min-height: 104px; }
.fb-desc:focus { outline: none; border-color: var(--accent, #16a34a);
  box-shadow: 0 0 0 3px var(--accent-dim, #dcfce7); }

.fb-attachments { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.fb-attachment-chip { display: inline-flex; align-items: center; gap: 8px;
  background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 8px;
  padding: 6px 8px; font-size: 0.78rem; color: #475569; }
.fb-attachment-chip img { width: 28px; height: 28px; object-fit: cover;
  border-radius: 4px; }
.fb-attachment-remove { border: none; background: none; color: #94a3b8;
  cursor: pointer; font-size: 0.9rem; line-height: 1; }
.fb-attachment-remove:hover { color: #ef4444; }

.fb-actions-row { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
.fb-upload { display: inline-flex; align-items: center; gap: 6px; font-size: 0.85rem;
  font-weight: 600; border: 1px solid #d1d5db; background: #fff; border-radius: 8px;
  padding: 7px 12px; cursor: pointer; }
.fb-upload:hover { background: #f8fafc; }
.fb-hint { font-size: 0.78rem; color: #9ca3af; }

.fb-footer { display: flex; align-items: center; justify-content: flex-end;
  gap: 10px; margin-top: 1.4rem; }
.fb-status { margin-right: auto; font-size: 0.82rem; color: #6b7280; }
.fb-cancel { border: 1px solid #d1d5db; background: #fff; border-radius: 8px;
  padding: 8px 16px; font-weight: 600; cursor: pointer; }
.fb-submit { border: none; background: var(--accent, #16a34a); color: #fff;
  border-radius: 8px; padding: 8px 18px; font-weight: 600; cursor: pointer; }
.fb-submit:disabled { opacity: 0.55; cursor: default; }

.fb-view-body { font-size: 0.9rem; }
.fb-view-item { display: flex; align-items: center; gap: 10px;
  padding: 10px 0; border-bottom: 1px solid #eef2f7; }
.fb-view-item:last-child { border-bottom: none; }
.fb-view-main { flex: 1; min-width: 0; }
.fb-view-title { font-weight: 600; color: #1f2937; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.fb-view-meta { font-size: 0.76rem; color: #9ca3af; }
.fb-view-empty { color: #6b7280; padding: 0.5rem 0; }

.fb-status-pill { font-size: 0.74rem; font-weight: 700; padding: 3px 10px;
  border-radius: 999px; text-transform: capitalize; white-space: nowrap; }
.fb-status-pill.is-open { background: var(--accent-dim, #dcfce7);
  color: var(--accent, #15803d); }
.fb-status-pill.is-done { background: #dcfce7; color: #15803d; }
.fb-status-pill.is-rejected { background: #fee2e2; color: #b91c1c; }
```

- [ ] **Step 4: Run to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_whatsnew_panel_static.py::test_footer_css_has_whatsnew_trigger_and_card_styles -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/static/footer.css tests/test_whatsnew_panel_static.py
git commit -m "feat(feedback): green modal + status-pill styles"
```

---

## Task 11: Footer JS — modal behavior, multipart submit, paste, View list

**Files:**
- Modify: `src/zira_dashboard/static/footer.js`
- Test: `tests/test_whatsnew_panel_static.py`

- [ ] **Step 1: Update the JS static test**

In `tests/test_whatsnew_panel_static.py`, replace `test_footer_js_injects_trigger_read_state_and_feedback_submit` with:

```python
def test_footer_js_injects_trigger_read_state_and_feedback_submit():
    js = JS.read_text(encoding="utf-8")

    assert "document.getElementById('changelog-open')" not in js
    assert "function injectButton()" in js
    assert "changelog_cutoff" in js
    assert "changelog_read" in js
    assert "function markAllRead()" in js
    assert "function makeBadgeModal" in js
    # New feedback modal wiring.
    assert "function submitFeedback" in js
    assert "FormData" in js
    assert "window.gpiFetch('/feedback'" in js
    assert "/api/feedback/mine" in js
    assert "function renderMyFeedback" in js
    assert "'paste'" in js
```

- [ ] **Step 2: Run to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_whatsnew_panel_static.py::test_footer_js_injects_trigger_read_state_and_feedback_submit -v`
Expected: FAIL — `FormData` / `/api/feedback/mine` / `renderMyFeedback` absent.

- [ ] **Step 3: Replace feedback logic in `footer.js`**

In the first IIFE, remove the old `toggleFeedback` and `submitFeedback` functions and their wiring inside `ensureModal` (the `feedbackToggle`/`feedbackForm`/`feedbackCancel` lines). Then add a new self-contained IIFE at the end of the file:

```javascript
// ---------- Feedback modal (Send) + View Feedback list ----------
(function () {
  var PLACEHOLDERS = {
    bug: 'What broke, and what did you expect?',
    feature: 'What would you like to see, and why?',
  };
  var attachments = [];   // {file, name, url}
  var currentType = 'bug';

  function $(id) { return document.getElementById(id); }

  function openModal(el) {
    if (!el) return;
    el.hidden = false;
    document.documentElement.style.overflow = 'hidden';
  }
  function closeModal(el) {
    if (!el) return;
    el.hidden = true;
    document.documentElement.style.overflow = '';
  }

  function resetSendForm() {
    attachments = [];
    currentType = 'bug';
    var desc = $('fb-desc');
    if (desc) { desc.value = ''; desc.placeholder = PLACEHOLDERS.bug; }
    setType('bug');
    renderAttachments();
    var status = $('fb-status');
    if (status) { status.hidden = true; status.textContent = ''; }
  }

  function setType(type) {
    currentType = (type === 'feature') ? 'feature' : 'bug';
    Array.prototype.forEach.call(document.querySelectorAll('.fb-type-btn'), function (btn) {
      var active = btn.getAttribute('data-type') === currentType;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    var desc = $('fb-desc');
    if (desc) desc.placeholder = PLACEHOLDERS[currentType];
  }

  function addFiles(fileList) {
    Array.prototype.forEach.call(fileList || [], function (file) {
      if (!file) return;
      var isImage = /^image\//.test(file.type);
      attachments.push({
        file: file,
        name: file.name || (isImage ? 'screenshot.png' : 'file'),
        url: isImage ? URL.createObjectURL(file) : null,
      });
    });
    renderAttachments();
  }

  function renderAttachments() {
    var box = $('fb-attachments');
    if (!box) return;
    box.innerHTML = '';
    attachments.forEach(function (att, idx) {
      var chip = document.createElement('span');
      chip.className = 'fb-attachment-chip';
      if (att.url) {
        var img = document.createElement('img');
        img.src = att.url; img.alt = '';
        chip.appendChild(img);
      }
      var label = document.createElement('span');
      label.textContent = att.name;
      chip.appendChild(label);
      var rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'fb-attachment-remove';
      rm.setAttribute('aria-label', 'Remove attachment');
      rm.textContent = '×';
      rm.addEventListener('click', function () {
        attachments.splice(idx, 1);
        renderAttachments();
      });
      chip.appendChild(rm);
      box.appendChild(chip);
    });
  }

  function submitFeedback() {
    var desc = $('fb-desc');
    var status = $('fb-status');
    var submit = $('fb-submit');
    var message = ((desc && desc.value) || '').trim();
    if (status) status.hidden = false;
    if (!message) { if (status) status.textContent = 'Please enter a description.'; return; }
    if (submit) submit.disabled = true;
    if (status) status.textContent = 'Sending…';

    var form = new FormData();
    form.append('type', currentType);
    form.append('description', message);
    form.append('page_url', window.location.href);
    attachments.forEach(function (att) { form.append('files', att.file, att.name); });

    window.gpiFetch('/feedback', { method: 'POST', body: form })
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (resp && resp.ok) {
          if (status) status.textContent = 'Thanks — sent!';
          setTimeout(function () { closeModal($('fb-modal')); resetSendForm(); }, 1200);
        } else if (status) {
          status.textContent = 'Failed: ' + ((resp && resp.error) || 'unknown');
        }
        if (submit) submit.disabled = false;
      })
      .catch(function () {
        if (status) status.textContent = 'Network error.';
        if (submit) submit.disabled = false;
      });
  }

  function statusLabel(s) {
    return { open: 'Open', done: 'Done', rejected: 'Rejected' }[s] || 'Open';
  }

  function renderMyFeedback(data) {
    var body = $('fb-view-body');
    if (!body) return;
    var items = (data && data.items) || [];
    if (!items.length) {
      body.innerHTML = '<p class="fb-view-empty">You haven\'t sent any feedback yet.</p>';
      return;
    }
    body.innerHTML = '';
    items.forEach(function (it) {
      var row = document.createElement('div');
      row.className = 'fb-view-item';
      var main = document.createElement('div');
      main.className = 'fb-view-main';
      var title = document.createElement('div');
      title.className = 'fb-view-title';
      title.textContent = it.title;
      var meta = document.createElement('div');
      meta.className = 'fb-view-meta';
      var typeLabel = it.type === 'feature' ? 'Feature request' : 'Bug';
      meta.textContent = typeLabel + ' · ' + (it.created_at || '').slice(0, 10);
      main.appendChild(title); main.appendChild(meta);
      var pill = document.createElement('span');
      pill.className = 'fb-status-pill is-' + (it.status || 'open');
      pill.textContent = statusLabel(it.status);
      row.appendChild(main); row.appendChild(pill);
      body.appendChild(row);
    });
    if (data && data.status_available === false) {
      var note = document.createElement('p');
      note.className = 'fb-view-empty';
      note.textContent = 'Status temporarily unavailable.';
      body.appendChild(note);
    }
  }

  function openView() {
    var body = $('fb-view-body');
    if (body) body.textContent = 'Loading…';
    openModal($('fb-view-modal'));
    window.gpiFetch('/api/feedback/mine')
      .then(function (r) { return r.json(); })
      .then(renderMyFeedback)
      .catch(function () {
        if (body) body.innerHTML = '<p class="fb-view-empty">Could not load your feedback.</p>';
      });
  }

  function wire() {
    var openBtn = $('fb-open');
    var viewBtn = $('fb-view-open');
    if (!openBtn && !viewBtn) return;
    if (openBtn) openBtn.addEventListener('click', function () {
      resetSendForm(); openModal($('fb-modal')); var d = $('fb-desc'); if (d) d.focus();
    });
    if (viewBtn) viewBtn.addEventListener('click', openView);

    var close = $('fb-close'), cancel = $('fb-cancel'), backdrop = $('fb-backdrop');
    [close, cancel, backdrop].forEach(function (el) {
      if (el) el.addEventListener('click', function () { closeModal($('fb-modal')); });
    });
    var vClose = $('fb-view-close'), vBackdrop = $('fb-view-backdrop');
    [vClose, vBackdrop].forEach(function (el) {
      if (el) el.addEventListener('click', function () { closeModal($('fb-view-modal')); });
    });

    Array.prototype.forEach.call(document.querySelectorAll('.fb-type-btn'), function (btn) {
      btn.addEventListener('click', function () { setType(btn.getAttribute('data-type')); });
    });

    var uploadBtn = $('fb-upload-btn'), fileInput = $('fb-file-input');
    if (uploadBtn && fileInput) {
      uploadBtn.addEventListener('click', function () { fileInput.click(); });
      fileInput.addEventListener('change', function () { addFiles(fileInput.files); fileInput.value = ''; });
    }

    var desc = $('fb-desc');
    if (desc) {
      desc.addEventListener('paste', function (event) {
        var items = (event.clipboardData && event.clipboardData.items) || [];
        var imgs = [];
        Array.prototype.forEach.call(items, function (it) {
          if (it.kind === 'file' && /^image\//.test(it.type)) {
            var f = it.getAsFile();
            if (f) imgs.push(f);
          }
        });
        if (imgs.length) { event.preventDefault(); addFiles(imgs); }
      });
    }

    var submit = $('fb-submit');
    if (submit) submit.addEventListener('click', submitFeedback);

    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape') return;
      var m = $('fb-modal'), v = $('fb-view-modal');
      if (m && !m.hidden) closeModal(m);
      if (v && !v.hidden) closeModal(v);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else {
    wire();
  }
})();
```

- [ ] **Step 4: Run to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_whatsnew_panel_static.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/static/footer.js tests/test_whatsnew_panel_static.py
git commit -m "feat(feedback): modal behavior, multipart submit, paste, View list"
```

---

## Task 12: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest -q`
Expected: all non-DB-gated tests pass; DATABASE_URL/Odoo-gated tests skip (per `tests/conftest.py`).

- [ ] **Step 2: Grep for stragglers referencing removed surfaces**

Run: `grep -rn "admin/feedback\|changelog-feedback\|feedback_store.recent\|\.recent(" src/ tests/`
Expected: no references to `/admin/feedback`, `changelog-feedback*`, or `feedback_store.recent`.

- [ ] **Step 3: Manual smoke (optional, needs live Odoo env)**

Use the `run` / `verify` skill to launch the app, open the What's New panel,
click "Send feedback", submit a Bug with a pasted screenshot, confirm a task
appears in the Odoo "Plant Manager" project assigned to you and due today, then
open "View Feedback" and confirm the item shows with status "Open".

- [ ] **Step 4: Final commit (only if any straggler fixes were needed)**

```bash
git add -A
git commit -m "chore(feedback): cleanup stragglers after redesign"
```

---

## Self-review (completed during planning)

- **Spec coverage:** modal UI (T9–11), Bug/Feature toggle + dynamic placeholder
  (T9, T11), file picker + paste (T11), green styling (T10), Odoo project
  find-or-create + stages (T3), task create assigned to owner + due today +
  tag + body (T4, T6), attachments (T4, T6), local index row (T2, T6), View
  Feedback + Open/Done/Rejected status (T5, T7, T11), removal of admin page +
  nav tab (T8), schema columns (T1), tests throughout. All spec sections map to
  tasks.
- **Placeholder scan:** no TBD/TODO; every code step shows full code.
- **Type consistency:** helper names (`ensure_feedback_project`,
  `ensure_feedback_tag`, `create_feedback_task`, `add_task_attachment`,
  `fetch_task_stage_names`, `feedback_status_bucket`), the `insert(...)` /
  `for_submitter(...)` store signatures, the route field names (`type`,
  `description`, `page_url`, `files`), and DOM ids (`fb-open`, `fb-view-open`,
  `fb-modal`, `fb-view-modal`, `fb-desc`, `fb-file-input`, `fb-submit`) are used
  consistently across tasks and tests.
