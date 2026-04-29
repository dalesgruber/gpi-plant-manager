# People Matrix Views Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Replace the current single-purpose column filter with a richer **View** model — saved bundles of (hidden columns, person subset, active filter, reserve filter) that any user can pick from, edit in their session, and persist back to Postgres.

**Architecture:** New `skill_matrix_views` table; new `skill_matrix_views_store.py`; CRUD endpoints under `/staffing/skills/views`; "View ▾" popover replaces the old "Columns ▾" with session state in localStorage and explicit Save actions. One-time seed migrates legacy `app_settings['skill_filter']` into a "Default" view.

**Tech Stack:** psycopg2 + raw SQL (pattern matches existing stores). FastAPI sync routes. Jinja + vanilla JS for the popover.

**Dependencies:** Postgres migration spec (already shipped). `db.py`, the store pattern, and the matrix template are all in place.

---

## File Structure

- New: `src/zira_dashboard/skill_matrix_views_store.py`
- Modified: `src/zira_dashboard/db.py` (append `skill_matrix_views` DDL to `_SCHEMA_DDL`)
- Modified: `src/zira_dashboard/routes/skills.py` (GET passes views + initial state; new POST/PUT/DELETE endpoints; remove legacy `/filter`)
- Modified: `src/zira_dashboard/templates/skills.html` (replace "Columns ▾" UI with "View ▾" popover; new JS for session state + save actions)
- Removed: `src/zira_dashboard/skill_filter_store.py`
- New: `tests/test_views_store.py`

---

### Task 1: Schema + store module

**Files:**
- Modify: `src/zira_dashboard/db.py` (append DDL)
- Create: `src/zira_dashboard/skill_matrix_views_store.py`
- Create: `tests/test_views_store.py`

- [ ] **Step 1: Append DDL to `_SCHEMA_DDL` in `db.py`**

After the existing `sync_outbox` table, add:

```sql
CREATE TABLE IF NOT EXISTS skill_matrix_views (
  id              SERIAL PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  is_default      BOOLEAN NOT NULL DEFAULT FALSE,
  hidden_skills   TEXT[]  NOT NULL DEFAULT '{}',
  visible_people  TEXT[],
  active_filter   TEXT NOT NULL DEFAULT 'active'
                  CHECK (active_filter IN ('active','inactive','all')),
  reserve_filter  TEXT NOT NULL DEFAULT 'all'
                  CHECK (reserve_filter IN ('include','exclude','only')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS skill_matrix_views_default_idx
  ON skill_matrix_views (is_default) WHERE is_default = TRUE;
```

- [ ] **Step 2: Write failing tests in `tests/test_views_store.py`**

```python
import os
import pytest

from zira_dashboard import db, skill_matrix_views_store as views


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)


@pytest.fixture(autouse=True)
def _clean():
    db.execute("DELETE FROM skill_matrix_views WHERE name LIKE 'TestView%'")
    yield
    db.execute("DELETE FROM skill_matrix_views WHERE name LIKE 'TestView%'")


def test_create_and_get_view():
    v = views.create_view("TestViewA", {
        "hidden_skills": ["Loading"],
        "visible_people": ["Alice", "Bob"],
        "active_filter": "active",
        "reserve_filter": "exclude",
    })
    assert v["name"] == "TestViewA"
    assert v["hidden_skills"] == ["Loading"]
    assert v["visible_people"] == ["Alice", "Bob"]
    assert v["active_filter"] == "active"
    assert v["reserve_filter"] == "exclude"
    got = views.get_view("TestViewA")
    assert got == v


def test_update_view_overwrites_fields():
    views.create_view("TestViewB", {"hidden_skills": ["Loading"]})
    views.update_view("TestViewB", {
        "hidden_skills": ["Heat Treat"],
        "visible_people": None,
        "active_filter": "all",
        "reserve_filter": "only",
    })
    v = views.get_view("TestViewB")
    assert v["hidden_skills"] == ["Heat Treat"]
    assert v["visible_people"] is None
    assert v["active_filter"] == "all"
    assert v["reserve_filter"] == "only"


def test_set_default_clears_other_defaults():
    views.create_view("TestViewC", {})
    views.create_view("TestViewD", {})
    views.set_default("TestViewC")
    views.set_default("TestViewD")
    c = views.get_view("TestViewC")
    d = views.get_view("TestViewD")
    assert c["is_default"] is False
    assert d["is_default"] is True


def test_set_default_none_clears_default():
    views.create_view("TestViewE", {})
    views.set_default("TestViewE")
    views.set_default(None)
    assert views.get_default_view() is None


def test_delete_view():
    views.create_view("TestViewF", {})
    views.delete_view("TestViewF")
    assert views.get_view("TestViewF") is None


def test_create_drops_invalid_active_filter():
    v = views.create_view("TestViewG", {"active_filter": "garbage"})
    assert v["active_filter"] == "active"  # default coerced


def test_visible_people_empty_list_normalizes_to_none():
    v = views.create_view("TestViewH", {"visible_people": []})
    assert v["visible_people"] is None
```

