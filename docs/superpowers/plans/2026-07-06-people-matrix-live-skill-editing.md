# People Matrix Live Skill Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let supervisors edit People Matrix skill levels from a compact 0/1/2/3 picker and sync accepted changes to Odoo live.

**Architecture:** Keep Odoo as the write source of truth: the browser posts one cell edit, the server writes Odoo first, then mirrors the accepted value into `person_skills` and invalidates roster/matrix caches. The matrix emits Odoo ids for editable cells, while cells missing ids stay read-only. The Odoo client owns all `hr.employee.skill` lookup, create, update, delete, and duplicate cleanup logic.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, Postgres via `psycopg2`, Odoo XML-RPC through `xmlrpc.client`, vanilla JavaScript, pytest.

## Global Constraints

- Interaction: clicking a skill cell opens a compact picker with exact choices: `0 not trained`, `1 practicing`, `2 competent`, `3 proficient`.
- Choosing a level writes to Odoo immediately.
- Odoo wins. The local database updates only after the Odoo write succeeds.
- On failure, keep the previous cell value, show an error toast, and do not leave a local dirty shadow edit.
- The existing "Refresh from Odoo" button still pulls the matrix from Odoo and reconciles external changes.
- Cells without person or skill Odoo ids render read-only.
- Duplicate `hr.employee.skill` rows are normalized by the write helper: bucket 0 unlinks all matches; buckets 1-3 update the first match and unlink the remaining matches.
- Use ASCII in all edited files unless the file already contains the specific non-ASCII character being edited.

---

## File Structure

- `src/zira_dashboard/odoo_client.py` - extend skill metadata fetches and add Odoo `hr.employee.skill` write helper.
- `src/zira_dashboard/odoo_sync.py` - persist `skills.odoo_id` during sync.
- `src/zira_dashboard/routes/skills.py` - add one JSON cell-update route and local mirror helper.
- `src/zira_dashboard/templates/skills.html` - render editable cell controls only when both Odoo ids exist.
- `src/zira_dashboard/static/skills-page.js` - add the one-at-a-time skill picker, POST behavior, and cell state updates.
- `src/zira_dashboard/static/skills.css` - add editable cell, saving, picker, and error state styles.
- `tests/test_odoo_client.py` - cover skill metadata ids and Odoo write helper calls.
- `tests/test_odoo_sync.py` - cover `skills.odoo_id` persistence.
- `tests/test_skills_cell_update.py` - cover the JSON update route with monkeypatched DB and Odoo calls.
- `tests/test_skills_template_render.py` - cover editable/read-only skill cell markup.
- `tests/test_skills_static.py` - cover picker behavior hooks in the static JS/CSS.

---

### Task 1: Odoo Skill Metadata And Write Helper

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py`
- Modify: `tests/test_odoo_client.py`

**Interfaces:**
- Consumes: existing `execute(model, method, *args, **kwargs)`, `unwrap_m2o(val)`, and `fetch_skill_level_buckets()`.
- Produces: `fetch_skill_columns_with_types() -> list[dict]` rows containing `{"id": int, "name": str, "type": str}` and `set_employee_skill_level(employee_odoo_id: int, skill_odoo_id: int, bucket: int) -> None`.

- [ ] **Step 1: Add failing tests for skill ids and Odoo create/update/unlink**

Append these tests to `tests/test_odoo_client.py`:

```python
def test_fetch_skill_columns_with_types_includes_odoo_id(monkeypatch):
    responses = {
        ("hr.skill.type", "search_read"): [
            {"id": 1, "name": "Production Skills"},
            {"id": 2, "name": "Supervisor Skills"},
        ],
        ("hr.skill", "search_read"): [
            {"id": 10, "name": "Repair", "skill_type_id": [1, "Production Skills"]},
            {"id": 11, "name": "Dismantle", "skill_type_id": [1, "Production Skills"]},
            {"id": 20, "name": "Lead", "skill_type_id": [2, "Supervisor Skills"]},
        ],
    }
    _stub_execute(monkeypatch, responses)

    assert odoo_client.fetch_skill_columns_with_types() == [
        {"id": 11, "name": "Dismantle", "type": "Production Skills"},
        {"id": 10, "name": "Repair", "type": "Production Skills"},
        {"id": 20, "name": "Lead", "type": "Supervisor Skills"},
    ]


def test_set_employee_skill_level_creates_missing_skill_row(monkeypatch):
    calls = []

    def fake_execute(model, method, *args, **kwargs):
        calls.append((model, method, args, kwargs))
        if (model, method) == ("hr.skill", "read"):
            return [{"id": 10, "skill_type_id": [1, "Production Skills"]}]
        if (model, method) == ("hr.skill.level", "search_read"):
            return [
                {"id": 100, "level_progress": 0, "skill_type_id": [1, "Production Skills"]},
                {"id": 101, "level_progress": 33, "skill_type_id": [1, "Production Skills"]},
                {"id": 102, "level_progress": 67, "skill_type_id": [1, "Production Skills"]},
                {"id": 103, "level_progress": 100, "skill_type_id": [1, "Production Skills"]},
            ]
        if (model, method) == ("hr.employee.skill", "search"):
            return []
        if (model, method) == ("hr.employee.skill", "create"):
            return 555
        raise AssertionError(f"unexpected call: {(model, method)}")

    monkeypatch.setattr(odoo_client, "execute", fake_execute)

    odoo_client.set_employee_skill_level(7, 10, 3)

    create_calls = [c for c in calls if c[0:2] == ("hr.employee.skill", "create")]
    assert create_calls == [
        (
            "hr.employee.skill",
            "create",
            ({
                "employee_id": 7,
                "skill_id": 10,
                "skill_type_id": 1,
                "skill_level_id": 103,
            },),
            {},
        )
    ]


