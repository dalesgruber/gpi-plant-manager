# Dashboards Sub-Nav + Pinning + Unified Index + TVs Flatten Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface every dashboard (built-in VS, built-in per-WC, user-built custom) under one "Dashboards" top tab with a pinnable sub-nav, redesign `/dashboards` to list everything with pin toggles, drop the top-nav and Settings sidebar links to `/widgets` and `/dashboards` (now reachable via sub-nav), and flatten the TVs settings cascading kind/wc/custom picker into one dashboard select.

**Architecture:** New `pinned_dashboards` table records favorites. New `pinned_dashboards_store` does CRUD. New `dashboard_catalog` helper enumerates every renderable dashboard with `pinned` flag (single source for index, sub-nav, TVs picker). New `_dashboards_subnav.html` partial renders the sub-tab strip and is included by every dashboard-family screen page. `/dashboards` becomes two sections: Built-in + Custom, with ★/☆ pin toggles. The TVs picker becomes one `<select>` listing everything.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, psycopg2 + Postgres, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-13-dashboards-subnav-pin-design.md`

---

## File Structure

**New files:**
- `src/zira_dashboard/pinned_dashboards_store.py` — CRUD on `pinned_dashboards`
- `src/zira_dashboard/dashboard_catalog.py` — enumerate all dashboards + pinned subset
- `src/zira_dashboard/templates/_dashboards_subnav.html` — sub-nav partial
- `src/zira_dashboard/static/dashboards-subnav.css` — sub-nav styling
- `tests/test_pinned_dashboards_store.py` — store tests
- `tests/test_dashboard_catalog.py` — catalog unit tests

**Modified files:**
- `src/zira_dashboard/db.py` — append `pinned_dashboards` to `_SCHEMA_DDL`
- `src/zira_dashboard/app.py` — call `pinned_dashboards_store.seed_defaults_if_empty()` in `lifespan`
- `src/zira_dashboard/routes/custom_dashboards.py` — new `POST /api/pinned-dashboards` + extend `dashboards_index` context for the redesign
- `src/zira_dashboard/routes/widgets.py` — include sub-nav context
- `src/zira_dashboard/routes/value_streams.py` — include sub-nav context in `_render_recycling` + `_render_new_vs`
- `src/zira_dashboard/routes/wc_dashboard.py` — include sub-nav context in `_render_wc_dashboard`
- `src/zira_dashboard/routes/settings.py` — pass `all_dashboards` to template for TVs picker
- `src/zira_dashboard/templates/recycling.html`, `new_vs.html`, `wc_dashboard.html`, `custom_dashboard.html`, `widgets.html` — include sub-nav partial
- `src/zira_dashboard/templates/dashboards.html` — full redesign (two sections + pin toggle)
- `src/zira_dashboard/templates/_settings_tvs.html` — flat dashboard picker
- `src/zira_dashboard/templates/index.html`, `recycling.html`, `new_vs.html`, `_staffing_base.html`, `settings.html` — drop top-nav "My Dashboards" link
- `src/zira_dashboard/templates/settings.html` — drop "Widget Workshop" + "My Dashboards" sidebar entries
- `tests/test_db.py` — assert `pinned_dashboards` table created
- `tests/test_custom_dashboards_routes.py` — pin/unpin route tests
- `CHANGELOG.md` — one deploy entry

---

## Conventions

- Python interpreter: `.venv/Scripts/python.exe`.
- Postgres tests gate on `DATABASE_URL` via module-level `pytestmark`.
- Slug derivation reuses `wc_dashboard_data.slug_for_wc`.
- Commit messages: `feat(dash-subnav):` / `schema(dash-subnav):` / `docs:`.

---

## Task 1: Schema migration — `pinned_dashboards` table

**Files:**
- Modify: `src/zira_dashboard/db.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Append failing test to `tests/test_db.py`**

```python
def test_bootstrap_creates_pinned_dashboards_table():
    db.init_pool()
    db.bootstrap_schema()
    rows = db.query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'pinned_dashboards'"
    )
    assert len(rows) == 1, "pinned_dashboards table missing"
    cols = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'pinned_dashboards'"
    )
    names = {r["column_name"] for r in cols}
    expected = {"id", "kind", "ref", "sort_order", "created_at"}
    assert expected.issubset(names), f"missing columns: {expected - names}"
    # UNIQUE(kind, ref) exists
    idx_rows = db.query(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = 'pinned_dashboards'"
    )
    found = any(
        "UNIQUE" in r["indexdef"].upper() and "kind" in r["indexdef"] and "ref" in r["indexdef"]
        for r in idx_rows
    )
    assert found, "UNIQUE (kind, ref) constraint missing"
```

- [ ] **Step 2: Confirm fail/skip**

`.venv/Scripts/python.exe -m pytest tests/test_db.py -v 2>&1 | tail -10`
Expected: SKIP without `DATABASE_URL`.

- [ ] **Step 3: Append DDL to `_SCHEMA_DDL` in `db.py`**

Find the closing `"""` of `_SCHEMA_DDL` and insert BEFORE it:

```sql
-- pinned_dashboards: which dashboards (built-in VS / per-WC / custom)
-- the user has favorited for the sub-nav. ref is '' for vs_*, WC name
-- for wc, slug for custom.
CREATE TABLE IF NOT EXISTS pinned_dashboards (
  id          SERIAL PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('vs_recycling', 'vs_new', 'wc', 'custom')),
  ref         TEXT NOT NULL,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (kind, ref)
);
```

- [ ] **Step 4: Verify**

```
.venv/Scripts/python.exe -m pytest tests/test_db.py -v 2>&1 | tail -10
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; print('OK')"
```

Expected: tests SKIP without DB; app boots.

- [ ] **Step 5: Commit**

```
git add src/zira_dashboard/db.py tests/test_db.py
git commit -m "$(cat <<'EOF'
schema(dash-subnav): pinned_dashboards table

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `pinned_dashboards_store.py`

**Files:**
- Create: `src/zira_dashboard/pinned_dashboards_store.py`
- Create: `tests/test_pinned_dashboards_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_pinned_dashboards_store.py`:

```python
"""Postgres-gated tests for pinned_dashboards_store."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="pinned_dashboards_store tests need Postgres",
)


@pytest.fixture(autouse=True)
def _clean():
    from zira_dashboard import db
    db.init_pool()
    db.bootstrap_schema()
    db.execute("DELETE FROM pinned_dashboards WHERE ref LIKE 'pdt-%' OR (kind = 'wc' AND ref LIKE 'pdt-%')")
    yield
    db.execute("DELETE FROM pinned_dashboards WHERE ref LIKE 'pdt-%' OR (kind = 'wc' AND ref LIKE 'pdt-%')")


def test_pin_inserts_row():
    from zira_dashboard import pinned_dashboards_store
    pinned_dashboards_store.pin("wc", "pdt-test-wc")
    assert pinned_dashboards_store.is_pinned("wc", "pdt-test-wc") is True


def test_pin_is_idempotent():
    from zira_dashboard import pinned_dashboards_store, db
    pinned_dashboards_store.pin("custom", "pdt-test-dash")
    pinned_dashboards_store.pin("custom", "pdt-test-dash")
    rows = db.query(
        "SELECT COUNT(*) AS n FROM pinned_dashboards WHERE kind = 'custom' AND ref = 'pdt-test-dash'"
    )
    assert int(rows[0]["n"]) == 1


def test_unpin_removes_row():
    from zira_dashboard import pinned_dashboards_store
    pinned_dashboards_store.pin("wc", "pdt-rm-wc")
    pinned_dashboards_store.unpin("wc", "pdt-rm-wc")
    assert pinned_dashboards_store.is_pinned("wc", "pdt-rm-wc") is False


def test_unpin_missing_is_noop():
    from zira_dashboard import pinned_dashboards_store
    pinned_dashboards_store.unpin("wc", "pdt-never-pinned")  # no raise


def test_list_pins_ordered_by_sort_then_created():
    from zira_dashboard import pinned_dashboards_store
    pinned_dashboards_store.pin("wc", "pdt-a")
    pinned_dashboards_store.pin("wc", "pdt-b")
    pinned_dashboards_store.pin("wc", "pdt-c")
    pins = [p for p in pinned_dashboards_store.list_pins() if p["ref"].startswith("pdt-")]
    refs = [p["ref"] for p in pins]
    # All three present; insertion order preserved (sort_order auto-incremented)
    assert refs == ["pdt-a", "pdt-b", "pdt-c"]


def test_seed_defaults_if_empty_seeds_two_vs_pins(monkeypatch):
    from zira_dashboard import pinned_dashboards_store, db
    db.execute("DELETE FROM pinned_dashboards")
    pinned_dashboards_store.seed_defaults_if_empty()
    pins = pinned_dashboards_store.list_pins()
    kinds = [p["kind"] for p in pins]
    assert "vs_recycling" in kinds
    assert "vs_new" in kinds
    assert len(pins) == 2
    # Re-running is a no-op.
    pinned_dashboards_store.seed_defaults_if_empty()
    assert len(pinned_dashboards_store.list_pins()) == 2
```

- [ ] **Step 2: Confirm fail/skip**

`.venv/Scripts/python.exe -m pytest tests/test_pinned_dashboards_store.py -v 2>&1 | tail -10`
Expected: SKIP without DATABASE_URL.

- [ ] **Step 3: Create the store module**

Create `src/zira_dashboard/pinned_dashboards_store.py`:

```python
"""Persistence layer for pinned_dashboards.