- [ ] **Step 3: Run tests, verify FAIL**

`.venv/Scripts/python.exe -m pytest tests/test_views_store.py -v` — expected ImportError.

- [ ] **Step 4: Implement `skill_matrix_views_store.py`**

```python
"""CRUD for People Matrix Views — server-side state shared across all
devices and users. See docs/superpowers/specs/2026-04-29-skill-matrix-views-design.md."""

from __future__ import annotations

ACTIVE_FILTERS = ("active", "inactive", "all")
RESERVE_FILTERS = ("include", "exclude", "only")


def _row_to_dict(row: dict) -> dict:
    return {
        "name": row["name"],
        "is_default": row["is_default"],
        "hidden_skills": list(row["hidden_skills"] or []),
        "visible_people": list(row["visible_people"]) if row["visible_people"] is not None else None,
        "active_filter": row["active_filter"],
        "reserve_filter": row["reserve_filter"],
    }


def _coerce(payload: dict) -> dict:
    out: dict = {}
    hs = payload.get("hidden_skills") or []
    out["hidden_skills"] = [str(s) for s in hs if isinstance(s, str)]
    vp = payload.get("visible_people")
    if vp is None:
        out["visible_people"] = None
    else:
        cleaned = [str(s).strip() for s in vp if isinstance(s, str) and str(s).strip()]
        out["visible_people"] = cleaned if cleaned else None
    af = payload.get("active_filter")
    out["active_filter"] = af if af in ACTIVE_FILTERS else "active"
    rf = payload.get("reserve_filter")
    out["reserve_filter"] = rf if rf in RESERVE_FILTERS else "all"
    return out


def list_views() -> list[dict]:
    from . import db
    rows = db.query(
        "SELECT name, is_default, hidden_skills, visible_people, "
        "       active_filter, reserve_filter FROM skill_matrix_views "
        "ORDER BY is_default DESC, lower(name)"
    )
    return [_row_to_dict(r) for r in rows]


def get_view(name: str) -> dict | None:
    from . import db
    rows = db.query(
        "SELECT name, is_default, hidden_skills, visible_people, "
        "       active_filter, reserve_filter FROM skill_matrix_views "
        "WHERE name = %s",
        (name,),
    )
    return _row_to_dict(rows[0]) if rows else None


def get_default_view() -> dict | None:
    from . import db
    rows = db.query(
        "SELECT name, is_default, hidden_skills, visible_people, "
        "       active_filter, reserve_filter FROM skill_matrix_views "
        "WHERE is_default = TRUE LIMIT 1"
    )
    return _row_to_dict(rows[0]) if rows else None


def create_view(name: str, payload: dict) -> dict:
    from . import db
    name = (name or "").strip()[:80]
    if not name:
        raise ValueError("name required")
    fields = _coerce(payload)
    db.execute(
        "INSERT INTO skill_matrix_views (name, hidden_skills, visible_people, "
        "active_filter, reserve_filter) VALUES (%s, %s, %s, %s, %s)",
        (
            name, fields["hidden_skills"], fields["visible_people"],
            fields["active_filter"], fields["reserve_filter"],
        ),
    )
    out = get_view(name)
    assert out is not None
    return out


def update_view(name: str, payload: dict) -> dict:
    from . import db
    fields = _coerce(payload)
    db.execute(
        "UPDATE skill_matrix_views SET hidden_skills = %s, visible_people = %s, "
        "active_filter = %s, reserve_filter = %s, updated_at = now() "
        "WHERE name = %s",
        (
            fields["hidden_skills"], fields["visible_people"],
            fields["active_filter"], fields["reserve_filter"], name,
        ),
    )
    out = get_view(name)
    if out is None:
        raise ValueError(f"view {name!r} not found")
    return out


def delete_view(name: str) -> None:
    from . import db
    db.execute("DELETE FROM skill_matrix_views WHERE name = %s", (name,))


def set_default(name: str | None) -> None:
    """Atomically clear all defaults, then set one (or none if name is None)."""
    from . import db
    with db.cursor() as cur:
        cur.execute("UPDATE skill_matrix_views SET is_default = FALSE WHERE is_default = TRUE")
        if name is not None:
            cur.execute("UPDATE skill_matrix_views SET is_default = TRUE WHERE name = %s", (name,))
```