def test_set_employee_skill_level_updates_first_duplicate_and_unlinks_rest(monkeypatch):
    calls = []

    def fake_execute(model, method, *args, **kwargs):
        calls.append((model, method, args, kwargs))
        if (model, method) == ("hr.skill", "read"):
            return [{"id": 10, "skill_type_id": [1, "Production Skills"]}]
        if (model, method) == ("hr.skill.level", "search_read"):
            return [
                {"id": 100, "level_progress": 0, "skill_type_id": [1, "Production Skills"]},
                {"id": 101, "level_progress": 33, "skill_type_id": [1, "Production Skills"]},
                {"id": 102, "level_progress": 67, "skill_type_id": [1, "Production Skills"]},
                {"id": 103, "level_progress": 100, "skill_type_id": [1, "Production Skills"]},
            ]
        if (model, method) == ("hr.employee.skill", "search"):
            return [55, 56]
        if (model, method) == ("hr.employee.skill", "write"):
            return True
        if (model, method) == ("hr.employee.skill", "unlink"):
            return True
        raise AssertionError(f"unexpected call: {(model, method)}")

    monkeypatch.setattr(odoo_client, "execute", fake_execute)

    odoo_client.set_employee_skill_level(7, 10, 2)

    assert (
        "hr.employee.skill",
        "write",
        ([55], {"skill_level_id": 102}),
        {},
    ) in calls
    assert (
        "hr.employee.skill",
        "unlink",
        ([56],),
        {},
    ) in calls


def test_set_employee_skill_level_zero_unlinks_all_existing_rows(monkeypatch):
    calls = []

    def fake_execute(model, method, *args, **kwargs):
        calls.append((model, method, args, kwargs))
        if (model, method) == ("hr.employee.skill", "search"):
            return [55, 56]
        if (model, method) == ("hr.employee.skill", "unlink"):
            return True
        raise AssertionError(f"unexpected call: {(model, method)}")

    monkeypatch.setattr(odoo_client, "execute", fake_execute)

    odoo_client.set_employee_skill_level(7, 10, 0)

    assert calls == [
        (
            "hr.employee.skill",
            "search",
            ([("employee_id", "=", 7), ("skill_id", "=", 10)],),
            {},
        ),
        ("hr.employee.skill", "unlink", ([55, 56],), {}),
    ]


def test_set_employee_skill_level_rejects_invalid_bucket():
    with pytest.raises(ValueError, match="bucket must be 0, 1, 2, or 3"):
        odoo_client.set_employee_skill_level(7, 10, 4)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_odoo_client.py::test_fetch_skill_columns_with_types_includes_odoo_id tests/test_odoo_client.py::test_set_employee_skill_level_creates_missing_skill_row tests/test_odoo_client.py::test_set_employee_skill_level_updates_first_duplicate_and_unlinks_rest tests/test_odoo_client.py::test_set_employee_skill_level_zero_unlinks_all_existing_rows tests/test_odoo_client.py::test_set_employee_skill_level_rejects_invalid_bucket -v
```

Expected: FAIL because `fetch_skill_columns_with_types()` omits `id` and `set_employee_skill_level()` does not exist.

- [ ] **Step 3: Implement skill id metadata and write helper**

In `src/zira_dashboard/odoo_client.py`, replace the `by_type` collection inside `fetch_skill_columns_with_types()` so it stores full skill rows and emits ids:

```python
    by_type: dict[int, list[dict]] = {tid: [] for tid in type_ids}
    for s in skills:
        tid = unwrap_m2o(s["skill_type_id"])
        by_type.setdefault(tid, []).append(s)
    out: list[dict] = []
    for tid in type_ids:
        for skill in sorted(by_type.get(tid, []), key=lambda row: str(row["name"]).lower()):
            out.append({
                "id": skill["id"],
                "name": skill["name"],
                "type": type_name_by_id[tid],
            })
    return out
```

Still in `src/zira_dashboard/odoo_client.py`, add this helper immediately before
`fetch_skill_level_buckets()`:

```python
def _bucket_for_level_count(rank: int, count: int) -> int:
    if count <= 1:
        return 0
    return max(0, min(3, round(rank * 3 / (count - 1))))
```

In `fetch_skill_level_buckets()`, replace:

```python
            if n <= 1:
                bucket = 0
            else:
                bucket = round(rank * 3 / (n - 1))
            out[lvl["id"]] = max(0, min(3, bucket))
```

with:

```python
            out[lvl["id"]] = _bucket_for_level_count(rank, n)
```

Then add the remaining Odoo write helpers immediately after
`fetch_skill_level_buckets()`:

```python


def _skill_type_id_for_skill(skill_odoo_id: int) -> int:
    rows = execute(
        "hr.skill",
        "read",
        [skill_odoo_id],
        fields=["skill_type_id"],
    )
    if not rows:
        raise ValueError(f"Skill {skill_odoo_id} not found in Odoo")
    type_id = unwrap_m2o(rows[0].get("skill_type_id"))
    if not type_id:
        raise ValueError(f"Skill {skill_odoo_id} has no skill type in Odoo")
    return int(type_id)


def _skill_level_id_for_bucket(skill_type_odoo_id: int, bucket: int) -> int:
    levels = execute(
        "hr.skill.level",
        "search_read",
        [("skill_type_id", "=", skill_type_odoo_id)],
        fields=["id", "level_progress", "skill_type_id"],
    )
    if not levels:
        raise ValueError(f"Skill type {skill_type_odoo_id} has no levels in Odoo")
    levels.sort(key=lambda lvl: lvl.get("level_progress", 0))
    by_bucket: dict[int, list[dict]] = {}
    count = len(levels)
    for rank, level_row in enumerate(levels):
        by_bucket.setdefault(_bucket_for_level_count(rank, count), []).append(level_row)
    candidates = by_bucket.get(bucket)
    if not candidates:
        raise ValueError(
            f"Skill type {skill_type_odoo_id} has no level mapped to bucket {bucket}"
        )
    return int(candidates[-1]["id"])