Tracks which dashboards (built-in VS, built-in per-WC, custom) the user
has favorited for the Dashboards sub-nav. `kind` + `ref` together
identify a dashboard:
  - kind='vs_recycling', ref=''   (Recycling VS)
  - kind='vs_new',       ref=''   (New VS)
  - kind='wc',           ref=<WC name>
  - kind='custom',       ref=<custom_dashboards.slug>

Seed on first boot pins the two VS dashboards. Deleted seeds stay
deleted across redeploys (same pattern as tv_displays_store).
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def pin(kind: str, ref: str) -> None:
    """Insert a pin. Idempotent — duplicate inserts no-op via ON CONFLICT."""
    from . import db
    if kind not in ("vs_recycling", "vs_new", "wc", "custom"):
        raise ValueError(f"invalid kind: {kind}")
    if not isinstance(ref, str):
        raise ValueError("ref must be a string")
    # New pins get max(sort_order) + 1 so they appear at the end.
    db.execute(
        "INSERT INTO pinned_dashboards (kind, ref, sort_order) "
        "VALUES (%s, %s, "
        "  COALESCE((SELECT MAX(sort_order) + 1 FROM pinned_dashboards), 0)"
        ") "
        "ON CONFLICT (kind, ref) DO NOTHING",
        (kind, ref),
    )


def unpin(kind: str, ref: str) -> None:
    from . import db
    db.execute(
        "DELETE FROM pinned_dashboards WHERE kind = %s AND ref = %s",
        (kind, ref),
    )


def is_pinned(kind: str, ref: str) -> bool:
    from . import db
    rows = db.query(
        "SELECT 1 FROM pinned_dashboards WHERE kind = %s AND ref = %s",
        (kind, ref),
    )
    return bool(rows)


def list_pins() -> list[dict]:
    """All pins ordered by (sort_order ASC, created_at ASC)."""
    from . import db
    rows = db.query(
        "SELECT kind, ref, sort_order "
        "FROM pinned_dashboards "
        "ORDER BY sort_order ASC, created_at ASC"
    )
    return [
        {"kind": r["kind"], "ref": r["ref"], "sort_order": int(r["sort_order"])}
        for r in rows
    ]


def seed_defaults_if_empty() -> None:
    """Pin Recycling VS + New VS on first boot. No-op on a non-empty table.

    Deleted seeds stay deleted across redeploys.
    """
    from . import db
    existing = db.query("SELECT 1 FROM pinned_dashboards LIMIT 1")
    if existing:
        return
    db.execute(
        "INSERT INTO pinned_dashboards (kind, ref, sort_order) VALUES "
        "  ('vs_recycling', '', 0), "
        "  ('vs_new', '', 1)"
    )
    _log.info("pinned_dashboards seeded 2 default pins (Recycling VS + New VS)")
```

- [ ] **Step 4: Verify**

```
.venv/Scripts/python.exe -m pytest tests/test_pinned_dashboards_store.py -v 2>&1 | tail -10
.venv/Scripts/python.exe -c "from zira_dashboard import pinned_dashboards_store; print('OK')"
```

Expected: tests SKIP without DB; module imports cleanly.

- [ ] **Step 5: Commit**

```
git add src/zira_dashboard/pinned_dashboards_store.py tests/test_pinned_dashboards_store.py
git commit -m "$(cat <<'EOF'
feat(dash-subnav): pinned_dashboards_store — pin / unpin / list / seed

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `dashboard_catalog.py` helper + boot seed call

**Files:**
- Create: `src/zira_dashboard/dashboard_catalog.py`
- Create: `tests/test_dashboard_catalog.py`
- Modify: `src/zira_dashboard/app.py` — call `pinned_dashboards_store.seed_defaults_if_empty()` in `lifespan`

- [ ] **Step 1: Write failing tests for the catalog**

Create `tests/test_dashboard_catalog.py`:

```python
"""Unit tests for dashboard_catalog — uses monkeypatch, no Postgres."""
from __future__ import annotations


def test_all_dashboards_lists_vs_then_wcs_then_custom(monkeypatch):
    from zira_dashboard import dashboard_catalog, pinned_dashboards_store, staffing, custom_dashboards_store

    class _Loc:
        def __init__(self, name): self.name = name

    monkeypatch.setattr(staffing, "LOCATIONS", [_Loc("Repair 1"), _Loc("Junior 2")])
    monkeypatch.setattr(custom_dashboards_store, "list_dashboards", lambda: [
        {"id": 7, "name": "Floor Hub", "slug": "floor-hub",
         "scope_kind": "wc", "scope_value": "Repair 1", "theme": "dark",
         "sort_order": 0, "widget_count": 3},
    ])
    monkeypatch.setattr(pinned_dashboards_store, "list_pins", lambda: [
        {"kind": "vs_recycling", "ref": "", "sort_order": 0},
        {"kind": "custom", "ref": "floor-hub", "sort_order": 1},
    ])

    out = dashboard_catalog.all_dashboards()
    kinds = [d["kind"] for d in out]
    # vs_recycling, vs_new, then 2 WCs, then 1 custom.
    assert kinds == ["vs_recycling", "vs_new", "wc", "wc", "custom"]
    # Pinned flag is correct.
    by_key = {(d["kind"], d["ref"]): d for d in out}
    assert by_key[("vs_recycling", "")]["pinned"] is True
    assert by_key[("vs_new", "")]["pinned"] is False
    assert by_key[("wc", "Repair 1")]["pinned"] is False
    assert by_key[("custom", "floor-hub")]["pinned"] is True


def test_all_dashboards_urls_are_correct(monkeypatch):
    from zira_dashboard import dashboard_catalog, pinned_dashboards_store, staffing, custom_dashboards_store

    class _Loc:
        def __init__(self, name): self.name = name

    monkeypatch.setattr(staffing, "LOCATIONS", [_Loc("Repair 1")])
    monkeypatch.setattr(custom_dashboards_store, "list_dashboards", lambda: [
        {"id": 3, "name": "X", "slug": "x", "scope_kind": "wc",
         "scope_value": "Repair 1", "theme": "dark", "sort_order": 0, "widget_count": 0},
    ])
    monkeypatch.setattr(pinned_dashboards_store, "list_pins", lambda: [])

    out = dashboard_catalog.all_dashboards()
    urls = {(d["kind"], d["ref"]): (d["open_url"], d["tv_url"]) for d in out}
    assert urls[("vs_recycling", "")] == ("/recycling", "/tv/recycling")
    assert urls[("vs_new", "")] == ("/new-vs", "/tv/new-vs")
    assert urls[("wc", "Repair 1")] == ("/wc/repair-1", "/tv/wc/repair-1")
    assert urls[("custom", "x")] == ("/dashboards/x", "/tv/dashboards/x")


def test_pinned_dashboards_for_subnav_filters_unpinned(monkeypatch):
    from zira_dashboard import dashboard_catalog, pinned_dashboards_store, staffing, custom_dashboards_store

    class _Loc:
        def __init__(self, name): self.name = name

    monkeypatch.setattr(staffing, "LOCATIONS", [_Loc("Repair 1")])
    monkeypatch.setattr(custom_dashboards_store, "list_dashboards", lambda: [])
    monkeypatch.setattr(pinned_dashboards_store, "list_pins", lambda: [
        {"kind": "vs_recycling", "ref": "", "sort_order": 0},
        {"kind": "wc", "ref": "Repair 1", "sort_order": 1},
    ])

    out = dashboard_catalog.pinned_dashboards_for_subnav()
    keys = [d["key"] for d in out]
    assert keys == ["vs_recycling:", "wc:Repair 1"]
    names = [d["name"] for d in out]
    assert names == ["Recycling VS", "Repair 1"]


def test_pinned_subnav_filters_orphaned_pins(monkeypatch):
    """A pin pointing at a deleted custom dashboard or removed WC is skipped."""
    from zira_dashboard import dashboard_catalog, pinned_dashboards_store, staffing, custom_dashboards_store

    monkeypatch.setattr(staffing, "LOCATIONS", [])  # WC "Repair 1" no longer exists
    monkeypatch.setattr(custom_dashboards_store, "list_dashboards", lambda: [])  # no custom dashboards
    monkeypatch.setattr(pinned_dashboards_store, "list_pins", lambda: [
        {"kind": "vs_recycling", "ref": "", "sort_order": 0},
        {"kind": "wc", "ref": "Repair 1", "sort_order": 1},
        {"kind": "custom", "ref": "deleted-slug", "sort_order": 2},
    ])

    out = dashboard_catalog.pinned_dashboards_for_subnav()
    keys = [d["key"] for d in out]
    # Only vs_recycling survives; the WC and custom pins are dropped.
    assert keys == ["vs_recycling:"]
```