- [ ] **Step 5: Run, verify PASS**

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/db.py src/zira_dashboard/skill_matrix_views_store.py tests/test_views_store.py
git commit -m "feat(views): skill_matrix_views table + CRUD store"
```

---

### Task 2: Routes — GET passes views + state, plus CRUD endpoints

**Files:**
- Modify: `src/zira_dashboard/routes/skills.py`

- [ ] **Step 1: Update GET to pass views list + default**

In the `staffing_skills` GET handler:

```python
from .. import skill_matrix_views_store as views_store
all_views = views_store.list_views()
default_view = views_store.get_default_view()
```

Add to template context:

```python
"views": all_views,
"default_view_name": default_view["name"] if default_view else None,
"default_view_state": default_view,  # full dict or None
```

Drop the existing `hidden = set(skill_filter_store.load_hidden())` line and the `hidden_skills` context entry — the matrix's initial render no longer pre-applies hidden columns server-side. JS in the template handles applying state from session/default View on page load.

- [ ] **Step 2: Add CRUD endpoints**

```python
@router.post("/staffing/skills/views")
async def staffing_skills_view_create(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    if views_store.get_view(name) is not None:
        return JSONResponse({"ok": False, "error": "name already exists"}, status_code=409)
    view = views_store.create_view(name, body)
    return JSONResponse({"ok": True, "view": view})


@router.put("/staffing/skills/views/{name}")
async def staffing_skills_view_update(name: str, request: Request):
    body = await request.json()
    if views_store.get_view(name) is None:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    view = views_store.update_view(name, body)
    return JSONResponse({"ok": True, "view": view})


@router.delete("/staffing/skills/views/{name}")
def staffing_skills_view_delete(name: str):
    views_store.delete_view(name)
    return JSONResponse({"ok": True})


@router.post("/staffing/skills/views/{name}/default")
def staffing_skills_view_set_default(name: str):
    if views_store.get_view(name) is None:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    views_store.set_default(name)
    return JSONResponse({"ok": True})


@router.delete("/staffing/skills/views/default")
def staffing_skills_view_clear_default():
    views_store.set_default(None)
    return JSONResponse({"ok": True})
```

- [ ] **Step 3: Remove legacy `/filter` endpoint and the `skill_filter_store` import**

Drop the `staffing_skills_filter` route. Drop the `from .. import skill_filter_store` line.

- [ ] **Step 4: Smoke-import**

```bash
.venv/Scripts/python.exe -c "from zira_dashboard.routes import skills; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/skills.py
git commit -m "feat(views): GET passes views; new CRUD endpoints; remove legacy filter route"
```

---

### Task 3: Replace template UI — "View ▾" popover + JS

**Files:**
- Modify: `src/zira_dashboard/templates/skills.html`

This is the bulk of the visible change. Implementation sketch (full code goes in the template):

- Replace `<button class="col-filter-btn" id="col-filter-btn">Columns ▾</button>` and its popover with a new "View ▾" button + popover.
- The popover includes:
  - Dropdown listing all views (from server-passed `views`), with "+ Save new view…" at bottom.
  - Active radio (active/inactive/all)
  - Reserve radio (include/exclude/only)
  - People radio (all / selected) + "Edit selection…" sub-popover with searchable checkbox list of every person in `people`
  - Skill columns grouped by `skill_type` (Production/Supervisor) with per-skill checkboxes (group toggles too — same UX as today's column filter)
  - Action buttons: Save changes, Save as new…, Set as default, Delete view
- JS:
  - `localStorage.getItem('skillMatrixSession')` on page load → if exists, apply; else apply server's `default_view_state`.
  - `applyViewToDOM(state)` — sets CSS classes on `<th>`, `<td>`, `<tr>` to hide/show.
  - On any control change → mark session dirty, re-apply, write to localStorage.
  - On Save changes → PUT the loaded view, clear dirty.
  - On Save as new → prompt for name, POST to create, then "load" the new view.
  - On Set as default → POST to set-default endpoint.
  - On Delete view → confirm, DELETE endpoint, fall back to default view.

- [ ] **Step 1: Pseudocode the JS state machine** (in this task's report — confirms understanding before writing)

- [ ] **Step 2: Implement the popover HTML + CSS + JS**

Full implementation. Use the existing color palette and CSS classes where possible (`.col-filter-popover`, `.col-filter-group`, etc.) so it visually matches the rest of the page.

- [ ] **Step 3: Manual smoke**

Open `/staffing/skills` locally with `DATABASE_URL` set, see the View ▾ popover. Apply a filter, reload, see it persist in the session. Save as new, switch to it, reload — still applies. Set as default, open in incognito, see default applied.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/templates/skills.html
git commit -m "feat(views): View ▾ popover replaces column-only filter"
```

---

### Task 4: Migration + cleanup

**Files:**
- New: `scripts/seed_default_view_from_legacy_filter.py` (one-shot)
- Removed: `src/zira_dashboard/skill_filter_store.py`

- [ ] **Step 1: Write the seed script**

```python
"""One-shot migration: if app_settings['skill_filter'] exists and there
are no views yet, create a "Default" view from it and mark it default."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zira_dashboard import db, skill_matrix_views_store as views


def main() -> int:
    db.init_pool()
    db.bootstrap_schema()
    if views.list_views():
        print("Views already exist; skipping seed.")
        return 0
    rows = db.query("SELECT value FROM app_settings WHERE key = 'skill_filter'")
    hidden: list[str] = []
    if rows:
        v = rows[0]["value"]
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                v = None
        if isinstance(v, dict) and isinstance(v.get("hidden"), list):
            hidden = [str(x) for x in v["hidden"] if isinstance(x, str)]
    views.create_view("Default", {"hidden_skills": hidden})
    views.set_default("Default")
    print(f"Created 'Default' view with {len(hidden)} hidden skills; marked as default.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Remove `skill_filter_store.py`**

```bash
rm src/zira_dashboard/skill_filter_store.py
```

Verify nothing else imports it:

```bash
grep -rn "skill_filter_store" src/ tests/
```

If any imports remain, remove them.

- [ ] **Step 3: Run seed locally against live Postgres**

```powershell
$env:DATABASE_URL = "<paste from Railway>"
python -m scripts.seed_default_view_from_legacy_filter
```

- [ ] **Step 4: Run tests, full suite**

```bash
.venv/Scripts/python.exe -m pytest tests/ --ignore=tests/test_dashboards_polish.py
```

- [ ] **Step 5: Commit**

```bash
git add scripts/seed_default_view_from_legacy_filter.py src/zira_dashboard/skill_filter_store.py
git commit -m "feat(views): seed Default view from legacy filter; drop legacy store"
```

---

### Task 5: Push + verify on Railway

- [ ] **Step 1: Push everything**

```bash
git push origin main
```

- [ ] **Step 2: Wait for redeploy**

```bash
until curl -s -o /dev/null -w "%{http_code}" "https://gpiplantmanager.com/staffing/skills" | grep -q "^200$"; do sleep 8; done
```

- [ ] **Step 3: Manually verify in browser**

- "View ▾" button appears in the toolbar.
- Default view (or current legacy filter) auto-applied.
- Save a new view; switch between views; set default; delete; all behave per spec.
- Filter changes persist across reload (localStorage).
- Open in a different browser → server's default applies (your local localStorage doesn't carry across devices).

---

## Done criteria

- All five tasks committed and pushed.
- New `skill_matrix_views` table populated.
- Legacy filter migrated to a "Default" view.
- "View ▾" popover replaces "Columns ▾".
- Filter changes are session-scoped (localStorage); explicit Save updates Postgres.
- Saved views carry across devices/users.
- Test suite green.