def set_employee_skill_level(employee_odoo_id: int, skill_odoo_id: int, bucket: int) -> None:
    """Create, update, or remove an Odoo hr.employee.skill row.

    `bucket` is the dashboard's 0-3 scale. Bucket 0 removes the employee/skill
    relation. Buckets 1-3 map back to the matching hr.skill.level for the
    skill's type.
    """
    if bucket not in (0, 1, 2, 3):
        raise ValueError("bucket must be 0, 1, 2, or 3")

    existing_ids = execute(
        "hr.employee.skill",
        "search",
        [("employee_id", "=", int(employee_odoo_id)), ("skill_id", "=", int(skill_odoo_id))],
    )

    if bucket == 0:
        if existing_ids:
            execute("hr.employee.skill", "unlink", existing_ids)
        return

    skill_type_id = _skill_type_id_for_skill(int(skill_odoo_id))
    skill_level_id = _skill_level_id_for_bucket(skill_type_id, bucket)
    values = {"skill_level_id": skill_level_id}

    if existing_ids:
        keep_id = int(existing_ids[0])
        execute("hr.employee.skill", "write", [keep_id], values)
        duplicate_ids = [int(i) for i in existing_ids[1:]]
        if duplicate_ids:
            execute("hr.employee.skill", "unlink", duplicate_ids)
        return

    execute(
        "hr.employee.skill",
        "create",
        {
            "employee_id": int(employee_odoo_id),
            "skill_id": int(skill_odoo_id),
            "skill_type_id": skill_type_id,
            "skill_level_id": skill_level_id,
        },
    )
```

- [ ] **Step 4: Run Odoo client tests**

Run:

```bash
pytest tests/test_odoo_client.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/zira_dashboard/odoo_client.py tests/test_odoo_client.py
git commit -m "feat(odoo): write employee skill levels"
```

---

### Task 2: Persist Skill Odoo IDs During Sync

**Files:**
- Modify: `src/zira_dashboard/odoo_sync.py`
- Modify: `tests/test_odoo_sync.py`

**Interfaces:**
- Consumes: Task 1 `fetch_skill_columns_with_types()` returns `id`.
- Produces: `skills.odoo_id` populated for synced skill columns.

- [ ] **Step 1: Add failing sync test for `skills.odoo_id`**

Append this test to `tests/test_odoo_sync.py`:

```python
def test_sync_stores_skill_odoo_ids(monkeypatch):
    from zira_dashboard import db

    _stub_client(
        monkeypatch,
        employees=[{"id": 99003, "name": "TestCara", "active": True, "work_email": False}],
        skills_for={99003: [{"skill_id": 7001, "skill_name": "TestRepair", "level_id": 103}]},
        columns_meta=[
            {"id": 7001, "name": "TestRepair", "type": "Production Skills"},
            {"id": 7002, "name": "TestDismantler", "type": "Production Skills"},
        ],
        buckets={103: 3},
    )

    result = odoo_sync.sync(force=True)

    assert result.ok is True
    rows = db.query(
        "SELECT name, odoo_id FROM skills WHERE name IN ('TestRepair', 'TestDismantler') ORDER BY name"
    )
    assert rows == [
        {"name": "TestDismantler", "odoo_id": 7002},
        {"name": "TestRepair", "odoo_id": 7001},
    ]
```

Update the `_clean_sync_state()` fixture in the same file so it removes the new test employee id:

```python
    db.execute("DELETE FROM person_skills WHERE person_id IN (SELECT id FROM people WHERE odoo_id BETWEEN 99000 AND 99999)")
    db.execute("DELETE FROM people WHERE odoo_id BETWEEN 99000 AND 99999")
```

The existing range already covers `99003`, so no SQL text change is required for the employee cleanup.

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_odoo_sync.py::test_sync_stores_skill_odoo_ids -v
```

Expected: FAIL with `odoo_id` equal to `None` for synced skills.

- [ ] **Step 3: Store `odoo_id` in skill upsert**

In `src/zira_dashboard/odoo_sync.py`, replace the skill upsert in the `for i, m in enumerate(columns_meta):` loop with:

```python
            cur.execute(
                "INSERT INTO skills (odoo_id, name, skill_type, sort_order, last_pulled_at) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (name) DO UPDATE SET odoo_id = EXCLUDED.odoo_id, "
                "skill_type = EXCLUDED.skill_type, "
                "sort_order = EXCLUDED.sort_order, last_pulled_at = EXCLUDED.last_pulled_at",
                (m.get("id"), m["name"], m.get("type", ""), i, pulled_at),
            )
```

- [ ] **Step 4: Run sync tests**

Run:

```bash
pytest tests/test_odoo_sync.py -v
```

Expected: PASS, or SKIP when `DATABASE_URL` is not set.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/zira_dashboard/odoo_sync.py tests/test_odoo_sync.py
git commit -m "feat(odoo): persist skill ids during sync"
```

---

### Task 3: Skill Cell Update Route And Local Mirror

**Files:**
- Modify: `src/zira_dashboard/routes/skills.py`
- Create: `tests/test_skills_cell_update.py`

**Interfaces:**
- Consumes: Task 1 `odoo_client.set_employee_skill_level(employee_odoo_id: int, skill_odoo_id: int, bucket: int) -> None`.
- Produces: `POST /staffing/skills/cell` accepting JSON `{"person_odoo_id": int, "skill_odoo_id": int, "level": int}` and returning `{"ok": true, "level": int}` on success.

- [ ] **Step 1: Add route tests with monkeypatched dependencies**

Create `tests/test_skills_cell_update.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zira_dashboard.routes.skills import router