- [ ] **Step 2: Confirm fail**

`.venv/Scripts/python.exe -m pytest tests/test_dashboard_catalog.py -v 2>&1 | tail -10`
Expected: ImportError until module exists.

- [ ] **Step 3: Create the catalog module**

Create `src/zira_dashboard/dashboard_catalog.py`:

```python
"""Enumerates every renderable dashboard.

Single source of truth for the unified /dashboards index, the dashboards
sub-nav partial, and the TVs settings flat picker.

Order is stable: vs_recycling, vs_new, then WCs in staffing.LOCATIONS
order, then custom dashboards in custom_dashboards_store.list_dashboards()
order.
"""
from __future__ import annotations

from .wc_dashboard_data import slug_for_wc


def all_dashboards() -> list[dict]:
    """Returns every renderable dashboard:
      [{kind, ref, name, open_url, tv_url, pinned, ...}, ...]

    Custom-kind entries also carry `id` (so the TVs picker can store
    custom_dashboard_id).
    """
    from . import pinned_dashboards_store, staffing, custom_dashboards_store

    pinned_set = {(p["kind"], p["ref"]) for p in pinned_dashboards_store.list_pins()}
    out: list[dict] = []

    # Built-in VS
    out.append({
        "kind": "vs_recycling", "ref": "",
        "name": "Recycling VS",
        "open_url": "/recycling", "tv_url": "/tv/recycling",
        "pinned": ("vs_recycling", "") in pinned_set,
    })
    out.append({
        "kind": "vs_new", "ref": "",
        "name": "New VS",
        "open_url": "/new-vs", "tv_url": "/tv/new-vs",
        "pinned": ("vs_new", "") in pinned_set,
    })

    # Built-in per-WC
    for loc in staffing.LOCATIONS:
        slug = slug_for_wc(loc.name)
        out.append({
            "kind": "wc", "ref": loc.name,
            "name": loc.name,
            "open_url": f"/wc/{slug}", "tv_url": f"/tv/wc/{slug}",
            "pinned": ("wc", loc.name) in pinned_set,
        })

    # Custom
    for d in custom_dashboards_store.list_dashboards():
        out.append({
            "kind": "custom", "ref": d["slug"],
            "id": d["id"],
            "name": d["name"],
            "open_url": f"/dashboards/{d['slug']}",
            "tv_url": f"/tv/dashboards/{d['slug']}",
            "pinned": ("custom", d["slug"]) in pinned_set,
            "scope_kind": d.get("scope_kind"),
            "scope_value": d.get("scope_value"),
            "widget_count": d.get("widget_count", 0),
        })

    return out


def pinned_dashboards_for_subnav() -> list[dict]:
    """The pinned subset of all_dashboards(), in pin order, with a `key`
    field for the templates to mark the active tab.

    Pins referencing a removed WC or deleted custom dashboard are
    silently dropped — the underlying row stays in pinned_dashboards
    (we don't side-effect-prune at read time) but doesn't render.
    """
    from . import pinned_dashboards_store
    catalog = {(d["kind"], d["ref"]): d for d in all_dashboards()}
    out: list[dict] = []
    for pin in pinned_dashboards_store.list_pins():
        key = (pin["kind"], pin["ref"])
        item = catalog.get(key)
        if item is None:
            continue
        out.append({
            "kind": pin["kind"],
            "ref": pin["ref"],
            "name": item["name"],
            "open_url": item["open_url"],
            "key": f"{pin['kind']}:{pin['ref']}",
        })
    return out
```

- [ ] **Step 4: Run tests**

```
.venv/Scripts/python.exe -m pytest tests/test_dashboard_catalog.py -v 2>&1 | tail -10
.venv/Scripts/python.exe -c "from zira_dashboard import dashboard_catalog; print('OK')"
```

Expected: all PASS. Module imports.

- [ ] **Step 5: Add boot seed call in `app.py`**

Open `src/zira_dashboard/app.py`. Find:

```python
    from . import tv_displays_store, widget_definitions_store
    tv_displays_store.seed_defaults_if_empty()
    widget_definitions_store.seed_defaults_if_empty()
    _prewarm_stratustime()
```

Replace with:

```python
    from . import tv_displays_store, widget_definitions_store, pinned_dashboards_store
    tv_displays_store.seed_defaults_if_empty()
    widget_definitions_store.seed_defaults_if_empty()
    pinned_dashboards_store.seed_defaults_if_empty()
    _prewarm_stratustime()
```

- [ ] **Step 6: Verify app boots + tests still pass**

```
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; print('OK')"
.venv/Scripts/python.exe -m pytest 2>&1 | tail -3
```

Expected: `OK`. Full suite green.

- [ ] **Step 7: Commit**

