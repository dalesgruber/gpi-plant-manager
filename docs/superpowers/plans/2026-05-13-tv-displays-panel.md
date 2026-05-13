# TV Displays Settings Panel (Sub-Project 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "TVs" section in Settings that lists every TV Dale has mounted at the plant, lets him toggle each one to light or dark mode, and gives each TV its own short bookmarkable URL (`/tv/d/{slug}`). Also surfaces the existing layout templates with a delete button.

**Architecture:** New `tv_displays` table holds named TV entries. New `tv_displays_store.py` is the data layer. New `routes/tv_displays.py` exposes the CRUD endpoints + the `/tv/d/{slug}` viewing route that dispatches to the existing recycling / new-vs / per-WC render helpers. New `_settings_tvs.html` partial gets included into `settings.html` as a new left-rail section. Seed list of 10 rows (Recycling VS, New VS, Junior 2, Repair 1/2/3, Dismantler 1/2/3/4) inserts on first boot only.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, psycopg2 + Postgres, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-13-tv-displays-panel-design.md`

---

## File Structure

**New files:**
- `src/zira_dashboard/tv_displays_store.py` — data layer (save / list / by_slug / set_theme / delete / seed_defaults_if_empty)
- `src/zira_dashboard/routes/tv_displays.py` — `GET /tv/d/{slug}`, `POST /api/tv-displays`, `POST /api/tv-displays/{id}/theme`, `DELETE /api/tv-displays/{id}`
- `src/zira_dashboard/templates/_settings_tvs.html` — Jinja partial rendering the Displays + Templates tables
- `tests/test_tv_displays_store.py` — Postgres-gated tests for the store
- `tests/test_tv_displays_routes.py` — integration tests for the routes + settings page

**Modified files:**
- `src/zira_dashboard/db.py` — append `tv_displays` `CREATE TABLE` to `_SCHEMA_DDL`
- `src/zira_dashboard/app.py` — register the new router; call `seed_defaults_if_empty()` in `lifespan`
- `src/zira_dashboard/routes/settings.py` — handle `section=tvs`, pass `displays` + `templates` context to the template
- `src/zira_dashboard/templates/settings.html` — add "TVs" left-rail link, include the new partial
- `tests/test_db.py` — assert `tv_displays` table is created
- `CHANGELOG.md` — one deploy entry

**Responsibility split:** `tv_displays_store.py` is the only module that talks to the `tv_dashboard_displays` table. The route module is thin — parse request, call store, call render helpers, return response. The settings partial is presentation-only.

---

## Conventions

- Python interpreter on Dale's Windows box: `.venv/Scripts/python.exe`.
- Postgres-touching tests gate on `DATABASE_URL` via module-level `pytestmark = pytest.mark.skipif(...)`.
- Commit messages: `feat(tv-displays):` / `test(tv-displays):` / `schema(tv-displays):` / `docs:`.
- Slug derivation reuses `wc_dashboard_data.slug_for_wc` — do NOT re-implement.
- Existing render helpers reused (do NOT duplicate or refactor widget logic):
  - `routes.value_streams._render_recycling(request, *, window, start, end, tv_mode, tv_theme)`
  - `routes.value_streams._render_new_vs(request, *, day, tv_mode, tv_theme)`
  - `routes.wc_dashboard._render_wc_dashboard(request, *, slug, tv_mode, tv_theme)`

---

## Task 1: Schema migration

**Files:**
- Modify: `src/zira_dashboard/db.py` — append to `_SCHEMA_DDL`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
def test_bootstrap_creates_tv_displays_table():
    db.init_pool()
    db.bootstrap_schema()
    rows = db.query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'tv_displays'"
    )
    assert len(rows) == 1, "tv_displays table missing"
    cols = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'tv_displays'"
    )
    names = {r["column_name"] for r in cols}
    expected = {"id", "name", "slug", "kind", "wc_name", "theme", "sort_order", "created_at", "updated_at"}
    assert expected.issubset(names), f"missing columns: {expected - names}"
    # Slug must be UNIQUE
    idx_rows = db.query(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = 'tv_displays'"
    )
    assert any("slug" in r["indexdef"] and "UNIQUE" in r["indexdef"].upper() for r in idx_rows), \
        "tv_displays.slug must be UNIQUE"
```