def _client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_skill_cell_update_writes_odoo_then_mirrors_local(monkeypatch):
    from zira_dashboard.routes import skills as skills_routes

    calls = []

    def fake_query(sql, params=None):
        calls.append(("query", sql, params))
        if "FROM people" in sql:
            return [{"id": 1, "odoo_id": 77, "name": "Maria Garcia"}]
        if "FROM skills" in sql:
            return [{"id": 2, "odoo_id": 88, "name": "Repair", "skill_type": "Production Skills"}]
        return []

    class FakeCursor:
        def execute(self, sql, params=None):
            calls.append(("execute", sql, params))

    class FakeCursorContext:
        def __enter__(self):
            return FakeCursor()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(skills_routes.db, "query", fake_query)
    monkeypatch.setattr(skills_routes.db, "cursor", lambda: FakeCursorContext())
    monkeypatch.setattr(
        skills_routes.odoo_client,
        "set_employee_skill_level",
        lambda employee_id, skill_id, level: calls.append(("odoo", employee_id, skill_id, level)),
    )
    monkeypatch.setattr(skills_routes.staffing, "_invalidate_roster_cache", lambda: calls.append(("roster_cache",)))
    monkeypatch.setattr(skills_routes._http_cache, "invalidate_today_cache", lambda: calls.append(("today_cache",)))
    monkeypatch.setattr(skills_routes._http_cache, "invalidate_stable_cache", lambda: calls.append(("stable_cache",)))

    response = _client().post(
        "/staffing/skills/cell",
        json={"person_odoo_id": 77, "skill_odoo_id": 88, "level": 3},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "level": 3, "label": "proficient"}
    assert ("odoo", 77, 88, 3) in calls
    upserts = [c for c in calls if c[0] == "execute" and "INSERT INTO person_skills" in c[1]]
    assert upserts
    assert calls.index(("odoo", 77, 88, 3)) < calls.index(upserts[0])
    assert ("roster_cache",) in calls
    assert ("today_cache",) in calls
    assert ("stable_cache",) in calls


def test_skill_cell_update_zero_deletes_local_after_odoo_success(monkeypatch):
    from zira_dashboard.routes import skills as skills_routes

    calls = []

    def fake_query(sql, params=None):
        if "FROM people" in sql:
            return [{"id": 1, "odoo_id": 77, "name": "Maria Garcia"}]
        if "FROM skills" in sql:
            return [{"id": 2, "odoo_id": 88, "name": "Repair", "skill_type": "Production Skills"}]
        return []

    class FakeCursor:
        def execute(self, sql, params=None):
            calls.append((sql, params))

    class FakeCursorContext:
        def __enter__(self):
            return FakeCursor()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(skills_routes.db, "query", fake_query)
    monkeypatch.setattr(skills_routes.db, "cursor", lambda: FakeCursorContext())
    monkeypatch.setattr(skills_routes.odoo_client, "set_employee_skill_level", lambda *args: None)
    monkeypatch.setattr(skills_routes.staffing, "_invalidate_roster_cache", lambda: None)
    monkeypatch.setattr(skills_routes._http_cache, "invalidate_today_cache", lambda: None)
    monkeypatch.setattr(skills_routes._http_cache, "invalidate_stable_cache", lambda: None)

    response = _client().post(
        "/staffing/skills/cell",
        json={"person_odoo_id": 77, "skill_odoo_id": 88, "level": 0},
    )

    assert response.status_code == 200
    assert any("DELETE FROM person_skills" in sql for sql, _params in calls)


def test_skill_cell_update_rejects_invalid_level():
    response = _client().post(
        "/staffing/skills/cell",
        json={"person_odoo_id": 77, "skill_odoo_id": 88, "level": 4},
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "level" in response.json()["error"]


def test_skill_cell_update_rejects_missing_person(monkeypatch):
    from zira_dashboard.routes import skills as skills_routes

    monkeypatch.setattr(skills_routes.db, "query", lambda sql, params=None: [])

    response = _client().post(
        "/staffing/skills/cell",
        json={"person_odoo_id": 77, "skill_odoo_id": 88, "level": 2},
    )

    assert response.status_code == 404
    assert response.json() == {"ok": False, "error": "Person not found. Refresh from Odoo and try again."}


def test_skill_cell_update_rejects_non_matrix_skill(monkeypatch):
    from zira_dashboard.routes import skills as skills_routes

    def fake_query(sql, params=None):
        if "FROM people" in sql:
            return [{"id": 1, "odoo_id": 77, "name": "Maria Garcia"}]
        if "FROM skills" in sql:
            return [{"id": 2, "odoo_id": 88, "name": "Spanish", "skill_type": "Languages"}]
        return []

    monkeypatch.setattr(skills_routes.db, "query", fake_query)

    response = _client().post(
        "/staffing/skills/cell",
        json={"person_odoo_id": 77, "skill_odoo_id": 88, "level": 2},
    )

    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "Skill is not editable in the People Matrix."}


def test_skill_cell_update_leaves_local_state_when_odoo_fails(monkeypatch):
    from zira_dashboard.routes import skills as skills_routes

    calls = []

    def fake_query(sql, params=None):
        if "FROM people" in sql:
            return [{"id": 1, "odoo_id": 77, "name": "Maria Garcia"}]
        if "FROM skills" in sql:
            return [{"id": 2, "odoo_id": 88, "name": "Repair", "skill_type": "Production Skills"}]
        return []

    def fail_odoo(*args):
        raise RuntimeError("odoo down")

    class FakeCursor:
        def execute(self, sql, params=None):
            calls.append((sql, params))

    class FakeCursorContext:
        def __enter__(self):
            return FakeCursor()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(skills_routes.db, "query", fake_query)
    monkeypatch.setattr(skills_routes.db, "cursor", lambda: FakeCursorContext())
    monkeypatch.setattr(skills_routes.odoo_client, "set_employee_skill_level", fail_odoo)

    response = _client().post(
        "/staffing/skills/cell",
        json={"person_odoo_id": 77, "skill_odoo_id": 88, "level": 3},
    )

    assert response.status_code == 502
    assert response.json() == {"ok": False, "error": "Odoo save failed: odoo down"}
    assert calls == []