```
git add src/zira_dashboard/dashboard_catalog.py tests/test_dashboard_catalog.py src/zira_dashboard/app.py
git commit -m "$(cat <<'EOF'
feat(dash-subnav): dashboard_catalog helper + boot seed call

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Pin/unpin route

**Files:**
- Modify: `src/zira_dashboard/routes/custom_dashboards.py`
- Modify: `tests/test_custom_dashboards_routes.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_custom_dashboards_routes.py`:

```python
def test_post_pin_vs_recycling():
    from zira_dashboard import pinned_dashboards_store, db
    c = TestClient(app)
    # Reset state
    db.execute("DELETE FROM pinned_dashboards WHERE kind = 'vs_recycling' AND ref = ''")
    r = c.post("/api/pinned-dashboards", json={
        "kind": "vs_recycling", "ref": "", "pinned": True,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert pinned_dashboards_store.is_pinned("vs_recycling", "") is True


def test_post_unpin():
    from zira_dashboard import pinned_dashboards_store
    c = TestClient(app)
    pinned_dashboards_store.pin("vs_new", "")
    r = c.post("/api/pinned-dashboards", json={
        "kind": "vs_new", "ref": "", "pinned": False,
    })
    assert r.status_code == 200
    assert pinned_dashboards_store.is_pinned("vs_new", "") is False


def test_post_pin_invalid_kind():
    c = TestClient(app)
    r = c.post("/api/pinned-dashboards", json={
        "kind": "garbage", "ref": "x", "pinned": True,
    })
    assert r.status_code == 400


def test_post_pin_wc_invalid_ref():
    """Pinning a WC that isn't in staffing.LOCATIONS returns 400."""
    c = TestClient(app)
    r = c.post("/api/pinned-dashboards", json={
        "kind": "wc", "ref": "NOT-A-REAL-WC-XYZ", "pinned": True,
    })
    assert r.status_code == 400


def test_post_pin_custom_invalid_ref():
    """Pinning a custom dashboard whose slug doesn't exist returns 400."""
    c = TestClient(app)
    r = c.post("/api/pinned-dashboards", json={
        "kind": "custom", "ref": "nonexistent-slug-xyz", "pinned": True,
    })
    assert r.status_code == 400
```

- [ ] **Step 2: Confirm fail/skip**

`.venv/Scripts/python.exe -m pytest tests/test_custom_dashboards_routes.py -v 2>&1 | tail -15`
Expected: SKIP without DB.

- [ ] **Step 3: Add the route**

Open `src/zira_dashboard/routes/custom_dashboards.py`. Append at the end of the file:

```python
@router.post("/api/pinned-dashboards")
async def post_pinned_dashboard(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    body = body or {}
    kind = body.get("kind")
    ref = body.get("ref")
    pinned = body.get("pinned")
    if kind not in ("vs_recycling", "vs_new", "wc", "custom"):
        return JSONResponse({"ok": False, "error": "invalid kind"}, status_code=400)
    if not isinstance(ref, str):
        return JSONResponse({"ok": False, "error": "ref must be string"}, status_code=400)
    if not isinstance(pinned, bool):
        return JSONResponse({"ok": False, "error": "pinned must be bool"}, status_code=400)
    if kind == "wc":
        from .. import staffing
        if not any(loc.name == ref for loc in staffing.LOCATIONS):
            return JSONResponse(
                {"ok": False, "error": f"unknown work center: {ref}"},
                status_code=400,
            )
    if kind == "custom":
        if custom_dashboards_store.get_dashboard(ref) is None:
            return JSONResponse(
                {"ok": False, "error": f"unknown custom dashboard slug: {ref}"},
                status_code=400,
            )
    from .. import pinned_dashboards_store
    if pinned:
        pinned_dashboards_store.pin(kind, ref)
    else:
        pinned_dashboards_store.unpin(kind, ref)
    return JSONResponse({"ok": True, "pinned": pinned})
```

- [ ] **Step 4: Verify**

```
.venv/Scripts/python.exe -m pytest tests/test_custom_dashboards_routes.py -v 2>&1 | tail -20
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; routes = sorted({r.path for r in app.routes if hasattr(r, 'path')}); [print(p) for p in routes if 'pinned' in p]"
```

Expected: tests SKIP without DB. Route `/api/pinned-dashboards` listed.

- [ ] **Step 5: Commit**

```
git add src/zira_dashboard/routes/custom_dashboards.py tests/test_custom_dashboards_routes.py
git commit -m "$(cat <<'EOF'
feat(dash-subnav): POST /api/pinned-dashboards — pin / unpin

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Sub-nav partial + CSS

**Files:**
- Create: `src/zira_dashboard/templates/_dashboards_subnav.html`
- Create: `src/zira_dashboard/static/dashboards-subnav.css`

- [ ] **Step 1: Create the partial**

Create `src/zira_dashboard/templates/_dashboards_subnav.html`:

```jinja
{# Dashboards sub-nav strip.

   Context required:
     pinned_dashboards    — list of {kind, ref, name, open_url, key} from
                            dashboard_catalog.pinned_dashboards_for_subnav()
     active_dashboard_key — '{kind}:{ref}' for the current page, or
                            'meta:dashboards' / 'meta:widgets' for the
                            management pages, or None.

   Pinned dashboards appear left-aligned in pin order; "My Dashboards"
   and "Workshop" are anchored on the right.
#}
<nav class="dash-subnav">
  <div class="pinned-tabs">
    {% for p in pinned_dashboards %}
      <a href="{{ p.open_url }}"
         class="subnav-item {% if active_dashboard_key == p.key %}active{% endif %}">
        {{ p.name }}
      </a>
    {% endfor %}
  </div>
  <div class="meta-tabs">
    <a href="/dashboards"
       class="subnav-item {% if active_dashboard_key == 'meta:dashboards' %}active{% endif %}">
      My Dashboards
    </a>
    <a href="/widgets"
       class="subnav-item {% if active_dashboard_key == 'meta:widgets' %}active{% endif %}">
      Workshop
    </a>
  </div>
</nav>
```

- [ ] **Step 2: Create the CSS**

Create `src/zira_dashboard/static/dashboards-subnav.css`:

```css
.dash-subnav {
  display: flex;
  gap: 0.5rem;
  padding: 0.4rem 1rem;
  border-bottom: 1px solid var(--border, #d8dee5);
  background: var(--panel, #fff);
  align-items: center;
}
.dash-subnav .pinned-tabs {
  flex: 1 1 auto;
  display: flex;
  gap: 0.4rem;
  overflow-x: auto;
  min-width: 0;
}
.dash-subnav .meta-tabs {
  flex: 0 0 auto;
  display: flex;
  gap: 0.4rem;
  margin-left: 0.5rem;
}
.dash-subnav .subnav-item {
  color: var(--muted, #6b7280);
  text-decoration: none;
  padding: 0.3rem 0.7rem;
  border-radius: 6px;
  font-size: 0.88rem;
  white-space: nowrap;
}
.dash-subnav .subnav-item.active {
  color: var(--accent, #16a34a);
  background: var(--accent-dim, #dcfce7);
  font-weight: 600;
}
.dash-subnav .subnav-item:hover:not(.active) {
  color: var(--fg, #1f2937);
}
```

- [ ] **Step 3: Verify parse**

```
.venv/Scripts/python.exe -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'), autoescape=True); env.parse(open('src/zira_dashboard/templates/_dashboards_subnav.html', encoding='utf-8').read()); print('parse OK')"
```

Expected: `parse OK`.

- [ ] **Step 4: Commit**

```
git add src/zira_dashboard/templates/_dashboards_subnav.html src/zira_dashboard/static/dashboards-subnav.css
git commit -m "$(cat <<'EOF'
feat(dash-subnav): _dashboards_subnav.html partial + CSS

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Page wiring — include sub-nav on 5 templates

**Files:**
- Modify: `src/zira_dashboard/routes/value_streams.py`
- Modify: `src/zira_dashboard/routes/wc_dashboard.py`
- Modify: `src/zira_dashboard/routes/custom_dashboards.py`
- Modify: `src/zira_dashboard/routes/widgets.py`
- Modify: `src/zira_dashboard/templates/recycling.html`
- Modify: `src/zira_dashboard/templates/new_vs.html`
- Modify: `src/zira_dashboard/templates/wc_dashboard.html`
- Modify: `src/zira_dashboard/templates/custom_dashboard.html`
- Modify: `src/zira_dashboard/templates/widgets.html`

Each handler adds `pinned_dashboards` + `active_dashboard_key` to its context. Each template includes the partial + links the CSS. TV-mode pages SKIP the partial (chrome stripped).

- [ ] **Step 1: Update `routes/value_streams.py`**

Open `src/zira_dashboard/routes/value_streams.py`. Find `_render_recycling`'s `templates.TemplateResponse(...)` call and add two context keys (alongside the existing ones). The context dict ends with `"is_today": is_today, ...`. Find a clean place to add (search for `"window": window,` near the top of the context dict):

Add these two lines INSIDE the `"recycling.html"` context dict (a sensible spot is after the `"active_vs": "recycling",` key):

```python
            "pinned_dashboards": _pinned_for_subnav(),
            "active_dashboard_key": "vs_recycling:",
```

Same for `_render_new_vs` — its context dict has `"new_vs.html"`; add after the existing first context key:

```python
            "pinned_dashboards": _pinned_for_subnav(),
            "active_dashboard_key": "vs_new:",
```

At the bottom of `routes/value_streams.py`, add a small helper near the other module-level helpers (BEFORE the `_render_recycling` definition is cleanest, but appending to file works too):

```python
def _pinned_for_subnav():
    from .. import dashboard_catalog
    return dashboard_catalog.pinned_dashboards_for_subnav()
```

- [ ] **Step 2: Update `routes/wc_dashboard.py`**

Open `src/zira_dashboard/routes/wc_dashboard.py`. Find the `templates.TemplateResponse` call in `_render_wc_dashboard` — it returns a context dict with `"slug": slug, "wc_name": wc_name, ...`. Add two keys to the dict:

```python
            "pinned_dashboards": _pinned_for_subnav(),
            "active_dashboard_key": "wc:" + wc_name,
```

Append the helper at the bottom of the file:

```python
def _pinned_for_subnav():
    from .. import dashboard_catalog
    return dashboard_catalog.pinned_dashboards_for_subnav()
```

- [ ] **Step 3: Update `routes/custom_dashboards.py`**

In `_render_dashboard`, find the `templates.TemplateResponse(request, "custom_dashboard.html", ...)` call. Add two keys to the context dict (just after `"dashboard": dash,`):

```python
            "pinned_dashboards": _pinned_for_subnav(),
            "active_dashboard_key": "custom:" + dash["slug"],
```

In `dashboards_index`, find the `templates.TemplateResponse(request, "dashboards.html", ...)` call. Add:

```python
            "pinned_dashboards": _pinned_for_subnav(),
            "active_dashboard_key": "meta:dashboards",
```

Append the helper at the bottom of the file:

```python
def _pinned_for_subnav():
    from .. import dashboard_catalog
    return dashboard_catalog.pinned_dashboards_for_subnav()
```

- [ ] **Step 4: Update `routes/widgets.py`**

In `widgets_page`, find the `templates.TemplateResponse(request, "widgets.html", ...)` call. Add two keys:

```python
            "pinned_dashboards": _pinned_for_subnav(),
            "active_dashboard_key": "meta:widgets",
```

Append at the bottom of the file:

```python
def _pinned_for_subnav():
    from .. import dashboard_catalog
    return dashboard_catalog.pinned_dashboards_for_subnav()
```

- [ ] **Step 5: Update `templates/recycling.html` — link CSS + include partial after the top nav block**

Find `</head>` near the top of the file. Just BEFORE `</head>`, add:

```jinja
<link rel="stylesheet" href="/static/dashboards-subnav.css?v={{ static_v('dashboards-subnav.css') }}">
```

Find the closing `</header>` of the top-nav block (around line 35). Just after that closing tag, add:

```jinja
{% include "_dashboards_subnav.html" %}
```

- [ ] **Step 6: Same edits in `templates/new_vs.html`**

Find `</head>`. Just before it, add:

```jinja
<link rel="stylesheet" href="/static/dashboards-subnav.css?v={{ static_v('dashboards-subnav.css') }}">
```

Find the top-nav `</header>` closing tag (around line 34). Just after it, add:

```jinja
{% include "_dashboards_subnav.html" %}
```

- [ ] **Step 7: Update `templates/wc_dashboard.html`**

The page already has the `tv_header` rendered when `tv_mode` is True; the screen-mode header is a separate block. Find `</head>` near the top and add:

```jinja
<link rel="stylesheet" href="/static/dashboards-subnav.css?v={{ static_v('dashboards-subnav.css') }}">
```

Now find the spot just before `{{ tv_header(...) }}` — there's an `{% if not tv_mode %}` ... `{% endif %}` block somewhere wrapping the screen-mode header (or the page has no top nav of its own). Whatever block wraps the screen-mode chrome, the sub-nav must appear INSIDE the `{% if not tv_mode %}` guard (so it doesn't render on `/tv/wc/{slug}`).

If the current template renders the `tv_header` macro UNCONDITIONALLY, change to:

```jinja
{% if tv_mode %}
  {{ tv_header(...current call...) }}
{% else %}
  {% include "_dashboards_subnav.html" %}
{% endif %}
```

If there's already a `{% if tv_mode %}` ... `{% else %}` block surrounding the screen header, just add the include inside the `{% else %}` branch.

- [ ] **Step 8: Update `templates/custom_dashboard.html`**

Find `</head>` near the top and add:

```jinja
<link rel="stylesheet" href="/static/dashboards-subnav.css?v={{ static_v('dashboards-subnav.css') }}">
```

The template already has `{% if tv_mode %}{{ tv_header(...) }}{% else %}<header class="app">...</header>{% endif %}`. Inside the `{% else %}` branch, AFTER the `</header>` closing tag, add:

```jinja
  {% include "_dashboards_subnav.html" %}
```

- [ ] **Step 9: Update `templates/widgets.html`**

Find `</head>` and add the CSS link:

```jinja
<link rel="stylesheet" href="/static/dashboards-subnav.css?v={{ static_v('dashboards-subnav.css') }}">
```

The page currently has its own inline `<header><nav>...</nav></header>` block at the top. KEEP the top nav (Dashboards/Trophy Case/Staffing/Settings) but REMOVE the inline page-specific nav items that conflict (the existing nav has links to /widgets and /dashboards). The simplest path:

Find:

```jinja
<header>
  <h1>Widget Workshop</h1>
  <nav>
    <a href="/recycling">Dashboards</a>
    <a href="/widgets" class="active">Widgets</a>
    <a href="/dashboards">My Dashboards</a>
    <a href="/trophies">Trophy Case</a>
    <a href="/staffing">Staffing</a>
    <a href="/settings">Settings</a>
  </nav>
</header>
```

Replace with:

```jinja
<header>
  <h1>Widget Workshop</h1>
  <nav>
    <a href="/recycling">Dashboards</a>
    <a href="/trophies">Trophy Case</a>
    <a href="/staffing">Staffing</a>
    <a href="/settings">Settings</a>
  </nav>
</header>
{% include "_dashboards_subnav.html" %}
```

- [ ] **Step 10: Verify all pages parse + boot**

```
.venv/Scripts/python.exe -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'), autoescape=True); [env.parse(open(f'src/zira_dashboard/templates/{n}', encoding='utf-8').read()) for n in ['recycling.html','new_vs.html','wc_dashboard.html','custom_dashboard.html','widgets.html','_dashboards_subnav.html']]; print('parse OK')"
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; print('app OK')"
.venv/Scripts/python.exe -m pytest 2>&1 | tail -3
```

Expected: parse OK, app OK, full suite green (no new failures).

- [ ] **Step 11: Commit**

```
git add src/zira_dashboard/routes/value_streams.py src/zira_dashboard/routes/wc_dashboard.py src/zira_dashboard/routes/custom_dashboards.py src/zira_dashboard/routes/widgets.py src/zira_dashboard/templates/recycling.html src/zira_dashboard/templates/new_vs.html src/zira_dashboard/templates/wc_dashboard.html src/zira_dashboard/templates/custom_dashboard.html src/zira_dashboard/templates/widgets.html
git commit -m "$(cat <<'EOF'
feat(dash-subnav): include sub-nav on every dashboard-family page

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Redesigned `/dashboards` index

**Files:**
- Modify: `src/zira_dashboard/routes/custom_dashboards.py` — extend `dashboards_index` to pass all_dashboards split into built-in + custom
- Modify: `src/zira_dashboard/templates/dashboards.html` — full rewrite (two sections, pin toggles)

- [ ] **Step 1: Extend `dashboards_index` context**

Open `src/zira_dashboard/routes/custom_dashboards.py`. Find `dashboards_index`:

```python
@router.get("/dashboards", response_class=HTMLResponse)
def dashboards_index(request: Request):
    return templates.TemplateResponse(
        request, "dashboards.html",
        {
            "dashboards": custom_dashboards_store.list_dashboards(),
            "wcs": _wc_options(),
            "groups": _group_options(),
            "pinned_dashboards": _pinned_for_subnav(),
            "active_dashboard_key": "meta:dashboards",
        },
    )
```

Replace with:

```python
@router.get("/dashboards", response_class=HTMLResponse)
def dashboards_index(request: Request):
    from .. import dashboard_catalog
    all_d = dashboard_catalog.all_dashboards()
    builtin = [d for d in all_d if d["kind"] in ("vs_recycling", "vs_new", "wc")]
    custom = [d for d in all_d if d["kind"] == "custom"]
    return templates.TemplateResponse(
        request, "dashboards.html",
        {
            "builtin_dashboards": builtin,
            "custom_dashboards": custom,
            "wcs": _wc_options(),
            "groups": _group_options(),
            "pinned_dashboards": _pinned_for_subnav(),
            "active_dashboard_key": "meta:dashboards",
        },
    )
```

- [ ] **Step 2: Rewrite `templates/dashboards.html`**

Open `src/zira_dashboard/templates/dashboards.html`. Replace its entire contents with:

```jinja
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="/static/gpi-logo.png">
<title>My Dashboards — GPI Plant Manager</title>
<link rel="stylesheet" href="/static/dashboards-subnav.css?v={{ static_v('dashboards-subnav.css') }}">
<style>
  :root {
    --bg: #f1f4f7; --panel: #ffffff; --panel-2: #f1f4f7;
    --border: #d8dee5; --fg: #1f2937; --muted: #6b7280;
    --accent: #16a34a; --accent-dim: #dcfce7; --bad: #ef4444;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: var(--bg); color: var(--fg); }
  header.app { padding: 0.9rem 1.25rem; background: var(--panel); border-bottom: 1px solid var(--border); display: flex; gap: 1rem; align-items: center; }
  header.app h1 { margin: 0; font-size: 1.1rem; }
  header.app nav { display: flex; gap: 0.5rem; }
  header.app nav a { color: var(--muted); text-decoration: none; font-size: 0.9rem; padding: 0.25rem 0.6rem; border-radius: 6px; }
  main { padding: 1rem; }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 0.8rem 1rem; margin-bottom: 1rem; }
  h2.section { margin: 0 0 0.6rem; font-size: 0.78rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.8px; color: var(--muted); }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border); text-align: left; font-size: 0.9rem; vertical-align: middle; }
  th { color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.6px; }
  tr:last-child td { border-bottom: none; }
  .star { background: transparent; border: none; cursor: pointer; font-size: 1.1rem; color: var(--muted); padding: 0 0.3rem; }
  .star.on { color: #f59e0b; }
  a.btn { color: var(--accent); text-decoration: none; padding: 0.2rem 0.55rem; border: 1px solid var(--accent); border-radius: 5px; font-size: 0.8rem; margin-right: 0.3rem; }
  a.btn-tv { background: var(--accent); color: white; }
  button.danger { background: transparent; color: var(--bad); border: 1px solid var(--bad); border-radius: 5px; padding: 0.2rem 0.55rem; cursor: pointer; font-size: 0.8rem; }
  .scope-label { color: var(--muted); font-size: 0.82rem; }
  .new-form { margin-top: 0.75rem; display: flex; gap: 0.5rem; align-items: center; }
  .new-form input, .new-form select { background: var(--panel-2); color: var(--fg); border: 1px solid var(--border); border-radius: 6px; padding: 0.3rem 0.5rem; font: inherit; font-size: 0.9rem; }
  .new-form .submit { background: var(--accent); color: white; border: 1px solid var(--accent); border-radius: 6px; padding: 0.35rem 0.9rem; font-weight: 700; cursor: pointer; }
</style>
</head>
<body>
<header class="app">
  <h1>Plant Manager</h1>
  <nav>
    <a href="/recycling">Dashboards</a>
    <a href="/trophies">Trophy Case</a>
    <a href="/staffing">Staffing</a>
    <a href="/settings">Settings</a>
  </nav>
</header>
{% include "_dashboards_subnav.html" %}
<main>
  <section class="panel">
    <h2 class="section">Built-in dashboards</h2>
    <table>
      <thead><tr><th></th><th>Name</th><th>Actions</th></tr></thead>
      <tbody>
        {% for d in builtin_dashboards %}
          <tr data-kind="{{ d.kind }}" data-ref="{{ d.ref }}">
            <td><button type="button" class="star {% if d.pinned %}on{% endif %}" title="Pin to sub-nav">{% if d.pinned %}★{% else %}☆{% endif %}</button></td>
            <td>{{ d.name }}</td>
            <td>
              <a class="btn" href="{{ d.open_url }}">Open</a>
              <a class="btn btn-tv" href="{{ d.tv_url }}">Open as TV</a>
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </section>

  <section class="panel">
    <h2 class="section">My custom dashboards</h2>
    <table>
      <thead><tr><th></th><th>Name</th><th>Scope</th><th>Widgets</th><th>Actions</th></tr></thead>
      <tbody id="custom-body">
        {% for d in custom_dashboards %}
          <tr data-kind="custom" data-ref="{{ d.ref }}" data-id="{{ d.id }}">
            <td><button type="button" class="star {% if d.pinned %}on{% endif %}" title="Pin to sub-nav">{% if d.pinned %}★{% else %}☆{% endif %}</button></td>
            <td>{{ d.name }}</td>
            <td class="scope-label">{{ d.scope_kind }}: {{ d.scope_value }}</td>
            <td>{{ d.widget_count }}</td>
            <td>
              <a class="btn" href="{{ d.open_url }}">Edit</a>
              <a class="btn btn-tv" href="{{ d.tv_url }}">Open as TV</a>
              <button type="button" class="danger del-btn">×</button>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="5"><em>No custom dashboards yet — create one below.</em></td></tr>
        {% endfor %}
      </tbody>
    </table>

    <div class="new-form">
      <input type="text" id="new-name" placeholder="Dashboard name (e.g. Repair 1 TV)">
      <select id="new-scope-kind">
        <option value="wc" selected>Work Center</option>
        <option value="group">Group</option>
      </select>
      <select id="new-scope-wc">
        {% for w in wcs %}<option value="{{ w.name }}">{{ w.name }}</option>{% endfor %}
      </select>
      <select id="new-scope-group" style="display:none">
        {% for g in groups %}<option value="{{ g.name }}">{{ g.name }}</option>{% endfor %}
      </select>
      <select id="new-theme">
        <option value="dark" selected>Dark</option>
        <option value="light">Light</option>
      </select>
      <button type="button" class="submit" id="new-btn">+ Create new dashboard</button>
    </div>
  </section>
</main>

<script>
(function() {
  // ---- Star toggle ----
  document.querySelectorAll('.star').forEach(btn => {
    btn.addEventListener('click', () => {
      const row = btn.closest('tr');
      const kind = row.dataset.kind;
      const ref = row.dataset.ref;
      const currentlyPinned = btn.classList.contains('on');
      fetch('/api/pinned-dashboards', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({kind, ref, pinned: !currentlyPinned}),
      }).then(r => r.json()).then(d => {
        if (d.ok) {
          btn.classList.toggle('on');
          btn.textContent = btn.classList.contains('on') ? '★' : '☆';
        } else {
          alert('Pin failed: ' + (d.error || 'unknown'));
        }
      });
    });
  });

  // ---- Custom dashboard create + delete (unchanged from before) ----
  const kindSel = document.getElementById('new-scope-kind');
  const wcSel = document.getElementById('new-scope-wc');
  const grpSel = document.getElementById('new-scope-group');
  if (kindSel) {
    kindSel.addEventListener('change', () => {
      wcSel.style.display = kindSel.value === 'wc' ? '' : 'none';
      grpSel.style.display = kindSel.value === 'group' ? '' : 'none';
    });
  }
  const newBtn = document.getElementById('new-btn');
  if (newBtn) {
    newBtn.addEventListener('click', () => {
      const name = document.getElementById('new-name').value.trim();
      if (!name) return;
      const scope_value = kindSel.value === 'wc' ? wcSel.value : grpSel.value;
      fetch('/api/dashboards', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          name, scope_kind: kindSel.value, scope_value,
          theme: document.getElementById('new-theme').value,
        }),
      }).then(r => r.json()).then(d => {
        if (d.ok) location.href = '/dashboards/' + d.dashboard.slug;
        else alert('Error: ' + (d.error || 'unknown'));
      });
    });
  }

  document.querySelectorAll('.del-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tr = btn.closest('tr');
      const id = tr.dataset.id;
      if (!confirm('Delete this dashboard?')) return;
      fetch('/api/dashboards/' + id, {method: 'DELETE'})
        .then(r => r.json()).then(d => {
          if (d.ok) tr.remove();
        });
    });
  });
})();
</script>
</body>
</html>
```

- [ ] **Step 3: Verify parse + app + tests**

```
.venv/Scripts/python.exe -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'), autoescape=True); env.parse(open('src/zira_dashboard/templates/dashboards.html', encoding='utf-8').read()); print('parse OK')"
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; print('app OK')"
.venv/Scripts/python.exe -m pytest 2>&1 | tail -3
```

Expected: parse OK, app OK, suite green.

- [ ] **Step 4: Commit**

```
git add src/zira_dashboard/routes/custom_dashboards.py src/zira_dashboard/templates/dashboards.html
git commit -m "$(cat <<'EOF'
feat(dash-subnav): redesigned /dashboards index — Built-in + Custom with pin toggle

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Top-nav / sidebar cleanup + TVs flat picker + CHANGELOG + push

**Files:**
- Modify: `src/zira_dashboard/templates/index.html` — drop top-nav "My Dashboards"
- Modify: `src/zira_dashboard/templates/recycling.html` — drop top-nav "My Dashboards"
- Modify: `src/zira_dashboard/templates/new_vs.html` — drop top-nav "My Dashboards"
- Modify: `src/zira_dashboard/templates/_staffing_base.html` — drop top-nav "My Dashboards"
- Modify: `src/zira_dashboard/templates/settings.html` — drop top-nav "My Dashboards" + sidebar entries
- Modify: `src/zira_dashboard/routes/settings.py` — pass `all_dashboards` to template
- Modify: `src/zira_dashboard/templates/_settings_tvs.html` — flat dashboard picker
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Drop "My Dashboards" from top nav in 5 templates**

For each of `index.html`, `recycling.html`, `new_vs.html`, `_staffing_base.html`, `settings.html`, find the line `<a href="/dashboards">My Dashboards</a>` (with whatever inline styles each uses) and delete the whole line.

For example in `recycling.html`:

Find:

```jinja
      <a href="/recycling" class="active">Dashboards</a>
      <a href="/dashboards">My Dashboards</a>
      <a href="/trophies">Trophy Case</a>
```

Replace with:

```jinja
      <a href="/recycling" class="active">Dashboards</a>
      <a href="/trophies">Trophy Case</a>
```

Repeat for each file (each has its own variant — sometimes inline-styled, sometimes class-styled, but the link text `My Dashboards` is the unique marker).

- [ ] **Step 2: Drop the two new sidebar entries from `settings.html`**

In `templates/settings.html`, find:

```jinja
  <aside class="settings-sidebar" aria-label="Settings sections">
    <a href="/widgets" class="settings-nav-item">Widget Workshop</a>
    <a href="/dashboards" class="settings-nav-item">My Dashboards</a>
    <a href="?section=work_centers"
```

Replace with:

```jinja
  <aside class="settings-sidebar" aria-label="Settings sections">
    <a href="?section=work_centers"
```

- [ ] **Step 3: Pass `all_dashboards` to the Settings template for the TVs picker**

Open `src/zira_dashboard/routes/settings.py`. Find the TVs context block:

```python
    tv_displays_rows: list[dict] = []
    tv_templates_rows: list[dict] = []
    custom_dashboards_rows: list[dict] = []
    if section == "tvs":
        from .. import tv_displays_store, tv_templates_store, custom_dashboards_store
        tv_displays_rows = tv_displays_store.list_displays()
        tv_templates_rows = tv_templates_store.list_templates()
        custom_dashboards_rows = custom_dashboards_store.list_dashboards()
```

Replace with:

```python
    tv_displays_rows: list[dict] = []
    tv_templates_rows: list[dict] = []
    custom_dashboards_rows: list[dict] = []
    all_dashboards_for_picker: list[dict] = []
    if section == "tvs":
        from .. import tv_displays_store, tv_templates_store, custom_dashboards_store, dashboard_catalog
        tv_displays_rows = tv_displays_store.list_displays()
        tv_templates_rows = tv_templates_store.list_templates()
        custom_dashboards_rows = custom_dashboards_store.list_dashboards()
        all_dashboards_for_picker = dashboard_catalog.all_dashboards()
```

Then in the `return templates.TemplateResponse(...)` context dict, add `all_dashboards_for_picker`:

Find:

```python
            "tv_displays_rows": tv_displays_rows,
            "tv_templates_rows": tv_templates_rows,
            "custom_dashboards_rows": custom_dashboards_rows,
            "wc_locations_for_picker": [{"name": loc.name} for loc in staffing.LOCATIONS],
```

Replace with:

```python
            "tv_displays_rows": tv_displays_rows,
            "tv_templates_rows": tv_templates_rows,
            "custom_dashboards_rows": custom_dashboards_rows,
            "all_dashboards_for_picker": all_dashboards_for_picker,
            "wc_locations_for_picker": [{"name": loc.name} for loc in staffing.LOCATIONS],
```

- [ ] **Step 4: Replace the TVs row picker with a flat dashboard select**

Open `src/zira_dashboard/templates/_settings_tvs.html`. Find this whole block in the per-row markup:

```jinja
          <td>
            <select class="tv-kind-select">
              <option value="vs_recycling" {% if d.kind == 'vs_recycling' %}selected{% endif %}>Recycling VS</option>
              <option value="vs_new" {% if d.kind == 'vs_new' %}selected{% endif %}>New VS</option>
              <option value="wc" {% if d.kind == 'wc' %}selected{% endif %}>Work Center</option>
              <option value="custom" {% if d.kind == 'custom' %}selected{% endif %}>Custom Dashboard</option>
            </select>
            <select class="tv-wc-select" {% if d.kind != 'wc' %}style="display:none"{% endif %}>
              {% for loc in wc_locations_for_picker %}
                <option value="{{ loc.name }}" {% if d.wc_name == loc.name %}selected{% endif %}>{{ loc.name }}</option>
              {% endfor %}
            </select>
            <select class="tv-custom-select" {% if d.kind != 'custom' %}style="display:none"{% endif %}>
              {% for cd in custom_dashboards_rows %}
                <option value="{{ cd.id }}" {% if d.custom_dashboard_id == cd.id %}selected{% endif %}>{{ cd.name }}</option>
              {% endfor %}
            </select>
          </td>
```

Replace with:

```jinja
          <td>
            <select class="tv-dashboard-select">
              <optgroup label="Built-in">
                {% for dash in all_dashboards_for_picker %}
                  {% if dash.kind in ('vs_recycling', 'vs_new', 'wc') %}
                    <option value="{{ dash.kind }}|{{ dash.ref }}"
                            {% if d.kind == dash.kind and (
                              (d.kind in ('vs_recycling','vs_new')) or
                              (d.kind == 'wc' and d.wc_name == dash.ref)
                            ) %}selected{% endif %}>
                      {{ dash.name }}
                    </option>
                  {% endif %}
                {% endfor %}
              </optgroup>
              <optgroup label="Custom">
                {% for dash in all_dashboards_for_picker %}
                  {% if dash.kind == 'custom' %}
                    <option value="custom|{{ dash.id }}"
                            {% if d.kind == 'custom' and d.custom_dashboard_id == dash.id %}selected{% endif %}>
                      {{ dash.name }}
                    </option>
                  {% endif %}
                {% endfor %}
              </optgroup>
            </select>
          </td>
```

- [ ] **Step 5: Replace the Add-form pickers with a flat select**

In the same file, find the Add form:

```jinja
    <select id="tv-add-kind">
      <option value="vs_recycling">Recycling VS</option>
      <option value="vs_new">New VS</option>
      <option value="wc" selected>Work Center</option>
      <option value="custom">Custom Dashboard</option>
    </select>
    <select id="tv-add-wc">
      {% for loc in wc_locations_for_picker %}
        <option value="{{ loc.name }}">{{ loc.name }}</option>
      {% endfor %}
    </select>
    <select id="tv-add-custom" style="display:none">
      {% for cd in custom_dashboards_rows %}
        <option value="{{ cd.id }}">{{ cd.name }}</option>
      {% endfor %}
    </select>
```

Replace with:

```jinja
    <select id="tv-add-dashboard">
      <optgroup label="Built-in">
        {% for dash in all_dashboards_for_picker %}
          {% if dash.kind in ('vs_recycling', 'vs_new', 'wc') %}
            <option value="{{ dash.kind }}|{{ dash.ref }}">{{ dash.name }}</option>
          {% endif %}
        {% endfor %}
      </optgroup>
      <optgroup label="Custom">
        {% for dash in all_dashboards_for_picker %}
          {% if dash.kind == 'custom' %}
            <option value="custom|{{ dash.id }}">{{ dash.name }}</option>
          {% endif %}
        {% endfor %}
      </optgroup>
    </select>
```

- [ ] **Step 6: Rewrite the JS to use the flat picker**

In the same file, find the `saveRow` function:

```javascript
  function saveRow(tr) {
    const id = tr.dataset.id;
    const name = tr.querySelector('.tv-name-input').value.trim();
    const kind = tr.querySelector('.tv-kind-select').value;
    const wcSel = tr.querySelector('.tv-wc-select');
    const customSel = tr.querySelector('.tv-custom-select');
    const wc_name = kind === 'wc' ? wcSel.value : null;
    const custom_dashboard_id = kind === 'custom' ? parseInt(customSel.value, 10) : null;
    const themeBtn = tr.querySelector('.tv-theme-btn.active');
    const theme = themeBtn ? themeBtn.dataset.theme : 'dark';
    return postJson('/api/tv-displays', {
      id: parseInt(id, 10), name, kind, wc_name, custom_dashboard_id, theme,
    }).then(data => {
```

Replace with:

```javascript
  function parsePickerValue(v) {
    const i = v.indexOf('|');
    const kind = i >= 0 ? v.slice(0, i) : v;
    const ref = i >= 0 ? v.slice(i + 1) : '';
    if (kind === 'wc') return {kind, wc_name: ref, custom_dashboard_id: null};
    if (kind === 'custom') return {kind, wc_name: null, custom_dashboard_id: parseInt(ref, 10)};
    return {kind, wc_name: null, custom_dashboard_id: null};
  }

  function saveRow(tr) {
    const id = tr.dataset.id;
    const name = tr.querySelector('.tv-name-input').value.trim();
    const parsed = parsePickerValue(tr.querySelector('.tv-dashboard-select').value);
    const themeBtn = tr.querySelector('.tv-theme-btn.active');
    const theme = themeBtn ? themeBtn.dataset.theme : 'dark';
    return postJson('/api/tv-displays', {
      id: parseInt(id, 10), name,
      kind: parsed.kind, wc_name: parsed.wc_name,
      custom_dashboard_id: parsed.custom_dashboard_id, theme,
    }).then(data => {
```

Then find the per-row handler block that wires up the cascading selects:

```javascript
    tr.querySelector('.tv-kind-select').addEventListener('change', (e) => {
      const wcSel = tr.querySelector('.tv-wc-select');
      const customSel = tr.querySelector('.tv-custom-select');
      wcSel.style.display = e.target.value === 'wc' ? '' : 'none';
      customSel.style.display = e.target.value === 'custom' ? '' : 'none';
      saveRow(tr);
    });
    tr.querySelector('.tv-wc-select').addEventListener('change', () => saveRow(tr));
    tr.querySelector('.tv-custom-select').addEventListener('change', () => saveRow(tr));
```

Replace with:

```javascript
    tr.querySelector('.tv-dashboard-select').addEventListener('change', () => saveRow(tr));
```

Then find the Add-form kind-change handler:

```javascript
  const addKindSel = document.getElementById('tv-add-kind');
  const addWcSel = document.getElementById('tv-add-wc');
  const addCustomSel = document.getElementById('tv-add-custom');
  if (addKindSel) {
    addKindSel.addEventListener('change', (e) => {
      addWcSel.style.display = e.target.value === 'wc' ? '' : 'none';
      addCustomSel.style.display = e.target.value === 'custom' ? '' : 'none';
    });
  }
```

Replace with:

```javascript
  const addDashboardSel = document.getElementById('tv-add-dashboard');
```

Then find the Add-button click handler:

```javascript
      const name = document.getElementById('tv-add-name').value.trim();
      const kind = addKindSel.value;
      const wc_name = kind === 'wc' ? addWcSel.value : null;
      const custom_dashboard_id = kind === 'custom' ? parseInt(addCustomSel.value, 10) : null;
      const theme = document.getElementById('tv-add-theme').value;
      if (!name) { showStatus('tv-add-status', 'name required'); return; }
      showStatus('tv-add-status', 'Adding…');
      postJson('/api/tv-displays', {name, kind, wc_name, custom_dashboard_id, theme}).then(data => {
```

Replace with:

```javascript
      const name = document.getElementById('tv-add-name').value.trim();
      const parsed = parsePickerValue(addDashboardSel.value);
      const theme = document.getElementById('tv-add-theme').value;
      if (!name) { showStatus('tv-add-status', 'name required'); return; }
      showStatus('tv-add-status', 'Adding…');
      postJson('/api/tv-displays', {
        name, kind: parsed.kind, wc_name: parsed.wc_name,
        custom_dashboard_id: parsed.custom_dashboard_id, theme,
      }).then(data => {
```

- [ ] **Step 7: Verify**

```
.venv/Scripts/python.exe -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'), autoescape=True); [env.parse(open(f'src/zira_dashboard/templates/{n}', encoding='utf-8').read()) for n in ['index.html','recycling.html','new_vs.html','_staffing_base.html','settings.html','_settings_tvs.html']]; print('parse OK')"
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; print('app OK')"
.venv/Scripts/python.exe -m pytest 2>&1 | tail -3
```

Expected: parse OK, app OK, suite green.

- [ ] **Step 8: Get current time**

```
powershell.exe -Command "Get-Date -Format 'h:mm tt'"
```

- [ ] **Step 9: Add CHANGELOG entry**

In `CHANGELOG.md`, insert a new `### <HH:MM TT>` block at the top of today's `## 2026-05-13` section:

```markdown
### <HH:MM TT>

- **Dashboards sub-nav + pinning + unified index + simpler TVs picker** — major restructure of the dashboards family. (1) A new sub-nav strip under the top "Dashboards" tab shows your **pinned dashboards** left-aligned (Recycling VS + New VS pinned by default), with **My Dashboards** and **Workshop** anchored on the right. The strip renders on every dashboard-family page (`/recycling`, `/new-vs`, `/wc/{slug}`, `/dashboards/{slug}`, `/dashboards`, `/widgets`) so you can hop between favorites in one click. TV-mode pages stay chrome-stripped (no sub-nav there). (2) The redesigned `/dashboards` index lists **every dashboard in the system** — Built-in (Recycling VS, New VS, one per WC) and My custom dashboards — with a ★/☆ pin toggle per row that saves to a new `pinned_dashboards` table. (3) **Top nav + Settings sidebar cleanup**: the "My Dashboards" top-nav link and the Settings sidebar entries for Widget Workshop / My Dashboards are gone (now reachable via the sub-nav). The four top tabs are back to Dashboards · Trophy Case · Staffing · Settings. (4) **TVs settings flat picker**: the kind / wc / custom-dashboard cascading selects collapse into one **Dashboard** picker listing everything with Built-in and Custom optgroups. Existing TV display rows render correctly with the new picker auto-selected to their target. Schema unchanged.
```

- [ ] **Step 10: Commit + push**

```
git add src/zira_dashboard/templates/index.html src/zira_dashboard/templates/recycling.html src/zira_dashboard/templates/new_vs.html src/zira_dashboard/templates/_staffing_base.html src/zira_dashboard/templates/settings.html src/zira_dashboard/routes/settings.py src/zira_dashboard/templates/_settings_tvs.html CHANGELOG.md
git commit -m "$(cat <<'EOF'
feat(dash-subnav): nav cleanup + flat TVs picker + Phase changelog

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

Railway picks up the push. After deploy:

1. Top nav: only Dashboards / Trophy Case / Staffing / Settings — no "My Dashboards" link.
2. Visit `/recycling` → sub-nav under the top nav shows `Recycling VS · New VS` pinned + `My Dashboards | Workshop` on the right.
3. Click "My Dashboards" → see both built-in and custom dashboards with pin stars; pin a few and verify they appear in the sub-nav.
4. Visit `/settings?section=tvs` → the Add form and each row have a single Dashboard picker with Built-in / Custom optgroups.

---

## Done

The dashboards family is now coherent: every dashboard listed in one place, favorites surfaced as sub-tabs, management pages reachable via sub-nav, and TVs simplified to one picker per row.