- [ ] **Step 2: Run test to verify it fails (or skips)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py::test_bootstrap_creates_tv_displays_table -v`
Expected: SKIP without `DATABASE_URL`, FAIL with one (table doesn't exist).

- [ ] **Step 3: Append DDL to `_SCHEMA_DDL` in `db.py`**

Open `src/zira_dashboard/db.py`. Find the end of `_SCHEMA_DDL` (the closing `"""` near line 527, just after the `tv_dashboard_templates` block). Append BEFORE the closing `"""`:

```sql
-- TV display registry ---------------------------------------------------
-- Each row is a TV mounted somewhere in the plant. Carries a friendly
-- name, the dashboard it shows (kind + optional wc_name), and the theme
-- (light/dark) for that physical display. The /tv/d/{slug} route looks
-- up the row and dispatches to the underlying dashboard with the row's
-- theme. Seed list of 10 rows inserts on first boot only.
CREATE TABLE IF NOT EXISTS tv_displays (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL,
  slug        TEXT NOT NULL UNIQUE,
  kind        TEXT NOT NULL CHECK (kind IN ('vs_recycling', 'vs_new', 'wc')),
  wc_name     TEXT,
  theme       TEXT NOT NULL DEFAULT 'dark' CHECK (theme IN ('light', 'dark')),
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py -v`
Expected: PASS with DATABASE_URL, SKIP without.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/db.py tests/test_db.py
git commit -m "schema(tv-displays): tv_displays table"
```

---

## Task 2: `tv_displays_store.py` — data layer

**Files:**
- Create: `src/zira_dashboard/tv_displays_store.py`
- Test: `tests/test_tv_displays_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tv_displays_store.py`:

```python
"""Postgres-gated tests for tv_displays_store.

Each test cleans 'st-' prefix rows so it doesn't collide with the seed
list or any real displays Dale has saved.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="tv_displays_store tests need Postgres",
)


@pytest.fixture(autouse=True)
def _clean_displays():
    from zira_dashboard import db
    db.init_pool()
    db.bootstrap_schema()
    db.execute("DELETE FROM tv_displays WHERE slug LIKE 'st-%'")
    yield
    db.execute("DELETE FROM tv_displays WHERE slug LIKE 'st-%'")


def test_save_inserts_and_returns_slug():
    from zira_dashboard import tv_displays_store
    row = tv_displays_store.save(name="st-repair-1", kind="wc", wc_name="Repair 1", theme="dark")
    assert row["slug"] == "st-repair-1"
    assert row["theme"] == "dark"
    assert row["kind"] == "wc"
    assert row["wc_name"] == "Repair 1"
    assert isinstance(row["id"], int)


def test_save_collision_suffixes_slug():
    """Two saves with the same name produce -2 suffix."""
    from zira_dashboard import tv_displays_store
    a = tv_displays_store.save(name="st-clash", kind="wc", wc_name="Repair 1", theme="dark")
    b = tv_displays_store.save(name="st-clash", kind="wc", wc_name="Repair 2", theme="dark")
    assert a["slug"] == "st-clash"
    assert b["slug"] == "st-clash-2"
    c = tv_displays_store.save(name="st-clash", kind="wc", wc_name="Repair 3", theme="dark")
    assert c["slug"] == "st-clash-3"


def test_save_with_id_updates_existing():
    """save(..., id=existing) updates the row; name unchanged keeps slug."""
    from zira_dashboard import tv_displays_store
    row = tv_displays_store.save(name="st-edit", kind="wc", wc_name="Repair 1", theme="dark")
    updated = tv_displays_store.save(
        name="st-edit", kind="wc", wc_name="Repair 2", theme="light", id=row["id"],
    )
    assert updated["id"] == row["id"]
    assert updated["slug"] == "st-edit"
    assert updated["wc_name"] == "Repair 2"
    assert updated["theme"] == "light"


def test_save_rename_regenerates_slug_without_collision_on_self():
    """Renaming a row keeps its slug if the new name slug equals the old.
    A real rename to a fresh name generates a fresh slug."""
    from zira_dashboard import tv_displays_store
    row = tv_displays_store.save(name="st-renamable", kind="wc", wc_name="Repair 1", theme="dark")
    # Save again with same name should not bump the suffix.
    again = tv_displays_store.save(
        name="st-renamable", kind="wc", wc_name="Repair 1", theme="dark", id=row["id"],
    )
    assert again["slug"] == "st-renamable"
    # Now rename to a different name.
    renamed = tv_displays_store.save(
        name="st-was-renamed", kind="wc", wc_name="Repair 1", theme="dark", id=row["id"],
    )
    assert renamed["slug"] == "st-was-renamed"


def test_set_theme_updates_only_theme():
    from zira_dashboard import tv_displays_store
    row = tv_displays_store.save(name="st-theme", kind="wc", wc_name="Repair 1", theme="dark")
    tv_displays_store.set_theme(row["id"], "light")
    fetched = tv_displays_store.by_slug("st-theme")
    assert fetched["theme"] == "light"
    assert fetched["wc_name"] == "Repair 1"  # unchanged


def test_delete_removes_row():
    from zira_dashboard import tv_displays_store
    row = tv_displays_store.save(name="st-deleteme", kind="wc", wc_name="Repair 1", theme="dark")
    tv_displays_store.delete(row["id"])
    assert tv_displays_store.by_slug("st-deleteme") is None


def test_by_slug_returns_none_for_missing():
    from zira_dashboard import tv_displays_store
    assert tv_displays_store.by_slug("st-not-there") is None


def test_list_displays_returns_all_rows():
    from zira_dashboard import tv_displays_store
    tv_displays_store.save(name="st-a", kind="wc", wc_name="Repair 1", theme="dark")
    tv_displays_store.save(name="st-b", kind="vs_recycling", wc_name=None, theme="light")
    rows = tv_displays_store.list_displays()
    slugs = [r["slug"] for r in rows]
    assert "st-a" in slugs
    assert "st-b" in slugs


def test_seed_defaults_if_empty_seeds_when_empty(monkeypatch):
    """Seed runs on empty tv_displays; uses staffing.LOCATIONS for wc_name validation."""
    from zira_dashboard import tv_displays_store, staffing, db

    class _Loc:
        def __init__(self, name): self.name = name

    monkeypatch.setattr(staffing, "LOCATIONS", [
        _Loc("Junior 2"), _Loc("Repair 1"), _Loc("Repair 2"), _Loc("Repair 3"),
        _Loc("Dismantler 1"), _Loc("Dismantler 2"), _Loc("Dismantler 3"), _Loc("Dismantler 4"),
    ])
    # Force the table to be empty (after autouse cleanup, real rows may exist;
    # clear them all for this single test).
    db.execute("DELETE FROM tv_displays")
    tv_displays_store.seed_defaults_if_empty()
    rows = tv_displays_store.list_displays()
    names = [r["name"] for r in rows]
    assert "Recycling VS" in names
    assert "New VS" in names
    assert "Repair 1" in names
    assert "Dismantler 4" in names
    assert len(rows) == 10
    # Re-running is idempotent (table no longer empty).
    tv_displays_store.seed_defaults_if_empty()
    assert len(tv_displays_store.list_displays()) == 10


def test_seed_defaults_skips_missing_wc(monkeypatch, caplog):
    """If a seeded WC name isn't in staffing.LOCATIONS, log a warning + skip."""
    from zira_dashboard import tv_displays_store, staffing, db
    import logging

    class _Loc:
        def __init__(self, name): self.name = name

    # Only Repair 1 exists; the rest get skipped.
    monkeypatch.setattr(staffing, "LOCATIONS", [_Loc("Repair 1")])
    db.execute("DELETE FROM tv_displays")
    with caplog.at_level(logging.WARNING):
        tv_displays_store.seed_defaults_if_empty()
    rows = tv_displays_store.list_displays()
    names = [r["name"] for r in rows]
    # The two VS rows + Repair 1 should be present.
    assert "Recycling VS" in names
    assert "New VS" in names
    assert "Repair 1" in names
    # Missing WCs were skipped.
    assert "Junior 2" not in names
    assert "Dismantler 1" not in names
    assert len(rows) == 3
```

- [ ] **Step 2: Run tests to verify they fail/skip**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tv_displays_store.py -v`
Expected: SKIP without `DATABASE_URL`; FAIL with one (module doesn't exist).

- [ ] **Step 3: Create the store module**

Create `src/zira_dashboard/tv_displays_store.py`:

```python
"""Persistence layer for TV display registry.

Each row is a physical TV in the plant: a friendly name, which dashboard
it shows (kind = vs_recycling / vs_new / wc, plus wc_name when kind=wc),
and a light/dark theme. The /tv/d/{slug} route looks up the row and
dispatches to the appropriate render helper with the row's theme.

Seed list of 10 rows inserts on first boot only — once the table has
any rows, seeding is a no-op. Deleting a seeded row stays deleted across
redeploys.
"""
from __future__ import annotations

import logging
from typing import Optional

from .wc_dashboard_data import slug_for_wc

_log = logging.getLogger(__name__)


# (name, kind, wc_name) — order matters for sort_order assignment at seed.
_SEED_LIST = [
    ("Recycling VS", "vs_recycling", None),
    ("New VS",       "vs_new",        None),
    ("Junior 2",     "wc",            "Junior 2"),
    ("Repair 1",     "wc",            "Repair 1"),
    ("Repair 2",     "wc",            "Repair 2"),
    ("Repair 3",     "wc",            "Repair 3"),
    ("Dismantler 1", "wc",            "Dismantler 1"),
    ("Dismantler 2", "wc",            "Dismantler 2"),
    ("Dismantler 3", "wc",            "Dismantler 3"),
    ("Dismantler 4", "wc",            "Dismantler 4"),
]


def _unique_slug(base: str, *, exclude_id: Optional[int] = None) -> str:
    """Return `base` if no other row owns it; else suffix -2, -3, ...

    `exclude_id` lets a row keep its own slug when saving with no name change.
    """
    from . import db
    candidate = base
    n = 2
    while True:
        rows = db.query(
            "SELECT id FROM tv_displays WHERE slug = %s",
            (candidate,),
        )
        if not rows or (exclude_id is not None and all(r["id"] == exclude_id for r in rows)):
            return candidate
        candidate = f"{base}-{n}"
        n += 1


def save(
    *,
    name: str,
    kind: str,
    wc_name: Optional[str],
    theme: str,
    id: Optional[int] = None,
) -> dict:
    """Insert a new row or update an existing one (when `id` given).

    Slug is derived from `name` via `slug_for_wc`; on collision the
    store appends `-2`, `-3`, etc. (skipping the row's own slug when
    updating). Returns the saved row as a dict.
    """
    from . import db
    slug_base = slug_for_wc(name)
    if not slug_base:
        raise ValueError("name must produce a non-empty slug")
    if theme not in ("light", "dark"):
        theme = "dark"
    if kind not in ("vs_recycling", "vs_new", "wc"):
        raise ValueError(f"invalid kind: {kind}")
    slug = _unique_slug(slug_base, exclude_id=id)
    if id is None:
        rows = db.query(
            "INSERT INTO tv_displays (name, slug, kind, wc_name, theme) "
            "VALUES (%s, %s, %s, %s, %s) "
            "RETURNING id, name, slug, kind, wc_name, theme, sort_order",
            (name, slug, kind, wc_name, theme),
        )
    else:
        rows = db.query(
            "UPDATE tv_displays SET "
            "  name = %s, slug = %s, kind = %s, wc_name = %s, theme = %s, "
            "  updated_at = now() "
            "WHERE id = %s "
            "RETURNING id, name, slug, kind, wc_name, theme, sort_order",
            (name, slug, kind, wc_name, theme, id),
        )
    if not rows:
        raise LookupError(f"no tv_displays row with id={id}")
    r = rows[0]
    return {
        "id": int(r["id"]),
        "name": r["name"],
        "slug": r["slug"],
        "kind": r["kind"],
        "wc_name": r["wc_name"],
        "theme": r["theme"],
        "sort_order": int(r["sort_order"]),
    }


def set_theme(id: int, theme: str) -> None:
    """Update only the theme column. No slug re-derivation."""
    from . import db
    if theme not in ("light", "dark"):
        raise ValueError(f"invalid theme: {theme}")
    db.execute(
        "UPDATE tv_displays SET theme = %s, updated_at = now() WHERE id = %s",
        (theme, id),
    )


def delete(id: int) -> None:
    from . import db
    db.execute("DELETE FROM tv_displays WHERE id = %s", (id,))


def by_slug(slug: str) -> Optional[dict]:
    from . import db
    rows = db.query(
        "SELECT id, name, slug, kind, wc_name, theme, sort_order "
        "FROM tv_displays WHERE slug = %s",
        (slug,),
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "id": int(r["id"]),
        "name": r["name"],
        "slug": r["slug"],
        "kind": r["kind"],
        "wc_name": r["wc_name"],
        "theme": r["theme"],
        "sort_order": int(r["sort_order"]),
    }


def list_displays() -> list[dict]:
    """All rows ordered by (sort_order ASC, name ASC). Stable for UI."""
    from . import db
    rows = db.query(
        "SELECT id, name, slug, kind, wc_name, theme, sort_order "
        "FROM tv_displays ORDER BY sort_order ASC, lower(name) ASC"
    )
    return [
        {
            "id": int(r["id"]),
            "name": r["name"],
            "slug": r["slug"],
            "kind": r["kind"],
            "wc_name": r["wc_name"],
            "theme": r["theme"],
            "sort_order": int(r["sort_order"]),
        }
        for r in rows
    ]


def seed_defaults_if_empty() -> None:
    """Insert the 10-row seed list if `tv_displays` is empty.

    Rows whose `wc_name` is not present in `staffing.LOCATIONS` are
    skipped with a warning log so a partial WC roster doesn't fail
    boot. Once any row exists, this is a no-op — deleting a seeded
    row keeps it deleted across redeploys.
    """
    from . import db, staffing
    existing = db.query("SELECT 1 FROM tv_displays LIMIT 1")
    if existing:
        return
    valid_wc_names = {loc.name for loc in staffing.LOCATIONS}
    inserted = 0
    for idx, (name, kind, wc_name) in enumerate(_SEED_LIST):
        if kind == "wc" and wc_name not in valid_wc_names:
            _log.warning(
                "tv_displays seed skipping %s — not in staffing.LOCATIONS", name
            )
            continue
        slug = _unique_slug(slug_for_wc(name))
        db.execute(
            "INSERT INTO tv_displays (name, slug, kind, wc_name, theme, sort_order) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (name, slug, kind, wc_name, "dark", idx),
        )
        inserted += 1
    _log.info("tv_displays seeded %d default rows", inserted)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tv_displays_store.py -v`
Expected: 10 PASS with `DATABASE_URL`, 10 SKIP without.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/tv_displays_store.py tests/test_tv_displays_store.py
git commit -m "feat(tv-displays): tv_displays_store — save / list / by_slug / set_theme / delete / seed"
```

---

## Task 3: Routes — `/tv/d/{slug}` + CRUD API

**Files:**
- Create: `src/zira_dashboard/routes/tv_displays.py`
- Modify: `src/zira_dashboard/app.py` — register the router + call seed on boot
- Test: `tests/test_tv_displays_routes.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tv_displays_routes.py`:

```python
"""Integration tests for the tv-displays routes."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from zira_dashboard.app import app

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="tv-displays route tests need Postgres",
)


@pytest.fixture(autouse=True)
def _clean_displays():
    from zira_dashboard import db
    db.init_pool()
    db.bootstrap_schema()
    db.execute("DELETE FROM tv_displays WHERE slug LIKE 'rt-%'")
    yield
    db.execute("DELETE FROM tv_displays WHERE slug LIKE 'rt-%'")


def test_post_add_display_returns_url():
    c = TestClient(app)
    r = c.post("/api/tv-displays", json={
        "name": "rt-recycling-tv",
        "kind": "vs_recycling",
        "theme": "dark",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["slug"] == "rt-recycling-tv"
    assert body["url"] == "/tv/d/rt-recycling-tv"
    assert isinstance(body["id"], int)


def test_post_add_rejects_missing_name():
    c = TestClient(app)
    r = c.post("/api/tv-displays", json={"kind": "vs_recycling", "theme": "dark"})
    assert r.status_code == 400


def test_post_add_rejects_bad_kind():
    c = TestClient(app)
    r = c.post("/api/tv-displays", json={"name": "rt-bad", "kind": "garbage", "theme": "dark"})
    assert r.status_code == 400


def test_post_add_wc_requires_wc_name():
    c = TestClient(app)
    r = c.post("/api/tv-displays", json={"name": "rt-wcnone", "kind": "wc", "theme": "dark"})
    assert r.status_code == 400


def test_post_theme_toggle():
    c = TestClient(app)
    add = c.post("/api/tv-displays", json={
        "name": "rt-theme-toggle",
        "kind": "vs_recycling",
        "theme": "dark",
    }).json()
    r = c.post(f"/api/tv-displays/{add['id']}/theme", json={"theme": "light"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_display():
    c = TestClient(app)
    add = c.post("/api/tv-displays", json={
        "name": "rt-deleteme",
        "kind": "vs_recycling",
        "theme": "dark",
    }).json()
    r = c.delete(f"/api/tv-displays/{add['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_get_tv_d_unknown_slug_returns_404():
    c = TestClient(app)
    r = c.get("/tv/d/rt-not-a-real-display")
    assert r.status_code == 404
    assert "settings" in r.text.lower() or "tvs" in r.text.lower()


def test_get_tv_d_vs_recycling_dispatches():
    """/tv/d/{slug} for a vs_recycling display should render the recycling
    TV view — same content as /tv/recycling but with the row's theme."""
    c = TestClient(app)
    c.post("/api/tv-displays", json={
        "name": "rt-recyc-light",
        "kind": "vs_recycling",
        "theme": "light",
    })
    r = c.get("/tv/d/rt-recyc-light")
    assert r.status_code == 200
    # Theme should be baked into the HTML root attribute.
    assert 'data-tv-theme="light"' in r.text


def test_get_tv_d_with_query_theme_overrides_stored():
    c = TestClient(app)
    c.post("/api/tv-displays", json={
        "name": "rt-recyc-dark",
        "kind": "vs_recycling",
        "theme": "dark",
    })
    r = c.get("/tv/d/rt-recyc-dark?theme=light")
    assert r.status_code == 200
    assert 'data-tv-theme="light"' in r.text


def test_get_tv_d_wc_archived_returns_404(monkeypatch):
    """If the display's wc_name isn't in LOCATIONS, the route should 404
    with a clear message."""
    from zira_dashboard import staffing

    class _Loc:
        def __init__(self, name): self.name = name

    c = TestClient(app)
    # Save with a wc_name that we then remove from staffing.LOCATIONS.
    c.post("/api/tv-displays", json={
        "name": "rt-ghost-wc",
        "kind": "wc",
        "wc_name": "Repair 1",
        "theme": "dark",
    })
    monkeypatch.setattr(staffing, "LOCATIONS", [_Loc("Junior 2")])  # Repair 1 removed
    r = c.get("/tv/d/rt-ghost-wc")
    assert r.status_code == 404
    assert "work center" in r.text.lower() or "removed" in r.text.lower()
```

- [ ] **Step 2: Run tests to verify they fail/skip**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tv_displays_routes.py -v`
Expected: SKIP without `DATABASE_URL`; FAIL with one (routes missing).

- [ ] **Step 3: Create the route module**

Create `src/zira_dashboard/routes/tv_displays.py`:

```python
"""HTTP routes for the TV display registry.

  GET    /tv/d/{slug}                       resolve display -> dispatch
  POST   /api/tv-displays                   add/update
  POST   /api/tv-displays/{id}/theme        theme toggle
  DELETE /api/tv-displays/{id}              delete
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import tv_displays_store
from ..wc_dashboard_data import slug_for_wc

router = APIRouter()


@router.get("/tv/d/{slug}", response_class=HTMLResponse)
def tv_display(request: Request, slug: str, theme: str | None = Query(default=None)):
    row = tv_displays_store.by_slug(slug)
    if row is None:
        return HTMLResponse(
            _not_configured_html(slug),
            status_code=404,
        )
    tv_theme = "light" if theme == "light" else ("dark" if theme == "dark" else row["theme"])
    kind = row["kind"]
    if kind == "vs_recycling":
        from .value_streams import _render_recycling
        return _render_recycling(
            request, window="today", start=None, end=None,
            tv_mode=True, tv_theme=tv_theme,
        )
    if kind == "vs_new":
        from .value_streams import _render_new_vs
        return _render_new_vs(
            request, day=None, tv_mode=True, tv_theme=tv_theme,
        )
    if kind == "wc":
        from .. import staffing
        wc_name = row["wc_name"]
        valid = any(loc.name == wc_name for loc in staffing.LOCATIONS)
        if not valid:
            return HTMLResponse(
                _wc_removed_html(row["name"], wc_name),
                status_code=404,
            )
        from .wc_dashboard import _render_wc_dashboard
        return _render_wc_dashboard(
            request, slug=slug_for_wc(wc_name), tv_mode=True, tv_theme=tv_theme,
        )
    return JSONResponse(
        {"error": f"unknown kind: {kind}"}, status_code=500,
    )


def _not_configured_html(slug: str) -> str:
    return (
        f"<!doctype html><html><head><title>Display not configured</title>"
        f"<style>body{{font-family:system-ui;padding:3rem;text-align:center}}"
        f"a{{color:#16a34a}}</style></head><body>"
        f"<h1>Display \"{slug}\" isn't configured</h1>"
        f"<p>Add it on the <a href=\"/settings?section=tvs\">TVs settings page</a>.</p>"
        f"</body></html>"
    )


def _wc_removed_html(display_name: str, wc_name: str | None) -> str:
    return (
        f"<!doctype html><html><head><title>Work center removed</title>"
        f"<style>body{{font-family:system-ui;padding:3rem;text-align:center}}"
        f"a{{color:#16a34a}}</style></head><body>"
        f"<h1>Work center removed</h1>"
        f"<p>The display \"{display_name}\" was pointing at \"{wc_name}\", which is no longer in Settings.</p>"
        f"<p><a href=\"/settings?section=tvs\">Go to TVs settings</a></p>"
        f"</body></html>"
    )


@router.post("/api/tv-displays")
async def post_display(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    body = body or {}
    name = body.get("name")
    kind = body.get("kind")
    wc_name = body.get("wc_name") or None
    theme = body.get("theme") or "dark"
    row_id = body.get("id")
    if not isinstance(name, str) or not name.strip():
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    if kind not in ("vs_recycling", "vs_new", "wc"):
        return JSONResponse({"ok": False, "error": "kind invalid"}, status_code=400)
    if kind == "wc":
        from .. import staffing
        if not isinstance(wc_name, str) or not wc_name.strip():
            return JSONResponse({"ok": False, "error": "wc_name required when kind=wc"}, status_code=400)
        if not any(loc.name == wc_name for loc in staffing.LOCATIONS):
            return JSONResponse({"ok": False, "error": f"unknown work center: {wc_name}"}, status_code=400)
    else:
        wc_name = None
    if theme not in ("light", "dark"):
        return JSONResponse({"ok": False, "error": "theme must be light or dark"}, status_code=400)
    saved = tv_displays_store.save(
        name=name.strip(), kind=kind, wc_name=wc_name, theme=theme,
        id=int(row_id) if row_id is not None else None,
    )
    return JSONResponse({
        "ok": True,
        "id": saved["id"],
        "slug": saved["slug"],
        "url": f"/tv/d/{saved['slug']}",
    })


@router.post("/api/tv-displays/{display_id}/theme")
async def post_theme(display_id: int, request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    theme = (body or {}).get("theme")
    if theme not in ("light", "dark"):
        return JSONResponse({"ok": False, "error": "theme must be light or dark"}, status_code=400)
    tv_displays_store.set_theme(display_id, theme)
    return JSONResponse({"ok": True})


@router.delete("/api/tv-displays/{display_id}")
def delete_display(display_id: int):
    tv_displays_store.delete(display_id)
    return JSONResponse({"ok": True})
```

- [ ] **Step 4: Register the router and seed-on-boot in `app.py`**

Open `src/zira_dashboard/app.py`. In the `from .routes import (...)` block, add `tv_displays` to the alphabetical list (between `tv_templates` and `value_streams`):

```python
from .routes import (
    admin,
    api_layout,
    changelog,
    dashboard,
    late_report,
    leaderboards,
    past_schedules,
    people,
    settings,
    share,
    skills,
    staffing,
    time_off,
    trophies,
    tv_displays,
    tv_templates,
    value_streams,
    wc_dashboard,
)
```

Add the include line near the other `app.include_router` calls — right after `app.include_router(tv_templates.router)`:

```python
app.include_router(tv_templates.router)
app.include_router(tv_displays.router)
```

Then inside `lifespan` (after `db.bootstrap_schema()`), add the seed call:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_pool()
    db.bootstrap_schema()
    from . import tv_displays_store
    tv_displays_store.seed_defaults_if_empty()
    _prewarm_stratustime()
    ...
```

(Keep all the existing warmer-task setup unchanged.)

- [ ] **Step 5: Run tests + smoke check**

```bash
.venv/Scripts/python.exe -m pytest tests/test_tv_displays_routes.py -v
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; routes = sorted({r.path for r in app.routes if hasattr(r, 'path')}); [print(p) for p in routes if 'tv-displays' in p or '/tv/d/' in p]"
```

Expected pytest output: 10 PASS with DATABASE_URL, 10 SKIP without.
Expected smoke output: `/api/tv-displays`, `/api/tv-displays/{display_id}/theme`, `/api/tv-displays/{display_id}`, `/tv/d/{slug}`.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/tv_displays.py src/zira_dashboard/app.py tests/test_tv_displays_routes.py
git commit -m "feat(tv-displays): routes — /tv/d/{slug} + add/theme/delete API"
```

---

## Task 4: Settings UI — "TVs" sub-section

**Files:**
- Create: `src/zira_dashboard/templates/_settings_tvs.html`
- Modify: `src/zira_dashboard/routes/settings.py` — handle `section=tvs`
- Modify: `src/zira_dashboard/templates/settings.html` — add left-rail link + include partial

- [ ] **Step 1: Update `routes/settings.py` to accept `section=tvs` and pass context**

Open `src/zira_dashboard/routes/settings.py`. In `settings_page`, locate:

```python
    if section not in ("work_centers", "schedule", "integrations", "roster_filter"):
        section = "work_centers"
```

Change to:

```python
    if section not in ("work_centers", "schedule", "integrations", "roster_filter", "tvs"):
        section = "work_centers"
```

Then, just after `integration_status = stratustime_client.health_check()` block (still inside `settings_page`, before the `from .. import odoo_sync` line), add:

```python
    tv_displays_rows: list[dict] = []
    tv_templates_rows: list[dict] = []
    if section == "tvs":
        from .. import tv_displays_store, tv_templates_store
        tv_displays_rows = tv_displays_store.list_displays()
        tv_templates_rows = tv_templates_store.list_templates()
```

And in the `return templates.TemplateResponse(...)` context dict (the big dict at the bottom), add:

```python
            "tv_displays_rows": tv_displays_rows,
            "tv_templates_rows": tv_templates_rows,
            "wc_locations_for_picker": [{"name": loc.name} for loc in staffing.LOCATIONS],
```

- [ ] **Step 2: Create the partial template**

Create `src/zira_dashboard/templates/_settings_tvs.html`:

```jinja
{# TVs settings section. Included from settings.html when active_section == 'tvs'. #}
<section class="panel" data-section="tvs"
         {% if active_section != 'tvs' %}style="display:none"{% endif %}>
  <h2>TVs</h2>
  <p class="note">
    Each TV in the plant. The URL is bookmarkable on the TV browser — toggle theme
    here and the TV picks up the change on its next refresh. Renaming a row regenerates
    the slug, so the old URL stops working.
  </p>

  <table class="tv-displays-table">
    <thead>
      <tr>
        <th>Name</th>
        <th>Target</th>
        <th>Theme</th>
        <th>URL</th>
        <th></th>
      </tr>
    </thead>
    <tbody id="tv-displays-body">
      {% for d in tv_displays_rows %}
        <tr data-id="{{ d.id }}" data-slug="{{ d.slug }}">
          <td><input type="text" class="tv-name-input" value="{{ d.name }}"></td>
          <td>
            <select class="tv-kind-select">
              <option value="vs_recycling" {% if d.kind == 'vs_recycling' %}selected{% endif %}>Recycling VS</option>
              <option value="vs_new" {% if d.kind == 'vs_new' %}selected{% endif %}>New VS</option>
              <option value="wc" {% if d.kind == 'wc' %}selected{% endif %}>Work Center</option>
            </select>
            <select class="tv-wc-select" {% if d.kind != 'wc' %}style="display:none"{% endif %}>
              {% for loc in wc_locations_for_picker %}
                <option value="{{ loc.name }}" {% if d.wc_name == loc.name %}selected{% endif %}>{{ loc.name }}</option>
              {% endfor %}
            </select>
          </td>
          <td class="tv-theme-cell">
            <button type="button" class="tv-theme-btn {% if d.theme == 'dark' %}active{% endif %}"
                    data-theme="dark">Dark</button>
            <button type="button" class="tv-theme-btn {% if d.theme == 'light' %}active{% endif %}"
                    data-theme="light">Light</button>
          </td>
          <td class="tv-url-cell">
            <span class="tv-url">/tv/d/{{ d.slug }}</span>
            <button type="button" class="tv-copy-btn">Copy</button>
          </td>
          <td>
            <button type="button" class="tv-delete-btn" title="Delete">×</button>
          </td>
        </tr>
      {% else %}
        <tr class="empty"><td colspan="5">No TVs yet — add one below.</td></tr>
      {% endfor %}
    </tbody>
  </table>

  <div class="tv-add-form">
    <input type="text" id="tv-add-name" placeholder="Display name (e.g. Repair 1 — Wall TV)">
    <select id="tv-add-kind">
      <option value="vs_recycling">Recycling VS</option>
      <option value="vs_new">New VS</option>
      <option value="wc" selected>Work Center</option>
    </select>
    <select id="tv-add-wc">
      {% for loc in wc_locations_for_picker %}
        <option value="{{ loc.name }}">{{ loc.name }}</option>
      {% endfor %}
    </select>
    <select id="tv-add-theme">
      <option value="dark" selected>Dark</option>
      <option value="light">Light</option>
    </select>
    <button type="button" id="tv-add-btn">Add display</button>
    <span class="tv-add-status" id="tv-add-status"></span>
  </div>

  <h2 style="margin-top: 1.5rem">Layout Templates</h2>
  <p class="note">
    Templates saved from any <code>/wc/{slug}</code> editor view. Delete to clean up;
    templates can be re-saved any time from the editor.
  </p>

  <table class="tv-templates-table">
    <thead>
      <tr>
        <th>Name</th>
        <th>Theme</th>
        <th>Updated</th>
        <th></th>
      </tr>
    </thead>
    <tbody id="tv-templates-body">
      {% for t in tv_templates_rows %}
        <tr data-id="{{ t.id }}">
          <td>{{ t.name }}</td>
          <td>{{ t.theme }}</td>
          <td title="{{ t.updated_at.isoformat() if t.updated_at else '' }}">
            {{ t.updated_at.strftime('%Y-%m-%d %H:%M') if t.updated_at else '' }}
          </td>
          <td><button type="button" class="tv-template-delete" title="Delete">×</button></td>
        </tr>
      {% else %}
        <tr class="empty"><td colspan="4">No templates saved yet — visit any /wc/{slug} editor to save a layout.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</section>

<style>
  .tv-displays-table, .tv-templates-table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
  .tv-displays-table th, .tv-displays-table td,
  .tv-templates-table th, .tv-templates-table td {
    padding: 0.4rem 0.55rem; border-bottom: 1px solid var(--border);
    text-align: left; font-size: 0.88rem; vertical-align: middle;
  }
  .tv-displays-table th, .tv-templates-table th {
    font-size: 0.68rem; color: var(--muted); font-weight: 500;
    text-transform: uppercase; letter-spacing: 0.5px;
  }
  .tv-displays-table .tv-name-input {
    background: var(--panel-2); color: var(--fg); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.25rem 0.5rem; font: inherit; font-size: 0.85rem;
    width: 14rem;
  }
  .tv-displays-table select {
    background: var(--panel-2); color: var(--fg); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.25rem 0.45rem; font: inherit; font-size: 0.85rem;
  }
  .tv-theme-btn {
    background: var(--panel-2); color: var(--muted); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.2rem 0.65rem; cursor: pointer; font-size: 0.8rem;
  }
  .tv-theme-btn.active { background: var(--accent-dim); color: var(--accent); border-color: var(--accent); font-weight: 600; }
  .tv-url { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.8rem; color: var(--muted); }
  .tv-copy-btn, .tv-delete-btn, .tv-template-delete {
    background: transparent; border: 1px solid var(--border); border-radius: 6px;
    padding: 0.15rem 0.55rem; cursor: pointer; color: var(--muted); font-size: 0.8rem;
  }
  .tv-delete-btn, .tv-template-delete { color: #ef4444; }
  .tv-add-form {
    display: flex; gap: 0.5rem; align-items: center; margin-top: 0.75rem;
    padding: 0.6rem; background: var(--panel-2); border-radius: 8px;
  }
  .tv-add-form input, .tv-add-form select {
    background: var(--panel); color: var(--fg); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.3rem 0.5rem; font: inherit; font-size: 0.85rem;
  }
  .tv-add-form input { flex: 1; }
  #tv-add-btn {
    background: var(--accent-dim); color: var(--accent); border: 1px solid var(--accent);
    border-radius: 6px; padding: 0.3rem 0.9rem; font-weight: 700; font-size: 0.85rem;
    cursor: pointer;
  }
  .tv-add-status { color: var(--muted); font-size: 0.78rem; min-width: 10rem; }
</style>

<script>
(function() {
  function showStatus(elId, text) {
    const el = document.getElementById(elId);
    if (el) el.textContent = text;
  }

  function postJson(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    }).then(r => r.json());
  }

  // --- Inline-row edits (name change, kind change, wc_name change) ---
  function saveRow(tr) {
    const id = tr.dataset.id;
    const name = tr.querySelector('.tv-name-input').value.trim();
    const kind = tr.querySelector('.tv-kind-select').value;
    const wcSel = tr.querySelector('.tv-wc-select');
    const wc_name = kind === 'wc' ? wcSel.value : null;
    const themeBtn = tr.querySelector('.tv-theme-btn.active');
    const theme = themeBtn ? themeBtn.dataset.theme : 'dark';
    return postJson('/api/tv-displays', {
      id: parseInt(id, 10), name, kind, wc_name, theme,
    }).then(data => {
      if (data.ok) {
        tr.dataset.slug = data.slug;
        const urlEl = tr.querySelector('.tv-url');
        if (urlEl) urlEl.textContent = '/tv/d/' + data.slug;
      }
      return data;
    });
  }

  document.querySelectorAll('#tv-displays-body tr[data-id]').forEach(tr => {
    tr.querySelector('.tv-name-input').addEventListener('blur', () => saveRow(tr));
    tr.querySelector('.tv-kind-select').addEventListener('change', (e) => {
      const wcSel = tr.querySelector('.tv-wc-select');
      wcSel.style.display = e.target.value === 'wc' ? '' : 'none';
      saveRow(tr);
    });
    tr.querySelector('.tv-wc-select').addEventListener('change', () => saveRow(tr));

    // Theme toggle
    tr.querySelectorAll('.tv-theme-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        tr.querySelectorAll('.tv-theme-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        postJson('/api/tv-displays/' + tr.dataset.id + '/theme', {theme: btn.dataset.theme});
      });
    });

    // Copy URL
    tr.querySelector('.tv-copy-btn').addEventListener('click', () => {
      const slug = tr.dataset.slug;
      const url = window.location.origin + '/tv/d/' + slug;
      navigator.clipboard.writeText(url).then(() => {
        const btn = tr.querySelector('.tv-copy-btn');
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => btn.textContent = orig, 1200);
      });
    });

    // Delete
    tr.querySelector('.tv-delete-btn').addEventListener('click', () => {
      if (!confirm('Delete "' + tr.querySelector('.tv-name-input').value + '"?')) return;
      fetch('/api/tv-displays/' + tr.dataset.id, {method: 'DELETE'})
        .then(r => r.json()).then(data => {
          if (data.ok) tr.remove();
        });
    });
  });

  // --- Add new display ---
  const addKindSel = document.getElementById('tv-add-kind');
  const addWcSel = document.getElementById('tv-add-wc');
  addKindSel.addEventListener('change', (e) => {
    addWcSel.style.display = e.target.value === 'wc' ? '' : 'none';
  });
  document.getElementById('tv-add-btn').addEventListener('click', () => {
    const name = document.getElementById('tv-add-name').value.trim();
    const kind = addKindSel.value;
    const wc_name = kind === 'wc' ? addWcSel.value : null;
    const theme = document.getElementById('tv-add-theme').value;
    if (!name) { showStatus('tv-add-status', 'name required'); return; }
    showStatus('tv-add-status', 'Adding…');
    postJson('/api/tv-displays', {name, kind, wc_name, theme}).then(data => {
      if (data.ok) {
        showStatus('tv-add-status', 'Added — reloading…');
        setTimeout(() => location.reload(), 500);
      } else {
        showStatus('tv-add-status', 'Error: ' + (data.error || 'unknown'));
      }
    });
  });

  // --- Template delete ---
  document.querySelectorAll('.tv-template-delete').forEach(btn => {
    btn.addEventListener('click', () => {
      const tr = btn.closest('tr');
      if (!confirm('Delete this template?')) return;
      fetch('/api/tv-templates/' + tr.dataset.id, {method: 'DELETE'})
        .then(r => r.json()).then(data => {
          if (data.ok) tr.remove();
        });
    });
  });
})();
</script>
```

- [ ] **Step 3: Add the left-rail link + partial include in `settings.html`**

Open `src/zira_dashboard/templates/settings.html`. Find the left-rail nav block (around lines 501-518). Add a new link after the "Integrations" link so the order is Work Centers → Roster Filter → Schedule → Integrations → **TVs**:

Replace:

```jinja
    <a href="?section=integrations"
       class="settings-nav-item {% if active_section == 'integrations' %}active{% endif %}">
      Integrations
    </a>
  </aside>
```

With:

```jinja
    <a href="?section=integrations"
       class="settings-nav-item {% if active_section == 'integrations' %}active{% endif %}">
      Integrations
    </a>
    <a href="?section=tvs"
       class="settings-nav-item {% if active_section == 'tvs' %}active{% endif %}">
      TVs
    </a>
  </aside>
```

Then find the closing of the existing `<section class="panel" data-section="integrations">` block (the integrations section is the last `<form>` / `<section>` before `</div>` of `.settings-content`). Just after it ends and before the `</div>` that closes `.settings-content`, add:

```jinja
  {% include "_settings_tvs.html" %}
```

- [ ] **Step 4: Verify the template still parses and app boots**

Run:
```bash
.venv/Scripts/python.exe -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'), autoescape=True); env.parse(open('src/zira_dashboard/templates/settings.html', encoding='utf-8').read()); env.parse(open('src/zira_dashboard/templates/_settings_tvs.html', encoding='utf-8').read()); print('parse OK')"
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; print('OK')"
```
Expected: `parse OK` then `OK`.

- [ ] **Step 5: Smoke test the settings page**

Without a DB this just checks the route returns 200 for the default section. With DB it should also render the TVs section.

```bash
.venv/Scripts/python.exe -c "
from fastapi.testclient import TestClient
from zira_dashboard.app import app
c = TestClient(app)
r = c.get('/settings?section=tvs')
print('status:', r.status_code)
assert 'TVs' in r.text or 'tvs' in r.text.lower(), 'TVs heading missing'
print('TVs section renders OK')
"
```

(This will fail if `DATABASE_URL` isn't set since `/settings` reads roster data; skip the smoke test if so and rely on the deploy preview.)

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/templates/_settings_tvs.html src/zira_dashboard/templates/settings.html src/zira_dashboard/routes/settings.py
git commit -m "feat(tv-displays): Settings TVs section — list, add, theme toggle, copy URL"
```

---

## Task 5: CHANGELOG + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full test suite**

Run: `.venv/Scripts/python.exe -m pytest 2>&1 | tail -3`
Expected: pass count grows by the new tests; no new failures.

- [ ] **Step 2: Get the current time**

Run: `powershell.exe -Command "Get-Date -Format 'h:mm tt'"`

- [ ] **Step 3: Add CHANGELOG entry**

In `CHANGELOG.md`, insert a new `### <HH:MM TT>` block at the top of today's `## 2026-05-13` section:

```markdown
### <HH:MM TT>

- **Settings → TVs section** — central registry for every TV in the plant. Each row is a friendly name + which dashboard it shows + a light/dark toggle + a bookmarkable URL (`/tv/d/{slug}`). The first deploy seeds 10 default rows (Recycling VS, New VS, Junior 2, Repair 1/2/3, Dismantler 1/2/3/4) all in dark mode — toggle any to light and the TV picks up the change on its next 60 s refresh. The seed only runs on an empty table, so deleting a seeded row stays deleted across redeploys. Renaming a row regenerates the slug; a small note under the table warns that old URLs will break on rename. Also adds a Layout Templates table below Displays with a delete button for cleanup of templates saved via the WC editor. The existing `/tv/recycling`, `/tv/new-vs`, and `/tv/wc/{wc_slug}` URLs continue to work as default-dark fallbacks — no bookmarks shipped earlier today break. This is the final sub-project (4 of 4) in the TV dashboards spec.
```

- [ ] **Step 4: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): Settings TVs section + theme persistence"
git push origin main
```

Railway picks up the push and redeploys. After deploy, the first hit to `/settings?section=tvs` shows the 10 seeded rows. Toggle one to light, visit its `/tv/d/{slug}` URL — should render in light mode. Hit `/tv/d/{slug}?theme=dark` — overrides to dark for that one view without changing the stored theme.

---

## Done

The TV display registry ships. Dale has a single source of truth for every TV in the plant, can toggle each one to light or dark independently, and can clean up old layout templates from the same page. Existing `/tv/...` bookmarks keep working as default-dark fallbacks; the new `/tv/d/{slug}` URLs respect the stored theme.

If Dale wants a follow-on (drag-to-reorder, editable slug, bulk theme toggle, template rename / clone), each is small — `sort_order` and the underlying schema are already in place.