```

- [ ] **Step 2: Run route tests to verify failure**

Run:

```bash
pytest tests/test_skills_cell_update.py -v
```

Expected: FAIL during import or request because `/staffing/skills/cell` does not exist.

- [ ] **Step 3: Add module-level imports used by the endpoint**

In `src/zira_dashboard/routes/skills.py`, add these imports below the existing
`from .. import staffing` line:

```python
from .. import _http_cache, db, odoo_client
```

Keep the existing local imports inside older handlers. The new route uses module-level imports so tests can monkeypatch `skills_routes.db`, `skills_routes.odoo_client`, and `skills_routes._http_cache`.

- [ ] **Step 4: Implement validation, Odoo-first write, local mirror, and cache invalidation**

Add this helper and route below `staffing_skills_save()` in `src/zira_dashboard/routes/skills.py`:

```python
def _skill_cell_error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status_code)


def _level_label(level: int) -> str:
    return {0: "not trained", 1: "practicing", 2: "competent", 3: "proficient"}[level]


def _mirror_skill_level(person_id: int, skill_id: int, level: int) -> None:
    if level == 0:
        with db.cursor() as cur:
            cur.execute(
                "DELETE FROM person_skills WHERE person_id = %s AND skill_id = %s",
                (person_id, skill_id),
            )
        return

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO person_skills "
            "(person_id, skill_id, level, last_pushed_at, local_dirty) "
            "VALUES (%s, %s, %s, now(), FALSE) "
            "ON CONFLICT (person_id, skill_id) DO UPDATE SET "
            "level = EXCLUDED.level, last_pushed_at = EXCLUDED.last_pushed_at, "
            "local_dirty = FALSE",
            (person_id, skill_id, level),
        )


@router.post("/staffing/skills/cell")
async def staffing_skill_cell_update(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _skill_cell_error("Invalid JSON body.", 400)

    try:
        person_odoo_id = int(body.get("person_odoo_id"))
        skill_odoo_id = int(body.get("skill_odoo_id"))
        level = int(body.get("level"))
    except (TypeError, ValueError):
        return _skill_cell_error("person_odoo_id, skill_odoo_id, and level are required.", 400)

    if level not in (0, 1, 2, 3):
        return _skill_cell_error("level must be 0, 1, 2, or 3.", 400)

    def _work():
        person_rows = db.query(
            "SELECT id, odoo_id, name FROM people "
            "WHERE odoo_id = %s AND NOT excluded",
            (person_odoo_id,),
        )
        if not person_rows:
            return _skill_cell_error("Person not found. Refresh from Odoo and try again.", 404)

        skill_rows = db.query(
            "SELECT id, odoo_id, name, skill_type FROM skills WHERE odoo_id = %s",
            (skill_odoo_id,),
        )
        if not skill_rows:
            return _skill_cell_error("Skill not found. Refresh from Odoo and try again.", 404)

        skill = skill_rows[0]
        if skill["skill_type"] not in ("Production Skills", "Supervisor Skills"):
            return _skill_cell_error("Skill is not editable in the People Matrix.", 400)

        try:
            odoo_client.set_employee_skill_level(person_odoo_id, skill_odoo_id, level)
        except Exception as exc:
            return _skill_cell_error(f"Odoo save failed: {exc}", 502)

        _mirror_skill_level(int(person_rows[0]["id"]), int(skill["id"]), level)
        staffing._invalidate_roster_cache()
        _http_cache.invalidate_today_cache()
        _http_cache.invalidate_stable_cache()
        return JSONResponse({
            "ok": True,
            "level": level,
            "label": _level_label(level),
        })

    return await asyncio.to_thread(_work)
```

- [ ] **Step 5: Run route tests**

Run:

```bash
pytest tests/test_skills_cell_update.py -v
```

Expected: PASS.

- [ ] **Step 6: Run related cache/template tests**

Run:

```bash
pytest tests/test_skills_cache.py tests/test_skills_template_render.py -v
```

Expected: PASS, or the cache tests SKIP when `DATABASE_URL` is not set.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/zira_dashboard/routes/skills.py tests/test_skills_cell_update.py
git commit -m "feat(skills): add live skill cell update route"
```

---

### Task 4: Editable Skill Cell Markup

**Files:**
- Modify: `src/zira_dashboard/routes/skills.py`
- Modify: `src/zira_dashboard/templates/skills.html`
- Modify: `tests/test_skills_template_render.py`

**Interfaces:**
- Consumes: Task 2 `skills.odoo_id` values in the database.
- Produces: template context `skills` as rows containing `name`, `odoo_id`, and `skill_type`; skill cells render as `.skill-cell-btn` buttons when editable.

- [ ] **Step 1: Update template render helper for skill row objects**

In `tests/test_skills_template_render.py`, change `_render_skills_html()` to accept skill Odoo ids:

```python
def _render_skills_html(*, employee_id=None, odoo_url="", skill_odoo_id=88):
    person = SimpleNamespace(
        name="Maria Garcia",
        active=True,
        reserve=False,
        employee_id=employee_id,
        skills={"Repair": 2},
    )

    return _env().get_template("skills.html").render(
        active="skills",
        active_count=1,
        inactive_count=0,
        skills=[{"name": "Repair", "odoo_id": skill_odoo_id, "skill_type": "Production Skills"}],
        type_by_skill={"Repair": "Production Skills"},
        hidden_skills=[],
        person_certs={},
        people=[person],
        views=[],
        default_view_name=None,
        default_view_state=None,
        sync_last_at=None,
        sync_error=None,
        odoo_url=odoo_url,
    )
```

- [ ] **Step 2: Add failing template tests for editable and read-only cells**

Append these tests to `tests/test_skills_template_render.py`:

```python
def test_people_matrix_skill_cell_is_button_when_odoo_ids_exist():
    html = _render_skills_html(employee_id=42, skill_odoo_id=88)

    assert 'class="skill-cell-btn skill-display lvl-2"' in html
    assert 'data-person-odoo-id="42"' in html
    assert 'data-skill-odoo-id="88"' in html
    assert 'data-skill-name="Repair"' in html
    assert 'data-level="2"' in html
    assert 'aria-label="Edit Maria Garcia Repair skill, current level 2 competent"' in html


def test_people_matrix_skill_cell_stays_readonly_without_skill_odoo_id():
    html = _render_skills_html(employee_id=42, skill_odoo_id=None)

    assert 'data-skill-odoo-id=' not in html
    assert 'class="skill-display lvl-2"' in html
    assert 'skill-cell-btn' not in html
```

Update `test_people_matrix_sort_headers_are_keyboard_focusable()` to match row-object rendering:

```python
def test_people_matrix_sort_headers_are_keyboard_focusable():
    html = _render_skills_html()

    assert '<th class="name" style="text-align:left" role="button" tabindex="0" aria-sort="none">Name</th>' in html
    assert '<th role="button" tabindex="0" aria-sort="none">Reserve</th>' in html
    assert 'data-skill="Repair" data-type="Production Skills" class="skill-col" role="button" tabindex="0" aria-sort="none">Repair</th>' in html
```

- [ ] **Step 3: Run template tests to verify failure**

Run:

```bash
pytest tests/test_skills_template_render.py -v
```

Expected: FAIL because `skills.html` still treats `skills` as plain strings and renders read-only spans.

- [ ] **Step 4: Update route context to pass skill rows and JSON skill names**

In `src/zira_dashboard/routes/skills.py`, replace:

```python
    skill_rows = db.query(
        "SELECT name, skill_type FROM skills "
        "WHERE skill_type IN ('Production Skills', 'Supervisor Skills') "
        "ORDER BY skill_type, lower(name)"
    )
    columns = [r["name"] for r in skill_rows]
    type_by_skill = {r["name"]: r["skill_type"] for r in skill_rows}
```

with:

```python
    skill_rows = db.query(
        "SELECT name, odoo_id, skill_type FROM skills "
        "WHERE skill_type IN ('Production Skills', 'Supervisor Skills') "
        "ORDER BY skill_type, lower(name)"
    )
    columns = [
        {"name": r["name"], "odoo_id": r["odoo_id"], "skill_type": r["skill_type"]}
        for r in skill_rows
    ]
    skill_names = [r["name"] for r in skill_rows]
    type_by_skill = {r["name"]: r["skill_type"] for r in skill_rows}
```

In the template context, change:

```python
            "skills": columns,
```

to:

```python
            "skills": columns,
            "skill_names": skill_names,
```

- [ ] **Step 5: Update `skills.html` loops to use skill objects**

In `src/zira_dashboard/templates/skills.html`, replace the header skill loop:

```jinja2
          {% for s in skills %}<th data-skill="{{ s }}" data-type="{{ type_by_skill.get(s, '') }}" class="skill-col{% if s in hidden_skills %} col-hidden{% endif %}" role="button" tabindex="0" aria-sort="none">{{ s }}</th>{% endfor %}
```

with:

```jinja2
          {% for skill in skills %}
            {% set s = skill.name %}
            <th data-skill="{{ s }}" data-type="{{ type_by_skill.get(s, '') }}" class="skill-col{% if s in hidden_skills %} col-hidden{% endif %}" role="button" tabindex="0" aria-sort="none">{{ s }}</th>
          {% endfor %}
```

Replace the body skill loop:

```jinja2
          {% for s in skills %}
            {% set lvl = p.skills.get(s, 0) %}
            <td data-skill="{{ s }}" class="skill-col{% if s in hidden_skills %} col-hidden{% endif %}">
              <span class="skill-display lvl-{{ lvl }}">{{ lvl if lvl > 0 else '—' }}</span>
            </td>
          {% endfor %}
```

with:

```jinja2
          {% for skill in skills %}
            {% set s = skill.name %}
            {% set lvl = p.skills.get(s, 0) %}
            {% set level_label = {0: 'not trained', 1: 'practicing', 2: 'competent', 3: 'proficient'}[lvl] %}
            <td data-skill="{{ s }}" class="skill-col{% if s in hidden_skills %} col-hidden{% endif %}">
              {% if p.employee_id and skill.odoo_id %}
                <button
                  type="button"
                  class="skill-cell-btn skill-display lvl-{{ lvl }}"
                  data-person-odoo-id="{{ p.employee_id }}"
                  data-skill-odoo-id="{{ skill.odoo_id }}"
                  data-person-name="{{ p.name }}"
                  data-skill-name="{{ s }}"
                  data-level="{{ lvl }}"
                  aria-label="Edit {{ p.name }} {{ s }} skill, current level {{ lvl }} {{ level_label }}"
                >{{ lvl if lvl > 0 else '—' }}</button>
              {% else %}
                <span class="skill-display lvl-{{ lvl }}">{{ lvl if lvl > 0 else '—' }}</span>
              {% endif %}
            </td>
          {% endfor %}
```

In the inline script at the bottom, replace:

```jinja2
  window.SKILLS_ALL_SKILLS = {{ skills | tojson if skills is defined else '[]' }};
```

with:

```jinja2
  window.SKILLS_ALL_SKILLS = {{ skill_names | tojson if skill_names is defined else '[]' }};
```

- [ ] **Step 6: Run template tests**

Run:

```bash
pytest tests/test_skills_template_render.py -v
```

Expected: PASS.

- [ ] **Step 7: Run skills route cache smoke tests**

Run:

```bash
pytest tests/test_skills_cache.py -v
```

Expected: PASS, or SKIP when `DATABASE_URL` is not set.

- [ ] **Step 8: Commit**

Run:

```bash
git add src/zira_dashboard/routes/skills.py src/zira_dashboard/templates/skills.html tests/test_skills_template_render.py
git commit -m "feat(skills): render editable skill cells"
```

---

### Task 5: Picker UI, Live POST, And Styling

**Files:**
- Modify: `src/zira_dashboard/static/skills-page.js`
- Modify: `src/zira_dashboard/static/skills.css`
- Modify: `tests/test_skills_static.py`

**Interfaces:**
- Consumes: Task 3 `/staffing/skills/cell` JSON endpoint and Task 4 `.skill-cell-btn` markup.
- Produces: click/keyboard skill picker that updates a cell after successful POST and preserves old value after failure.

- [ ] **Step 1: Add static tests for picker hooks**

Append these tests to `tests/test_skills_static.py`:

```python
def test_people_matrix_skill_picker_posts_live_cell_update():
    js = Path("src/zira_dashboard/static/skills-page.js").read_text()

    assert "initSkillCellPicker" in js
    assert "fetch('/staffing/skills/cell'" in js
    assert "person_odoo_id" in js
    assert "skill_odoo_id" in js
    assert "updateSkillButton" in js


def test_people_matrix_skill_picker_handles_escape_and_focus_return():
    js = Path("src/zira_dashboard/static/skills-page.js").read_text()

    assert "skill-picker" in js
    assert "e.key === 'Escape'" in js
    assert "activeSkillButton.focus()" in js


def test_people_matrix_skill_picker_css_exists():
    css = Path("src/zira_dashboard/static/skills.css").read_text()

    assert ".skill-cell-btn" in css
    assert ".skill-cell-btn.saving" in css
    assert ".skill-picker" in css
```

- [ ] **Step 2: Run static tests to verify failure**

Run:

```bash
pytest tests/test_skills_static.py -v
```

Expected: FAIL because picker code and CSS do not exist yet.

- [ ] **Step 3: Add picker JavaScript**

In `src/zira_dashboard/static/skills-page.js`, insert this block after `showSavedToast()` and before the View popover block:

```javascript
  // ---------- Live skill cell picker ----------
  (function initSkillCellPicker() {
    const table = document.getElementById('skills-table');
    if (!table) return;

    const LEVELS = [
      { level: 0, label: 'not trained', text: '—' },
      { level: 1, label: 'practicing', text: '1' },
      { level: 2, label: 'competent', text: '2' },
      { level: 3, label: 'proficient', text: '3' },
    ];

    let picker = null;
    let activeSkillButton = null;

    function levelLabel(level) {
      const found = LEVELS.find(item => item.level === Number(level));
      return found ? found.label : 'not trained';
    }

    function closePicker() {
      if (picker) {
        picker.remove();
        picker = null;
      }
      if (activeSkillButton) {
        activeSkillButton.setAttribute('aria-expanded', 'false');
      }
    }

    function updateSkillButton(btn, level) {
      const numeric = Number(level);
      btn.dataset.level = String(numeric);
      btn.textContent = numeric > 0 ? String(numeric) : '—';
      btn.classList.remove('lvl-0', 'lvl-1', 'lvl-2', 'lvl-3');
      btn.classList.add('lvl-' + numeric);
      const person = btn.dataset.personName || 'person';
      const skill = btn.dataset.skillName || 'skill';
      btn.setAttribute(
        'aria-label',
        'Edit ' + person + ' ' + skill + ' skill, current level ' + numeric + ' ' + levelLabel(numeric)
      );
    }

    async function saveSkillLevel(btn, level) {
      const previousLevel = Number(btn.dataset.level || '0');
      if (Number(level) === previousLevel) {
        closePicker();
        return;
      }

      btn.disabled = true;
      btn.classList.add('saving');
      closePicker();

      try {
        const resp = await fetch('/staffing/skills/cell', {
          method: 'POST',
          headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            person_odoo_id: Number(btn.dataset.personOdooId),
            skill_odoo_id: Number(btn.dataset.skillOdooId),
            level: Number(level),
          }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.ok) {
          throw new Error(data.error || 'Odoo save failed');
        }
        updateSkillButton(btn, data.level);
        showSavedToast(null, 'Saved');
      } catch (e) {
        updateSkillButton(btn, previousLevel);
        showSavedToast(null, e && e.message ? e.message : 'Odoo save failed');
      } finally {
        btn.disabled = false;
        btn.classList.remove('saving');
        btn.focus();
      }
    }

    function openPicker(btn) {
      closePicker();
      activeSkillButton = btn;
      btn.setAttribute('aria-expanded', 'true');

      picker = document.createElement('div');
      picker.className = 'skill-picker';
      picker.id = 'skill-picker';
      picker.setAttribute('role', 'dialog');
      picker.setAttribute('aria-label', 'Choose skill level');

      LEVELS.forEach(item => {
        const choice = document.createElement('button');
        choice.type = 'button';
        choice.className = 'skill-picker-choice lvl-' + item.level;
        choice.dataset.level = String(item.level);
        choice.textContent = item.level + ' ' + item.label;
        if (item.level === 0) choice.textContent = '0 ' + item.label;
        choice.addEventListener('click', () => saveSkillLevel(btn, item.level));
        picker.appendChild(choice);
      });

      document.body.appendChild(picker);
      const rect = btn.getBoundingClientRect();
      picker.style.top = String(window.scrollY + rect.bottom + 4) + 'px';
      picker.style.left = String(window.scrollX + rect.left) + 'px';

      const first = picker.querySelector('button');
      if (first) first.focus();
    }

    table.addEventListener('click', e => {
      const btn = e.target.closest('.skill-cell-btn');
      if (!btn || btn.disabled) return;
      openPicker(btn);
    });

    table.addEventListener('keydown', e => {
      const btn = e.target.closest('.skill-cell-btn');
      if (!btn || btn.disabled) return;
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openPicker(btn);
      }
    });

    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && picker) {
        closePicker();
        if (activeSkillButton) activeSkillButton.focus();
      }
    });

    document.addEventListener('click', e => {
      if (!picker) return;
      if (picker.contains(e.target)) return;
      if (e.target.closest('.skill-cell-btn') === activeSkillButton) return;
      closePicker();
    });
  })();
```

- [ ] **Step 4: Add CSS for editable cells and picker**

In `src/zira_dashboard/static/skills.css`, replace the `.skill-display` block:

```css
  .skill-display { display: inline-block; min-width: 1.6rem; text-align: center;
                   padding: 0.15rem 0.35rem; border-radius: 4px;
                   background: transparent; border: 1px solid var(--border);
                   font-variant-numeric: tabular-nums; font-weight: 700; font-size: 0.82rem; }
```

with:

```css
  .skill-display { display: inline-block; min-width: 1.6rem; text-align: center;
                   padding: 0.15rem 0.35rem; border-radius: 4px;
                   background: transparent; border: 1px solid var(--border);
                   font-variant-numeric: tabular-nums; font-weight: 700; font-size: 0.82rem; }
  .skill-cell-btn {
    font: inherit;
    cursor: pointer;
    line-height: 1.2;
  }
  .skill-cell-btn:hover,
  .skill-cell-btn:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  .skill-cell-btn.saving {
    opacity: 0.65;
    cursor: wait;
  }
```

Add this picker CSS after the `.skill-display.lvl-0` rule:

```css
  .skill-picker {
    position: absolute;
    z-index: 200;
    display: grid;
    gap: 0.25rem;
    min-width: 9.5rem;
    padding: 0.35rem;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.35);
  }
  .skill-picker-choice {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: 0.35rem;
    width: 100%;
    padding: 0.3rem 0.45rem;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--panel-2);
    color: var(--fg);
    font: inherit;
    font-size: 0.82rem;
    cursor: pointer;
  }
  .skill-picker-choice:hover,
  .skill-picker-choice:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  .skill-picker-choice.lvl-0 { color: var(--muted); }
  .skill-picker-choice.lvl-1 { color: var(--warn); border-color: var(--warn-dim); }
  .skill-picker-choice.lvl-2 { color: var(--fg); }
  .skill-picker-choice.lvl-3 { color: var(--accent); border-color: var(--accent); background: var(--accent-dim); }
```

- [ ] **Step 5: Fix save toast success styling for skill saves**

The current `showSavedToast(null, 'Saved')` treats `"Saved"` as an error. In `saveSkillLevel()`, replace:

```javascript
        showSavedToast(null, 'Saved');
```

with:

```javascript
        showSavedToast(null);
```

- [ ] **Step 6: Run static tests**

Run:

```bash
pytest tests/test_skills_static.py -v
```

Expected: PASS.

- [ ] **Step 7: Run all skills-focused tests**

Run:

```bash
pytest tests/test_skills_static.py tests/test_skills_template_render.py tests/test_skills_cell_update.py tests/test_odoo_client.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add src/zira_dashboard/static/skills-page.js src/zira_dashboard/static/skills.css tests/test_skills_static.py
git commit -m "feat(skills): add live skill picker"
```

---

### Task 6: End-To-End Verification And Polish

**Files:**
- Modify only files already changed by Tasks 1-5 if verification exposes defects.

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: verified People Matrix live editing path with no regressions in existing skill/Odoo tests.

- [ ] **Step 1: Run the focused automated suite**

Run:

```bash
pytest tests/test_odoo_client.py tests/test_odoo_sync.py tests/test_skills_cell_update.py tests/test_skills_template_render.py tests/test_skills_static.py tests/test_skills_cache.py -v
```

Expected: PASS, with DB-backed tests SKIP when `DATABASE_URL` is unset.

- [ ] **Step 2: Run broader route/static smoke tests touched by the matrix**

Run:

```bash
pytest tests/test_staffing_static.py tests/test_staffing_view.py tests/test_work_centers_store_required_skills.py tests/test_object_api_models.py -v
```

Expected: PASS.

- [ ] **Step 3: Inspect changed files for accidental unrelated edits**

Run:

```bash
git diff -- src/zira_dashboard/odoo_client.py src/zira_dashboard/odoo_sync.py src/zira_dashboard/routes/skills.py src/zira_dashboard/templates/skills.html src/zira_dashboard/static/skills-page.js src/zira_dashboard/static/skills.css tests/test_odoo_client.py tests/test_odoo_sync.py tests/test_skills_cell_update.py tests/test_skills_template_render.py tests/test_skills_static.py
```

Expected: diff contains only Odoo skill writes, skill id sync, route update, editable markup, picker JS/CSS, and tests.

- [ ] **Step 4: Manual browser smoke with a configured Odoo environment**

Run the app using the console script defined in `pyproject.toml`:

```bash
AUTH_DISABLED=1 zira-dashboard
```

Expected: server starts. Open `/staffing/skills`, click an editable skill cell, choose a new level, see a saved toast, confirm the badge changes, open the employee in Odoo, confirm the skill level changed, click "Refresh from Odoo", and confirm the edited level remains.

- [ ] **Step 5: Commit verification fixes if any were needed**

If Step 1-4 required code or test changes, commit them:

```bash
git add src/zira_dashboard/odoo_client.py src/zira_dashboard/odoo_sync.py src/zira_dashboard/routes/skills.py src/zira_dashboard/templates/skills.html src/zira_dashboard/static/skills-page.js src/zira_dashboard/static/skills.css tests/test_odoo_client.py tests/test_odoo_sync.py tests/test_skills_cell_update.py tests/test_skills_template_render.py tests/test_skills_static.py
git commit -m "fix(skills): polish live skill editing"
```

If no changes were required, do not create an empty commit.

## Self-Review Notes

- Spec coverage: Tasks 1-2 cover Odoo id sync and write mapping; Task 3 covers Odoo-first route and local mirror; Tasks 4-5 cover picker interaction, accessibility, and read-only fallback; Task 6 covers automated and manual verification.
- Placeholder scan: no `TBD`, `TODO`, "implement later", "similar to", or unspecified validation steps remain.
- Type consistency: the endpoint payload uses `person_odoo_id`, `skill_odoo_id`, and `level`; the Odoo helper uses `set_employee_skill_level(employee_odoo_id: int, skill_odoo_id: int, bucket: int) -> None`; template data attributes map to JS `dataset.personOdooId`, `dataset.skillOdooId`, and `dataset.level`.
