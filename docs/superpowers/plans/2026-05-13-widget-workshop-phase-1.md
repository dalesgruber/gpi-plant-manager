# Widget Workshop & Custom Dashboards — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the widget catalog + custom-dashboard infrastructure end-to-end so Dale can create a saved widget preset in the Workshop, build a custom dashboard, drop the widget on it (with per-placement data overrides), and view the dashboard in editor OR TV mode. Phase 1 ships 3 widget types: Pallets-by-WC, Vs. Goat Pace, Monthly Ribbons.

**Architecture:** A widget type registry (`widget_types.py`) holds 3 entries, each with a parameter schema, a data-resolver function name, and a Jinja partial path. Three tables (`widget_definitions`, `custom_dashboards`, `dashboard_widgets`) persist presets + dashboards + placements. Two stores (`widget_definitions_store`, `custom_dashboards_store`) handle CRUD. Two route modules (`routes/widgets.py`, `routes/custom_dashboards.py`) own the HTTP. A generic dispatcher partial (`_widget_render.html`) renders any placement by switching on its type.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, psycopg2 + Postgres, pytest, gridstack 10.3. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-13-widget-workshop-and-custom-dashboards-design.md`

---

## File Structure

**New files:**
- `src/zira_dashboard/widget_types.py` — type registry, 3 entries with schemas + resolver/partial pointers
- `src/zira_dashboard/widget_data.py` — three resolver functions (one per type)
- `src/zira_dashboard/widget_definitions_store.py` — CRUD on `widget_definitions`
- `src/zira_dashboard/custom_dashboards_store.py` — CRUD on `custom_dashboards` + `dashboard_widgets`
- `src/zira_dashboard/routes/widgets.py` — workshop page + `/api/widget-defs` CRUD + `/api/widgets/types` + `/api/widgets/options/{kind}`
- `src/zira_dashboard/routes/custom_dashboards.py` — `/dashboards` index, `/dashboards/{slug}` editor, `/tv/dashboards/{slug}` TV, `/api/dashboards` + `/api/placements` CRUD
- `src/zira_dashboard/templates/widgets.html` — workshop page
- `src/zira_dashboard/templates/dashboards.html` — dashboards index
- `src/zira_dashboard/templates/custom_dashboard.html` — editor + TV (gated on `tv_mode`)
- `src/zira_dashboard/templates/_widget_render.html` — generic dispatcher partial
- `src/zira_dashboard/templates/widgets/_widget_pallets_by_wc.html` — partial for type=pallets_by_wc
- `src/zira_dashboard/templates/widgets/_widget_goat_race.html` — partial for type=goat_race
- `src/zira_dashboard/templates/widgets/_widget_ribbons.html` — partial for type=ribbons
- `tests/test_widget_types.py` — unit tests for the registry (schemas, resolver names exist, partial paths exist)
- `tests/test_widget_data.py` — unit tests for the 3 resolvers (mock underlying helpers)
- `tests/test_widget_definitions_store.py` — Postgres-gated CRUD tests
- `tests/test_custom_dashboards_store.py` — Postgres-gated CRUD + placement tests
- `tests/test_widgets_routes.py` — integration tests for workshop routes
- `tests/test_custom_dashboards_routes.py` — integration tests for dashboard routes

**Modified files:**
- `src/zira_dashboard/db.py` — append 3 `CREATE TABLE` blocks to `_SCHEMA_DDL`
- `src/zira_dashboard/app.py` — register two new routers
- `src/zira_dashboard/routes/settings.py` — add "Widget Workshop" entry to the settings left-rail (linking to `/widgets`, not a settings sub-section — just a nav shortcut)
- `src/zira_dashboard/templates/settings.html` — add the link to the sidebar
- `tests/test_db.py` — assert the 3 new tables are created
- `CHANGELOG.md` — one deploy entry

**Responsibility split:** Each file has one job. The registry knows types; the data module knows how to fetch data for each type; the stores know how to persist; the routes know HTTP; the templates know rendering. Adding a 4th widget type later requires only: one registry entry, one resolver function, one partial — no changes to stores or routes.

---

## Conventions

- Python interpreter: `.venv/Scripts/python.exe`.
- Postgres-touching tests gate on `DATABASE_URL` via module-level `pytestmark = pytest.mark.skipif(...)`.
- Slug derivation reuses `wc_dashboard_data.slug_for_wc` for custom dashboards.
- Commit messages: `feat(widgets):` / `test(widgets):` / `schema(widgets):` / `docs:`.
- Existing helpers reused as-is — DO NOT modify:
  - `awards.goat(group_name)` — group-scoped GOAT lookup
  - `awards.monthly_badges(group_name, year, month)` — top-3 person-days
  - `leaderboard.cached_leaderboard(client, stations, day, now)` — Zira data
  - `work_centers_store.members(kind, name)`, `.all_group_names(kind)`, `.goal_per_day(loc)`
  - `wc_dashboard_data.assigned_operators_for_wc(wc_name, day)`
  - `wc_dashboard_data.slug_for_wc(name)`
  - `staffing.LOCATIONS` for the WC roster
  - `_tv_header.html` macro: `tv_header(name, crumb=None, right=None)`
  - `shift_config.SITE_TZ` + `wc_dashboard_data._shift_elapsed_fraction(day)` for proration

---

## Task 1: Schema migration — 3 tables

**Files:**
- Modify: `src/zira_dashboard/db.py` — append to `_SCHEMA_DDL`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_db.py`:

```python
def test_bootstrap_creates_widget_workshop_tables():
    db.init_pool()
    db.bootstrap_schema()
    rows = db.query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name IN "
        "('widget_definitions', 'custom_dashboards', 'dashboard_widgets')"
    )
    names = {r["table_name"] for r in rows}
    assert names == {"widget_definitions", "custom_dashboards", "dashboard_widgets"}, \
        f"missing tables: {names}"


def test_widget_definitions_columns():
    db.init_pool()
    db.bootstrap_schema()
    cols = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'widget_definitions'"
    )
    names = {r["column_name"] for r in cols}
    expected = {"id", "name", "type", "visual_json", "default_data_json", "created_at", "updated_at"}
    assert expected.issubset(names), f"missing columns: {expected - names}"


def test_custom_dashboards_columns_and_slug_unique():
    db.init_pool()
    db.bootstrap_schema()
    cols = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'custom_dashboards'"
    )
    names = {r["column_name"] for r in cols}
    expected = {"id", "name", "slug", "scope_kind", "scope_value", "theme",
                "sort_order", "created_at", "updated_at"}
    assert expected.issubset(names), f"missing columns: {expected - names}"
    idx_rows = db.query(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = 'custom_dashboards'"
    )
    assert any("slug" in r["indexdef"] and "UNIQUE" in r["indexdef"].upper() for r in idx_rows), \
        "custom_dashboards.slug must be UNIQUE"


def test_dashboard_widgets_columns_and_fks():
    db.init_pool()
    db.bootstrap_schema()
    cols = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'dashboard_widgets'"
    )
    names = {r["column_name"] for r in cols}
    expected = {"id", "dashboard_id", "widget_def_id", "x", "y", "w", "h",
                "data_overrides_json", "sort_order", "created_at"}
    assert expected.issubset(names), f"missing columns: {expected - names}"
    # Verify FKs exist.
    fks = db.query(
        "SELECT constraint_name, delete_rule "
        "FROM information_schema.referential_constraints "
        "WHERE constraint_schema = 'public' "
        "  AND constraint_name LIKE 'dashboard_widgets%'"
    )
    rules = {f["delete_rule"] for f in fks}
    assert "CASCADE" in rules, "dashboard_widgets.dashboard_id should ON DELETE CASCADE"
    assert "RESTRICT" in rules, "dashboard_widgets.widget_def_id should ON DELETE RESTRICT"
```

- [ ] **Step 2: Run to confirm fail/skip**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py -v 2>&1 | tail -15`
Expected: tests SKIP without `DATABASE_URL`, FAIL with one (tables don't exist yet).

- [ ] **Step 3: Append DDL to `_SCHEMA_DDL` in `db.py`**

Open `src/zira_dashboard/db.py`. Find the closing `"""` of `_SCHEMA_DDL` (currently right after the `tv_displays` table block). Append BEFORE the closing `"""`:

```sql
-- Widget Workshop & Custom Dashboards (sub-project 5, phase 1) ---------
-- widget_definitions: named presets — type + visual config + default data scope.
CREATE TABLE IF NOT EXISTS widget_definitions (
  id                SERIAL PRIMARY KEY,
  name              TEXT NOT NULL,
  type              TEXT NOT NULL,
  visual_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
  default_data_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_widget_definitions_type ON widget_definitions (type);

-- custom_dashboards: user-built dashboards. scope drives the TV header.
CREATE TABLE IF NOT EXISTS custom_dashboards (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL,
  slug        TEXT NOT NULL UNIQUE,
  scope_kind  TEXT NOT NULL CHECK (scope_kind IN ('wc', 'group')),
  scope_value TEXT NOT NULL,
  theme       TEXT NOT NULL DEFAULT 'dark' CHECK (theme IN ('light', 'dark')),
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- dashboard_widgets: placements. Each row is one widget on one dashboard.
-- ON DELETE CASCADE so deleting a dashboard sweeps its placements.
-- ON DELETE RESTRICT so a referenced widget definition can't be deleted.
CREATE TABLE IF NOT EXISTS dashboard_widgets (
  id                  SERIAL PRIMARY KEY,
  dashboard_id        INTEGER NOT NULL
                        REFERENCES custom_dashboards(id) ON DELETE CASCADE,
  widget_def_id       INTEGER NOT NULL
                        REFERENCES widget_definitions(id) ON DELETE RESTRICT,
  x                   INTEGER NOT NULL DEFAULT 0,
  y                   INTEGER NOT NULL DEFAULT 0,
  w                   INTEGER NOT NULL DEFAULT 4,
  h                   INTEGER NOT NULL DEFAULT 4,
  data_overrides_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  sort_order          INTEGER NOT NULL DEFAULT 0,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_dashboard
  ON dashboard_widgets (dashboard_id);
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py -v 2>&1 | tail -10`
Expected: all PASS with `DATABASE_URL`, SKIP without.
Also: `.venv/Scripts/python.exe -c "from zira_dashboard.app import app; print('OK')"` → `OK`.

- [ ] **Step 5: Commit**

```
git add src/zira_dashboard/db.py tests/test_db.py
git commit -m "$(cat <<'EOF'
schema(widgets): widget_definitions / custom_dashboards / dashboard_widgets

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Widget type registry + 3 resolvers

**Files:**
- Create: `src/zira_dashboard/widget_types.py`
- Create: `src/zira_dashboard/widget_data.py`
- Test: `tests/test_widget_types.py`
- Test: `tests/test_widget_data.py`

- [ ] **Step 1: Write failing tests for the registry**

Create `tests/test_widget_types.py`:

```python
"""Unit tests for the widget type registry.

These run without Postgres — the registry is in-memory metadata only.
"""
from __future__ import annotations

from zira_dashboard import widget_types


def test_registry_has_three_phase1_types():
    types = widget_types.all_types()
    type_ids = {t["type"] for t in types}
    assert {"pallets_by_wc", "goat_race", "ribbons"}.issubset(type_ids)


def test_each_entry_has_required_fields():
    for entry in widget_types.all_types():
        assert isinstance(entry["type"], str) and entry["type"]
        assert isinstance(entry["label"], str) and entry["label"]
        assert isinstance(entry["data_params_schema"], list)
        assert isinstance(entry["visual_params_schema"], list)
        assert isinstance(entry["resolver"], str) and entry["resolver"]
        assert isinstance(entry["partial"], str) and entry["partial"]


def test_resolver_names_resolve_to_real_functions():
    from zira_dashboard import widget_data
    for entry in widget_types.all_types():
        fn = getattr(widget_data, entry["resolver"], None)
        assert callable(fn), f"resolver {entry['resolver']} not found in widget_data"


def test_partial_paths_point_to_existing_files():
    import os
    template_dir = os.path.join(
        os.path.dirname(__file__), "..", "src", "zira_dashboard", "templates"
    )
    for entry in widget_types.all_types():
        path = os.path.join(template_dir, entry["partial"])
        assert os.path.exists(path), f"partial not found: {entry['partial']}"


def test_get_returns_entry_by_type():
    entry = widget_types.get("goat_race")
    assert entry is not None
    assert entry["type"] == "goat_race"


def test_get_unknown_type_returns_none():
    assert widget_types.get("nonexistent_type") is None


def test_options_from_values_are_in_allow_list():
    """data_params_schema entries using options_from must reference a known kind."""
    allowed = {"groups", "value_streams", "wcs"}
    for entry in widget_types.all_types():
        for field in entry["data_params_schema"]:
            if "options_from" in field:
                assert field["options_from"] in allowed, \
                    f"unknown options_from: {field['options_from']}"
```

- [ ] **Step 2: Write failing tests for the resolvers**

Create `tests/test_widget_data.py`:

```python
"""Unit tests for widget resolvers. Mock the underlying helpers
(`cached_leaderboard`, `awards.goat`, `awards.monthly_badges`,
`work_centers_store.members`) — resolvers must work without DB."""
from __future__ import annotations

from datetime import date


def test_resolve_pallets_by_wc_returns_items_and_total(monkeypatch):
    from zira_dashboard import widget_data, work_centers_store

    class _Loc:
        def __init__(self, name, meter_id="m1"):
            self.name = name
            self.meter_id = meter_id

    monkeypatch.setattr(
        work_centers_store, "members",
        lambda kind, name: [_Loc("Repair 1"), _Loc("Repair 2")] if (kind, name) == ("group", "Repairs") else [],
    )
    monkeypatch.setattr(work_centers_store, "goal_per_day", lambda loc: 50)

    # Fake leaderboard payload — items keyed by wc name with units.
    class _StationTotal:
        def __init__(self, units): self.units = units

    monkeypatch.setattr(
        widget_data, "_pallets_units_for_wc",
        lambda wc_name, day: {"Repair 1": 42, "Repair 2": 18}.get(wc_name, 0),
    )

    out = widget_data._resolve_pallets_by_wc(
        {"group": "Repairs"}, day=date(2026, 5, 13),
    )
    assert isinstance(out, dict)
    items = out["items"]
    assert {i["name"] for i in items} == {"Repair 1", "Repair 2"}
    total = sum(i["units"] for i in items)
    assert out["total_u"] == total


def test_resolve_pallets_by_wc_missing_group_returns_empty():
    from zira_dashboard import widget_data
    out = widget_data._resolve_pallets_by_wc({}, day=date(2026, 5, 13))
    assert out == {"items": [], "total_u": 0, "total_e": 0}


def test_resolve_goat_race_with_goat(monkeypatch):
    from zira_dashboard import widget_data, awards

    monkeypatch.setattr(
        awards, "goat",
        lambda group_name: {"name": "Alice", "units": 100, "day": "2025-03-15"} if group_name == "Repairs" else None,
    )
    monkeypatch.setattr(
        widget_data, "_units_today_for_group",
        lambda group, day: 60,
    )
    monkeypatch.setattr(widget_data, "_elapsed_fraction", lambda day: 0.5)

    out = widget_data._resolve_goat_race(
        {"group": "Repairs"}, day=date(2026, 5, 13),
    )
    assert out["group"] == "Repairs"
    assert out["goat"]["name"] == "Alice"
    # Pace today = 100 * 0.5 = 50. Units = 60 → AHEAD (delta > 5%).
    assert out["units_today"] == 60
    assert out["goat_pace_today"] == 50
    assert out["status"] == "AHEAD"


def test_resolve_goat_race_no_goat_yet(monkeypatch):
    from zira_dashboard import widget_data, awards

    monkeypatch.setattr(awards, "goat", lambda group_name: None)
    monkeypatch.setattr(widget_data, "_units_today_for_group", lambda g, d: 30)

    out = widget_data._resolve_goat_race(
        {"group": "Repairs"}, day=date(2026, 5, 13),
    )
    assert out["status"] is None
    assert out["goat"] is None
    assert out["units_today"] == 30


def test_resolve_goat_race_missing_group_returns_empty():
    from zira_dashboard import widget_data
    out = widget_data._resolve_goat_race({}, day=date(2026, 5, 13))
    assert out["group"] is None
    assert out["status"] is None


def test_resolve_ribbons_returns_entries(monkeypatch):
    from zira_dashboard import widget_data, awards

    monkeypatch.setattr(
        awards, "monthly_badges",
        lambda group, year, month: [
            {"position": 1, "name": "Alice", "units": 90},
            {"position": 2, "name": "Bob",   "units": 80},
            {"position": 3, "name": "Carol", "units": 70},
        ] if group == "Repairs" else [],
    )
    out = widget_data._resolve_ribbons(
        {"group": "Repairs"}, day=date(2026, 5, 13),
    )
    assert out["group"] == "Repairs"
    assert len(out["entries"]) == 3
    assert out["entries"][0]["name"] == "Alice"


def test_resolve_ribbons_missing_group_returns_empty():
    from zira_dashboard import widget_data
    out = widget_data._resolve_ribbons({}, day=date(2026, 5, 13))
    assert out == {"group": None, "entries": []}
```

- [ ] **Step 3: Run tests to confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_widget_types.py tests/test_widget_data.py -v 2>&1 | tail -20`
Expected: ImportError / module-not-found until modules are created.

- [ ] **Step 4: Create `src/zira_dashboard/widget_types.py`**

```python
"""Widget type registry — Phase 1 (3 types).

Each registered type carries:
  - data_params_schema: list of fields the placement provides to the
    resolver (e.g. group name, WC name).
  - visual_params_schema: list of fields the workshop offers for the
    visual preset (color, sort order, etc.).
  - resolver: name of the function in `widget_data` to call.
  - partial: Jinja partial relative to the templates dir.

Adding a new type later: append a dict here, add the resolver function
to widget_data.py, drop the partial under templates/widgets/.
"""
from __future__ import annotations


_REGISTRY: list[dict] = [
    {
        "type": "pallets_by_wc",
        "label": "Pallets by Work Center",
        "data_params_schema": [
            {"key": "group", "label": "Group", "input": "select",
             "options_from": "groups", "required": True},
        ],
        "visual_params_schema": [
            {"key": "color", "label": "Bar color", "input": "color", "default": "#22c55e"},
            {"key": "sort", "label": "Sort order", "input": "select",
             "options": [
                 {"value": "preset", "label": "By preset order"},
                 {"value": "desc",   "label": "Most pallets first"},
                 {"value": "asc",    "label": "Fewest pallets first"},
                 {"value": "alpha",  "label": "Alphabetical"},
             ],
             "default": "preset"},
            {"key": "number_position", "label": "Number position", "input": "select",
             "options": [
                 {"value": "widget", "label": "Right of bar"},
                 {"value": "bar",    "label": "End of bar"},
                 {"value": "inside", "label": "Inside bar"},
                 {"value": "hidden", "label": "Hidden"},
             ],
             "default": "widget"},
        ],
        "resolver": "_resolve_pallets_by_wc",
        "partial": "widgets/_widget_pallets_by_wc.html",
    },
    {
        "type": "goat_race",
        "label": "Vs. Goat Pace",
        "data_params_schema": [
            {"key": "group", "label": "Group", "input": "select",
             "options_from": "groups", "required": True},
        ],
        "visual_params_schema": [
            {"key": "color", "label": "Accent color", "input": "color", "default": "#22c55e"},
        ],
        "resolver": "_resolve_goat_race",
        "partial": "widgets/_widget_goat_race.html",
    },
    {
        "type": "ribbons",
        "label": "Monthly Ribbons",
        "data_params_schema": [
            {"key": "group", "label": "Group", "input": "select",
             "options_from": "groups", "required": True},
        ],
        "visual_params_schema": [],
        "resolver": "_resolve_ribbons",
        "partial": "widgets/_widget_ribbons.html",
    },
]


def all_types() -> list[dict]:
    return list(_REGISTRY)


def get(type_id: str) -> dict | None:
    for entry in _REGISTRY:
        if entry["type"] == type_id:
            return entry
    return None
```

- [ ] **Step 5: Create `src/zira_dashboard/widget_data.py`**

```python
"""Data resolvers for the widget type registry.

Each resolver takes `params: dict` (the merged definition.default_data +
placement.data_overrides) and a `day: date`. Returns a dict the type's
Jinja partial consumes.

Resolvers must be robust to missing params — return an empty-state dict
rather than raising. The render layer treats empty data as a graceful
"no data yet" rather than an error.
"""
from __future__ import annotations

from datetime import date
from typing import Optional


def _elapsed_fraction(day: date) -> float:
    """Wrap the existing shift-elapsed-fraction helper so tests can monkeypatch."""
    from .wc_dashboard_data import _shift_elapsed_fraction
    return _shift_elapsed_fraction(day)


def _pallets_units_for_wc(wc_name: str, day: date) -> int:
    """Today's units for one WC. Wraps the existing helper so tests can monkeypatch."""
    from .wc_dashboard_data import _units_today_for_wc
    return _units_today_for_wc(wc_name, day)


def _units_today_for_group(group_name: str, day: date) -> int:
    """Sum of today's units across every WC in `group_name`."""
    from . import work_centers_store
    total = 0
    for loc in work_centers_store.members("group", group_name):
        total += _pallets_units_for_wc(loc.name, day)
    return total


def _resolve_pallets_by_wc(params: dict, day: date) -> dict:
    """Horizontal bar chart, one bar per WC in the group.

    Returns: {items: [{name, units, expected, pct, target_pct}, ...], total_u, total_e}.
    """
    from . import work_centers_store
    group = (params or {}).get("group")
    if not group:
        return {"items": [], "total_u": 0, "total_e": 0}
    members = work_centers_store.members("group", group) or []
    if not members:
        return {"items": [], "total_u": 0, "total_e": 0}
    frac = _elapsed_fraction(day)
    items: list[dict] = []
    total_u = 0
    total_e = 0
    max_scale = 0
    for loc in members:
        units = _pallets_units_for_wc(loc.name, day)
        full = int(work_centers_store.goal_per_day(loc) or 0)
        expected = full * frac
        total_u += units
        total_e += int(expected)
        scale_target = max(units, expected, full)
        if scale_target > max_scale:
            max_scale = scale_target
        items.append({
            "name": loc.name,
            "units": units,
            "expected": int(expected),
            "full_day_target": full,
        })
    # Second pass: compute percent fields once the scale is known.
    for it in items:
        scale = max_scale if max_scale > 0 else 1
        it["pct"] = (it["units"] / scale * 100.0) if scale else 0.0
        it["target_pct"] = (it["expected"] / scale * 100.0) if scale else None
    return {"items": items, "total_u": total_u, "total_e": total_e}


def _resolve_goat_race(params: dict, day: date) -> dict:
    """Vs. Goat Pace widget — status + race stats vs the group's GOAT,
    prorated by elapsed shift fraction.

    Returns the same shape `wc_dashboard_data.goat_race` returns.
    """
    from . import awards
    group = (params or {}).get("group")
    if not group:
        return {
            "group": None, "goat": None, "units_today": 0,
            "goat_pace_today": 0, "status": None,
        }
    goat = awards.goat(group)
    units = _units_today_for_group(group, day)
    if goat is None:
        return {
            "group": group, "goat": None, "units_today": units,
            "goat_pace_today": 0, "status": None,
        }
    frac = _elapsed_fraction(day)
    pace_today = float(goat.get("units", 0)) * frac
    if pace_today <= 0:
        status: Optional[str] = None
    else:
        delta_pct = (units - pace_today) / pace_today * 100.0
        if delta_pct > 5:
            status = "AHEAD"
        elif delta_pct < -5:
            status = "BEHIND"
        else:
            status = "ON_PACE"
    return {
        "group": group, "goat": goat, "units_today": units,
        "goat_pace_today": pace_today, "status": status,
    }


def _resolve_ribbons(params: dict, day: date) -> dict:
    """Top-3 person-days for the group this month."""
    from . import awards
    group = (params or {}).get("group")
    if not group:
        return {"group": None, "entries": []}
    entries = awards.monthly_badges(group, day.year, day.month) or []
    return {"group": group, "entries": entries}
```

- [ ] **Step 6: Run unit tests**

Note: `test_partial_paths_point_to_existing_files` will FAIL until Task 5 creates the three Jinja partials. Skip it for now or expect failure.

Run: `.venv/Scripts/python.exe -m pytest tests/test_widget_types.py tests/test_widget_data.py -v 2>&1 | tail -20`
Expected: all tests PASS except `test_partial_paths_point_to_existing_files` (xfail — partials come in Task 5).

- [ ] **Step 7: Mark the partial-path test as expected-to-fail until Task 5**

In `tests/test_widget_types.py`, decorate the partial test:

```python
import pytest


@pytest.mark.xfail(reason="partials are created in Task 5; this becomes a real assertion then")
def test_partial_paths_point_to_existing_files():
    ...
```

Run again: `.venv/Scripts/python.exe -m pytest tests/test_widget_types.py tests/test_widget_data.py -v 2>&1 | tail -10`
Expected: all green (xfail counts as success).

- [ ] **Step 8: Commit**

```
git add src/zira_dashboard/widget_types.py src/zira_dashboard/widget_data.py tests/test_widget_types.py tests/test_widget_data.py
git commit -m "$(cat <<'EOF'
feat(widgets): widget type registry + 3 resolvers (pallets-by-wc, goat-race, ribbons)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `widget_definitions_store.py`

**Files:**
- Create: `src/zira_dashboard/widget_definitions_store.py`
- Test: `tests/test_widget_definitions_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_widget_definitions_store.py`:

```python
"""Postgres-gated tests for widget_definitions_store."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="widget_definitions_store tests need Postgres",
)


@pytest.fixture(autouse=True)
def _clean():
    from zira_dashboard import db
    db.init_pool()
    db.bootstrap_schema()
    db.execute("DELETE FROM widget_definitions WHERE name LIKE 'wt-%'")
    yield
    db.execute("DELETE FROM widget_definitions WHERE name LIKE 'wt-%'")


def test_save_inserts_and_returns_row():
    from zira_dashboard import widget_definitions_store
    row = widget_definitions_store.save(
        name="wt-pallets", type="pallets_by_wc",
        visual={"color": "#22c55e", "sort": "desc"},
        default_data={"group": "Repairs"},
    )
    assert isinstance(row["id"], int)
    assert row["name"] == "wt-pallets"
    assert row["type"] == "pallets_by_wc"
    assert row["visual"] == {"color": "#22c55e", "sort": "desc"}
    assert row["default_data"] == {"group": "Repairs"}


def test_save_with_id_updates():
    from zira_dashboard import widget_definitions_store
    row = widget_definitions_store.save(
        name="wt-edit", type="goat_race", visual={}, default_data={"group": "Repairs"},
    )
    updated = widget_definitions_store.save(
        name="wt-edit-renamed", type="goat_race", visual={"color": "#ff0000"},
        default_data={"group": "Dismantlers"}, id=row["id"],
    )
    assert updated["id"] == row["id"]
    assert updated["name"] == "wt-edit-renamed"
    assert updated["visual"] == {"color": "#ff0000"}
    assert updated["default_data"] == {"group": "Dismantlers"}


def test_get_returns_row_or_none():
    from zira_dashboard import widget_definitions_store
    assert widget_definitions_store.get(999_999_999) is None
    row = widget_definitions_store.save(
        name="wt-get", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    fetched = widget_definitions_store.get(row["id"])
    assert fetched["id"] == row["id"]
    assert fetched["name"] == "wt-get"


def test_list_definitions_ordered_by_type_then_name():
    from zira_dashboard import widget_definitions_store
    widget_definitions_store.save(name="wt-z-pal", type="pallets_by_wc", visual={}, default_data={"group": "Repairs"})
    widget_definitions_store.save(name="wt-a-rib", type="ribbons", visual={}, default_data={"group": "Repairs"})
    widget_definitions_store.save(name="wt-a-pal", type="pallets_by_wc", visual={}, default_data={"group": "Repairs"})
    rows = [r for r in widget_definitions_store.list_definitions() if r["name"].startswith("wt-")]
    # Order: by type alphabetical, then name alphabetical.
    types_in_order = [r["type"] for r in rows]
    # pallets_by_wc < ribbons
    assert types_in_order[0] == "pallets_by_wc"
    # Within pallets_by_wc: wt-a-pal before wt-z-pal
    names = [r["name"] for r in rows if r["type"] == "pallets_by_wc"]
    assert names == sorted(names, key=str.lower)


def test_delete_removes_row():
    from zira_dashboard import widget_definitions_store
    row = widget_definitions_store.save(
        name="wt-del", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    widget_definitions_store.delete(row["id"])
    assert widget_definitions_store.get(row["id"]) is None


def test_delete_raises_when_referenced():
    """Cannot delete a definition that any dashboard_widgets row references."""
    from zira_dashboard import widget_definitions_store, custom_dashboards_store, db
    # Create definition + dashboard + placement.
    wd = widget_definitions_store.save(
        name="wt-referenced", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    dash = custom_dashboards_store.save_dashboard(
        name="wt-host-dash", scope_kind="group", scope_value="Repairs", theme="dark",
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"],
        x=0, y=0, w=4, h=4, data_overrides={},
    )
    with pytest.raises(Exception):
        widget_definitions_store.delete(wd["id"])
    # Cleanup
    db.execute("DELETE FROM custom_dashboards WHERE slug LIKE 'wt-%'")


def test_usage_count():
    from zira_dashboard import widget_definitions_store, custom_dashboards_store, db
    wd = widget_definitions_store.save(
        name="wt-usage", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    assert widget_definitions_store.usage_count(wd["id"]) == 0
    dash = custom_dashboards_store.save_dashboard(
        name="wt-usage-dash", scope_kind="group", scope_value="Repairs", theme="dark",
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=4, y=0, w=4, h=4, data_overrides={},
    )
    assert widget_definitions_store.usage_count(wd["id"]) == 2
    db.execute("DELETE FROM custom_dashboards WHERE slug LIKE 'wt-%'")
```

- [ ] **Step 2: Run to confirm fail/skip**

Run: `.venv/Scripts/python.exe -m pytest tests/test_widget_definitions_store.py -v 2>&1 | tail -15`
Expected: SKIP without `DATABASE_URL`, ModuleNotFoundError with one.

- [ ] **Step 3: Create `src/zira_dashboard/widget_definitions_store.py`**

```python
"""Persistence layer for widget definitions (workshop presets).

Each definition has a type (one of the registry slugs), a visual config
JSON, and a default data scope JSON. Deletion is blocked while any
`dashboard_widgets` row references the row — caller should check
`usage_count` first and ask the user to remove placements.
"""
from __future__ import annotations

import json
from typing import Optional


def save(
    *,
    name: str,
    type: str,
    visual: dict,
    default_data: dict,
    id: Optional[int] = None,
) -> dict:
    """Insert or update a definition. Returns the saved row as a dict."""
    from . import db
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name required")
    if not isinstance(type, str) or not type.strip():
        raise ValueError("type required")
    visual = visual or {}
    default_data = default_data or {}
    if id is None:
        rows = db.query(
            "INSERT INTO widget_definitions (name, type, visual_json, default_data_json) "
            "VALUES (%s, %s, %s::jsonb, %s::jsonb) "
            "RETURNING id, name, type, visual_json, default_data_json",
            (name.strip(), type.strip(), json.dumps(visual), json.dumps(default_data)),
        )
    else:
        rows = db.query(
            "UPDATE widget_definitions SET "
            "  name = %s, type = %s, visual_json = %s::jsonb, "
            "  default_data_json = %s::jsonb, updated_at = now() "
            "WHERE id = %s "
            "RETURNING id, name, type, visual_json, default_data_json",
            (name.strip(), type.strip(), json.dumps(visual), json.dumps(default_data), id),
        )
    if not rows:
        raise LookupError(f"no widget_definitions row with id={id}")
    return _hydrate(rows[0])


def get(id: int) -> Optional[dict]:
    from . import db
    rows = db.query(
        "SELECT id, name, type, visual_json, default_data_json "
        "FROM widget_definitions WHERE id = %s",
        (id,),
    )
    return _hydrate(rows[0]) if rows else None


def list_definitions() -> list[dict]:
    from . import db
    rows = db.query(
        "SELECT id, name, type, visual_json, default_data_json "
        "FROM widget_definitions ORDER BY type, lower(name)"
    )
    return [_hydrate(r) for r in rows]


def delete(id: int) -> None:
    """Hard-delete a definition. Postgres FK ON DELETE RESTRICT raises if
    any dashboard_widgets row references it — caller is expected to have
    called `usage_count` first."""
    from . import db
    db.execute("DELETE FROM widget_definitions WHERE id = %s", (id,))


def usage_count(id: int) -> int:
    from . import db
    rows = db.query(
        "SELECT COUNT(*) AS n FROM dashboard_widgets WHERE widget_def_id = %s",
        (id,),
    )
    return int(rows[0]["n"]) if rows else 0


def _hydrate(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "type": row["type"],
        "visual": _decode(row["visual_json"]),
        "default_data": _decode(row["default_data_json"]),
    }


def _decode(raw) -> dict:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}
```

- [ ] **Step 4: Run tests**

Note: `test_delete_raises_when_referenced` and `test_usage_count` depend on `custom_dashboards_store` (Task 4). They'll fail until that's done — they're forward references.

Run: `.venv/Scripts/python.exe -m pytest tests/test_widget_definitions_store.py -v 2>&1 | tail -15`
Expected: tests touching only `widget_definitions_store` PASS; `test_delete_raises_when_referenced` + `test_usage_count` fail because they import `custom_dashboards_store`. That's expected — Task 4 fixes them.

- [ ] **Step 5: Commit**

```
git add src/zira_dashboard/widget_definitions_store.py tests/test_widget_definitions_store.py
git commit -m "$(cat <<'EOF'
feat(widgets): widget_definitions_store — save / get / list / delete / usage_count

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `custom_dashboards_store.py`

**Files:**
- Create: `src/zira_dashboard/custom_dashboards_store.py`
- Test: `tests/test_custom_dashboards_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_custom_dashboards_store.py`:

```python
"""Postgres-gated tests for custom_dashboards_store."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="custom_dashboards_store tests need Postgres",
)


@pytest.fixture(autouse=True)
def _clean():
    from zira_dashboard import db
    db.init_pool()
    db.bootstrap_schema()
    db.execute("DELETE FROM custom_dashboards WHERE slug LIKE 'cdt-%'")
    db.execute("DELETE FROM widget_definitions WHERE name LIKE 'cdt-%'")
    yield
    db.execute("DELETE FROM custom_dashboards WHERE slug LIKE 'cdt-%'")
    db.execute("DELETE FROM widget_definitions WHERE name LIKE 'cdt-%'")


def test_save_dashboard_returns_slug():
    from zira_dashboard import custom_dashboards_store
    row = custom_dashboards_store.save_dashboard(
        name="cdt-repair-1", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    assert row["slug"] == "cdt-repair-1"
    assert row["scope_kind"] == "wc"
    assert row["scope_value"] == "Repair 1"


def test_save_dashboard_slug_collision_suffix():
    from zira_dashboard import custom_dashboards_store
    a = custom_dashboards_store.save_dashboard(
        name="cdt-clash", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    b = custom_dashboards_store.save_dashboard(
        name="cdt-clash", scope_kind="wc", scope_value="Repair 2", theme="dark",
    )
    assert a["slug"] == "cdt-clash"
    assert b["slug"] == "cdt-clash-2"


def test_save_dashboard_with_id_updates():
    from zira_dashboard import custom_dashboards_store
    row = custom_dashboards_store.save_dashboard(
        name="cdt-rename", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    updated = custom_dashboards_store.save_dashboard(
        name="cdt-renamed", scope_kind="group", scope_value="Repairs", theme="light",
        id=row["id"],
    )
    assert updated["id"] == row["id"]
    assert updated["name"] == "cdt-renamed"
    assert updated["slug"] == "cdt-renamed"
    assert updated["scope_kind"] == "group"
    assert updated["theme"] == "light"


def test_get_dashboard_by_id_and_slug():
    from zira_dashboard import custom_dashboards_store
    row = custom_dashboards_store.save_dashboard(
        name="cdt-fetch", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    by_id = custom_dashboards_store.get_dashboard(row["id"])
    by_slug = custom_dashboards_store.get_dashboard("cdt-fetch")
    assert by_id["id"] == row["id"]
    assert by_slug["id"] == row["id"]


def test_list_dashboards_includes_widget_count():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-list", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    rows = [r for r in custom_dashboards_store.list_dashboards() if r["slug"].startswith("cdt-")]
    target = next(r for r in rows if r["slug"] == "cdt-list")
    assert target["widget_count"] == 1


def test_delete_dashboard_cascades_placements():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store, db
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-cascade", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-cwd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    custom_dashboards_store.delete_dashboard(dash["id"])
    # Placement should be gone too.
    rows = db.query(
        "SELECT COUNT(*) AS n FROM dashboard_widgets WHERE dashboard_id = %s",
        (dash["id"],),
    )
    assert int(rows[0]["n"]) == 0


def test_add_placement_returns_row():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-add", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-add-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    p = custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"],
        x=0, y=0, w=6, h=4, data_overrides={"group": "Dismantlers"},
    )
    assert isinstance(p["id"], int)
    assert p["x"] == 0
    assert p["w"] == 6
    assert p["data_overrides"] == {"group": "Dismantlers"}


def test_list_placements_joins_definition():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-listp", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-listp-wd", type="goat_race", visual={"color": "#22c55e"},
        default_data={"group": "Repairs"},
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    placements = custom_dashboards_store.list_placements(dash["id"])
    assert len(placements) == 1
    p = placements[0]
    # Joined fields from widget_definitions
    assert p["type"] == "goat_race"
    assert p["name"] == "cdt-listp-wd"
    assert p["visual"] == {"color": "#22c55e"}
    assert p["default_data"] == {"group": "Repairs"}
    # Placement fields
    assert p["x"] == 0


def test_update_placement_changes_position():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-update", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-update-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    p = custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    custom_dashboards_store.update_placement(p["id"], x=2, y=3, w=8, h=6)
    refreshed = custom_dashboards_store.list_placements(dash["id"])[0]
    assert refreshed["x"] == 2 and refreshed["y"] == 3
    assert refreshed["w"] == 8 and refreshed["h"] == 6


def test_update_placement_changes_overrides():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-ovr", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-ovr-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    p = custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    custom_dashboards_store.update_placement(p["id"], data_overrides={"group": "Dismantlers"})
    refreshed = custom_dashboards_store.list_placements(dash["id"])[0]
    assert refreshed["data_overrides"] == {"group": "Dismantlers"}


def test_delete_placement():
    from zira_dashboard import custom_dashboards_store, widget_definitions_store
    dash = custom_dashboards_store.save_dashboard(
        name="cdt-delp", scope_kind="wc", scope_value="Repair 1", theme="dark",
    )
    wd = widget_definitions_store.save(
        name="cdt-delp-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    p = custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=wd["id"], x=0, y=0, w=4, h=4, data_overrides={},
    )
    custom_dashboards_store.delete_placement(p["id"])
    assert custom_dashboards_store.list_placements(dash["id"]) == []
```

- [ ] **Step 2: Run to confirm fail/skip**

Run: `.venv/Scripts/python.exe -m pytest tests/test_custom_dashboards_store.py -v 2>&1 | tail -15`
Expected: SKIP without `DATABASE_URL`, ModuleNotFoundError with one.

- [ ] **Step 3: Create `src/zira_dashboard/custom_dashboards_store.py`**

```python
"""Persistence layer for custom dashboards + their widget placements.

`custom_dashboards` holds the dashboard meta (name, slug, scope, theme).
`dashboard_widgets` holds placements (which widget def is on which
dashboard, where, with what data overrides).

Slug derivation reuses `wc_dashboard_data.slug_for_wc`. Collision
suffix follows the same pattern as `tv_displays_store`.
"""
from __future__ import annotations

import json
from typing import Optional, Union

from .wc_dashboard_data import slug_for_wc


def _unique_slug(base: str, *, exclude_id: Optional[int] = None) -> str:
    from . import db
    candidate = base
    n = 2
    while True:
        rows = db.query(
            "SELECT id FROM custom_dashboards WHERE slug = %s",
            (candidate,),
        )
        if not rows or (exclude_id is not None and all(r["id"] == exclude_id for r in rows)):
            return candidate
        candidate = f"{base}-{n}"
        n += 1


def save_dashboard(
    *,
    name: str,
    scope_kind: str,
    scope_value: str,
    theme: str,
    id: Optional[int] = None,
) -> dict:
    from . import db
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name required")
    if scope_kind not in ("wc", "group"):
        raise ValueError(f"invalid scope_kind: {scope_kind}")
    if not isinstance(scope_value, str) or not scope_value.strip():
        raise ValueError("scope_value required")
    if theme not in ("light", "dark"):
        theme = "dark"
    slug_base = slug_for_wc(name)
    if not slug_base:
        raise ValueError("name must produce a non-empty slug")
    slug = _unique_slug(slug_base, exclude_id=id)
    if id is None:
        rows = db.query(
            "INSERT INTO custom_dashboards "
            "  (name, slug, scope_kind, scope_value, theme) "
            "VALUES (%s, %s, %s, %s, %s) "
            "RETURNING id, name, slug, scope_kind, scope_value, theme, sort_order",
            (name.strip(), slug, scope_kind, scope_value.strip(), theme),
        )
    else:
        rows = db.query(
            "UPDATE custom_dashboards SET "
            "  name = %s, slug = %s, scope_kind = %s, scope_value = %s, "
            "  theme = %s, updated_at = now() "
            "WHERE id = %s "
            "RETURNING id, name, slug, scope_kind, scope_value, theme, sort_order",
            (name.strip(), slug, scope_kind, scope_value.strip(), theme, id),
        )
    if not rows:
        raise LookupError(f"no custom_dashboards row with id={id}")
    return _hydrate_dashboard(rows[0])


def get_dashboard(id_or_slug: Union[int, str]) -> Optional[dict]:
    from . import db
    if isinstance(id_or_slug, int):
        rows = db.query(
            "SELECT id, name, slug, scope_kind, scope_value, theme, sort_order "
            "FROM custom_dashboards WHERE id = %s",
            (id_or_slug,),
        )
    else:
        rows = db.query(
            "SELECT id, name, slug, scope_kind, scope_value, theme, sort_order "
            "FROM custom_dashboards WHERE slug = %s",
            (id_or_slug,),
        )
    return _hydrate_dashboard(rows[0]) if rows else None


def list_dashboards() -> list[dict]:
    """All dashboards with `widget_count` precomputed via subquery."""
    from . import db
    rows = db.query(
        "SELECT d.id, d.name, d.slug, d.scope_kind, d.scope_value, d.theme, d.sort_order, "
        "  COALESCE(c.n, 0) AS widget_count "
        "FROM custom_dashboards d "
        "LEFT JOIN ("
        "  SELECT dashboard_id, COUNT(*) AS n "
        "  FROM dashboard_widgets GROUP BY dashboard_id"
        ") c ON c.dashboard_id = d.id "
        "ORDER BY d.sort_order, lower(d.name)"
    )
    out = []
    for r in rows:
        d = _hydrate_dashboard(r)
        d["widget_count"] = int(r["widget_count"])
        out.append(d)
    return out


def delete_dashboard(id: int) -> None:
    from . import db
    db.execute("DELETE FROM custom_dashboards WHERE id = %s", (id,))


def add_placement(
    *,
    dashboard_id: int,
    widget_def_id: int,
    x: int, y: int, w: int, h: int,
    data_overrides: dict,
) -> dict:
    from . import db
    data_overrides = data_overrides or {}
    rows = db.query(
        "INSERT INTO dashboard_widgets "
        "  (dashboard_id, widget_def_id, x, y, w, h, data_overrides_json) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb) "
        "RETURNING id, dashboard_id, widget_def_id, x, y, w, h, data_overrides_json",
        (dashboard_id, widget_def_id, x, y, w, h, json.dumps(data_overrides)),
    )
    return _hydrate_placement(rows[0])


def update_placement(
    id: int,
    *,
    x: Optional[int] = None,
    y: Optional[int] = None,
    w: Optional[int] = None,
    h: Optional[int] = None,
    data_overrides: Optional[dict] = None,
) -> None:
    """Update a placement. Only the fields you pass are touched."""
    from . import db
    sets: list[str] = []
    params: list = []
    if x is not None: sets.append("x = %s"); params.append(x)
    if y is not None: sets.append("y = %s"); params.append(y)
    if w is not None: sets.append("w = %s"); params.append(w)
    if h is not None: sets.append("h = %s"); params.append(h)
    if data_overrides is not None:
        sets.append("data_overrides_json = %s::jsonb")
        params.append(json.dumps(data_overrides))
    if not sets:
        return
    params.append(id)
    db.execute(
        f"UPDATE dashboard_widgets SET {', '.join(sets)} WHERE id = %s",
        tuple(params),
    )


def delete_placement(id: int) -> None:
    from . import db
    db.execute("DELETE FROM dashboard_widgets WHERE id = %s", (id,))


def list_placements(dashboard_id: int) -> list[dict]:
    """Placements for one dashboard, joined with their widget definition.

    Each row carries the placement (x/y/w/h, data_overrides, id) AND the
    definition (name, type, visual, default_data) so the template can
    render without a second query per widget.
    """
    from . import db
    rows = db.query(
        "SELECT dw.id, dw.dashboard_id, dw.widget_def_id, "
        "  dw.x, dw.y, dw.w, dw.h, dw.data_overrides_json, "
        "  wd.name, wd.type, wd.visual_json, wd.default_data_json "
        "FROM dashboard_widgets dw "
        "JOIN widget_definitions wd ON wd.id = dw.widget_def_id "
        "WHERE dw.dashboard_id = %s "
        "ORDER BY dw.id",
        (dashboard_id,),
    )
    out = []
    for r in rows:
        p = _hydrate_placement(r)
        p["name"] = r["name"]
        p["type"] = r["type"]
        p["visual"] = _decode(r["visual_json"])
        p["default_data"] = _decode(r["default_data_json"])
        # Effective data params = defaults merged with overrides.
        merged = dict(p["default_data"])
        merged.update(p["data_overrides"])
        p["effective_data"] = merged
        out.append(p)
    return out


def _hydrate_dashboard(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "slug": row["slug"],
        "scope_kind": row["scope_kind"],
        "scope_value": row["scope_value"],
        "theme": row["theme"],
        "sort_order": int(row["sort_order"]),
    }


def _hydrate_placement(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "dashboard_id": int(row["dashboard_id"]),
        "widget_def_id": int(row["widget_def_id"]),
        "x": int(row["x"]),
        "y": int(row["y"]),
        "w": int(row["w"]),
        "h": int(row["h"]),
        "data_overrides": _decode(row["data_overrides_json"]),
    }


def _decode(raw) -> dict:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}
```

- [ ] **Step 4: Run tests for both stores**

Run: `.venv/Scripts/python.exe -m pytest tests/test_widget_definitions_store.py tests/test_custom_dashboards_store.py -v 2>&1 | tail -25`
Expected: PASS with `DATABASE_URL`, SKIP without. The two forward-reference tests from Task 3 (`test_delete_raises_when_referenced`, `test_usage_count`) now pass.

- [ ] **Step 5: Commit**

```
git add src/zira_dashboard/custom_dashboards_store.py tests/test_custom_dashboards_store.py
git commit -m "$(cat <<'EOF'
feat(widgets): custom_dashboards_store — dashboards + placements CRUD

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 3 widget Jinja partials + dispatcher

**Files:**
- Create: `src/zira_dashboard/templates/widgets/_widget_pallets_by_wc.html`
- Create: `src/zira_dashboard/templates/widgets/_widget_goat_race.html`
- Create: `src/zira_dashboard/templates/widgets/_widget_ribbons.html`
- Create: `src/zira_dashboard/templates/_widget_render.html`
- Modify: `tests/test_widget_types.py` — remove `xfail` mark from `test_partial_paths_point_to_existing_files`

- [ ] **Step 1: Create `templates/widgets/_widget_pallets_by_wc.html`**

```jinja
{# Pallets-by-WC bar chart. Expects:
   data = {items: [{name, units, expected, pct, target_pct, full_day_target}, ...], total_u, total_e}
   visual = {color, sort, number_position}
#}
<div class="grid-stack-item-content">
  <h3>{{ placement_title or 'Pallets by Work Center' }}</h3>
  <div class="pallets-by-wc">
    {% if data.items %}
      {% set numpos = visual.number_position or 'widget' %}
      {% set sort = visual.sort or 'preset' %}
      {% set fill_color = visual.color or 'var(--accent)' %}
      {% set sorted_items = data.items %}
      {% if sort == 'desc' %}
        {% set sorted_items = data.items | sort(attribute='units', reverse=True) %}
      {% elif sort == 'asc' %}
        {% set sorted_items = data.items | sort(attribute='units') %}
      {% elif sort == 'alpha' %}
        {% set sorted_items = data.items | sort(attribute='name') %}
      {% endif %}
      {% for b in sorted_items %}
        <div class="bar-row numpos-{{ numpos }}"
             title="{{ b.name }} — {{ b.units }} / {{ b.expected }} expected">
          <div class="name"><span class="name-primary">{{ b.name }}</span></div>
          <div class="bar-track">
            <div class="bar-fill" style="width: {{ b.pct }}%; background: {{ fill_color }}">
              {% if numpos == 'inside' %}<span class="in">{{ b.units }}</span>{% endif %}
              {% if numpos == 'bar' %}<span class="edge">{{ b.units }}</span>{% endif %}
            </div>
            {% if b.target_pct is not none %}
              <div class="bar-target-line" style="left: {{ b.target_pct }}%"></div>
            {% endif %}
          </div>
          {% if numpos == 'widget' %}
            <div class="val">{{ b.units }}<span class="pct">/{{ b.expected }}</span></div>
          {% endif %}
        </div>
      {% endfor %}
      <div class="widget-total">Total <b>{{ data.total_u }}</b> / {{ data.total_e }}</div>
    {% else %}
      <div class="empty-state">No data yet — pick a group with active WCs.</div>
    {% endif %}
  </div>
</div>
```

- [ ] **Step 2: Create `templates/widgets/_widget_goat_race.html`**

```jinja
{# Vs Goat Pace widget. Expects:
   data = {group, goat: {name, units, day} | None, units_today, goat_pace_today, status}
   visual = {color}
#}
<div class="grid-stack-item-content">
  <h3>{{ placement_title or 'Vs. Goat Pace' }}{% if data.group %} — {{ data.group }}{% endif %}</h3>
  <div class="goat-race">
    {% if data.status %}
      <div class="status-pill status-{{ data.status|lower }}">{{ data.status|replace('_', ' ') }}</div>
    {% else %}
      <div class="status-pill status-none">no record yet</div>
    {% endif %}
    <div class="race-stats">
      <div>Today: <b>{{ data.units_today }}</b></div>
      <div>GOAT pace now: <b>{{ data.goat_pace_today|round(0)|int }}</b></div>
      {% if data.goat %}
        <div class="goat-meta">🐐 {{ data.goat.name }} · {{ data.goat.units }} on {{ data.goat.day }}</div>
      {% endif %}
    </div>
  </div>
</div>
```

- [ ] **Step 3: Create `templates/widgets/_widget_ribbons.html`**

```jinja
{# Monthly Ribbons widget. Expects:
   data = {group, entries: [{position, name, units}, ...]}
#}
<div class="grid-stack-item-content">
  <h3>{{ placement_title or 'Monthly Ribbons' }}{% if data.group %} — {{ data.group }}{% endif %}</h3>
  <ul class="ribbons-list">
    {% for r in data.entries %}
      <li>
        <span class="medal">{% if r.position == 1 %}🥇{% elif r.position == 2 %}🥈{% else %}🥉{% endif %}</span>
        <span class="name"><a href="/staffing/people/{{ r.name|urlencode }}">{{ r.name }}</a></span>
        <span class="units">{{ r.units|round(0)|int }}</span>
      </li>
    {% else %}
      <li class="empty">no qualifying days yet</li>
    {% endfor %}
  </ul>
</div>
```

- [ ] **Step 4: Create `templates/_widget_render.html` (dispatcher)**

```jinja
{# Generic widget dispatcher. Switches on `placement.type` to load the
   per-type partial. Context every partial gets:
     placement   = the dashboard_widgets row joined with widget_definitions
     data        = output of the type's resolver
     visual      = placement.visual (from the definition)
     placement_title = placement.name (the definition's name)
#}
{% set visual = placement.visual %}
{% set placement_title = placement.name %}
{% if placement.type == 'pallets_by_wc' %}
  {% include "widgets/_widget_pallets_by_wc.html" %}
{% elif placement.type == 'goat_race' %}
  {% include "widgets/_widget_goat_race.html" %}
{% elif placement.type == 'ribbons' %}
  {% include "widgets/_widget_ribbons.html" %}
{% else %}
  <div class="grid-stack-item-content">
    <h3>Unknown widget type: {{ placement.type }}</h3>
    <p class="empty-state">This widget type isn't registered. Edit it in the workshop.</p>
  </div>
{% endif %}
```

- [ ] **Step 5: Remove the `xfail` mark from the partial-path test**

Open `tests/test_widget_types.py` and remove the `@pytest.mark.xfail(...)` decorator from `test_partial_paths_point_to_existing_files` so it becomes a real assertion.

- [ ] **Step 6: Verify partials parse + tests pass**

```
.venv/Scripts/python.exe -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'), autoescape=True); env.parse(open('src/zira_dashboard/templates/_widget_render.html', encoding='utf-8').read()); env.parse(open('src/zira_dashboard/templates/widgets/_widget_pallets_by_wc.html', encoding='utf-8').read()); env.parse(open('src/zira_dashboard/templates/widgets/_widget_goat_race.html', encoding='utf-8').read()); env.parse(open('src/zira_dashboard/templates/widgets/_widget_ribbons.html', encoding='utf-8').read()); print('parse OK')"
.venv/Scripts/python.exe -m pytest tests/test_widget_types.py -v 2>&1 | tail -10
```
Expected: `parse OK`; all type-registry tests PASS (no xfail).

- [ ] **Step 7: Commit**

```
git add src/zira_dashboard/templates/widgets/ src/zira_dashboard/templates/_widget_render.html tests/test_widget_types.py
git commit -m "$(cat <<'EOF'
feat(widgets): 3 widget partials + generic dispatcher

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Workshop routes + page

**Files:**
- Create: `src/zira_dashboard/routes/widgets.py`
- Create: `src/zira_dashboard/templates/widgets.html`
- Modify: `src/zira_dashboard/app.py` — register the router
- Test: `tests/test_widgets_routes.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_widgets_routes.py`:

```python
"""Integration tests for the Widget Workshop routes."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from zira_dashboard.app import app

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="widget routes need Postgres",
)


@pytest.fixture(autouse=True)
def _clean():
    from zira_dashboard import db
    db.init_pool()
    db.bootstrap_schema()
    db.execute("DELETE FROM widget_definitions WHERE name LIKE 'wr-%'")
    yield
    db.execute("DELETE FROM widget_definitions WHERE name LIKE 'wr-%'")


def test_get_widgets_types_returns_registry():
    c = TestClient(app)
    r = c.get("/api/widgets/types")
    assert r.status_code == 200
    body = r.json()
    type_ids = {t["type"] for t in body["types"]}
    assert {"pallets_by_wc", "goat_race", "ribbons"}.issubset(type_ids)


def test_get_widgets_options_groups():
    c = TestClient(app)
    r = c.get("/api/widgets/options/groups")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["options"], list)


def test_get_widgets_options_unknown_kind_returns_400():
    c = TestClient(app)
    r = c.get("/api/widgets/options/garbage")
    assert r.status_code == 400


def test_post_widget_def_creates():
    c = TestClient(app)
    r = c.post("/api/widget-defs", json={
        "name": "wr-create",
        "type": "ribbons",
        "visual": {},
        "default_data": {"group": "Repairs"},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["definition"]["name"] == "wr-create"


def test_post_widget_def_rejects_unknown_type():
    c = TestClient(app)
    r = c.post("/api/widget-defs", json={
        "name": "wr-bad-type", "type": "nope", "visual": {}, "default_data": {},
    })
    assert r.status_code == 400


def test_get_widget_defs_lists_them():
    c = TestClient(app)
    c.post("/api/widget-defs", json={
        "name": "wr-list", "type": "ribbons", "visual": {}, "default_data": {"group": "Repairs"},
    })
    r = c.get("/api/widget-defs")
    assert r.status_code == 200
    names = [d["name"] for d in r.json()["definitions"]]
    assert "wr-list" in names


def test_delete_widget_def_removes():
    c = TestClient(app)
    add = c.post("/api/widget-defs", json={
        "name": "wr-del", "type": "ribbons", "visual": {}, "default_data": {"group": "Repairs"},
    }).json()
    r = c.delete(f"/api/widget-defs/{add['definition']['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_widget_def_409_when_in_use():
    """Deletion blocked while any placement references the def."""
    from zira_dashboard import custom_dashboards_store, db
    c = TestClient(app)
    add = c.post("/api/widget-defs", json={
        "name": "wr-inuse", "type": "ribbons", "visual": {}, "default_data": {"group": "Repairs"},
    }).json()
    dash = custom_dashboards_store.save_dashboard(
        name="wr-host", scope_kind="group", scope_value="Repairs", theme="dark",
    )
    custom_dashboards_store.add_placement(
        dashboard_id=dash["id"], widget_def_id=add["definition"]["id"],
        x=0, y=0, w=4, h=4, data_overrides={},
    )
    r = c.delete(f"/api/widget-defs/{add['definition']['id']}")
    assert r.status_code == 409
    assert "in use" in r.text.lower() or "referenced" in r.text.lower()
    db.execute("DELETE FROM custom_dashboards WHERE slug LIKE 'wr-%'")


def test_get_widgets_page_renders():
    c = TestClient(app)
    r = c.get("/widgets")
    assert r.status_code == 200
    assert "Workshop" in r.text or "Widgets" in r.text
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_widgets_routes.py -v 2>&1 | tail -15`
Expected: SKIP without DB; 404s with DB until the routes exist.

- [ ] **Step 3: Create `src/zira_dashboard/routes/widgets.py`**

```python
"""Widget Workshop routes.

Pages:
  GET  /widgets                      workshop UI

API:
  GET    /api/widgets/types          type registry (read-only)
  GET    /api/widgets/options/{kind} resolve options_from at render time
  GET    /api/widget-defs            list all definitions
  POST   /api/widget-defs            create or update (body {id?, name, type, visual, default_data})
  DELETE /api/widget-defs/{id}       delete (409 if in use)
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import widget_definitions_store, widget_types
from ..deps import templates

router = APIRouter()


@router.get("/widgets", response_class=HTMLResponse)
def widgets_page(request: Request):
    return templates.TemplateResponse(
        request, "widgets.html",
        {
            "definitions": widget_definitions_store.list_definitions(),
            "types": widget_types.all_types(),
        },
    )


@router.get("/api/widgets/types")
def get_types():
    return JSONResponse({"types": widget_types.all_types()})


@router.get("/api/widgets/options/{kind}")
def get_options(kind: str):
    if kind == "groups":
        from .. import work_centers_store
        return JSONResponse({"options": [
            {"value": g, "label": g} for g in work_centers_store.all_group_names("group")
        ]})
    if kind == "value_streams":
        from .. import work_centers_store
        return JSONResponse({"options": [
            {"value": g, "label": g} for g in work_centers_store.all_group_names("value_stream")
        ]})
    if kind == "wcs":
        from .. import staffing
        return JSONResponse({"options": [
            {"value": loc.name, "label": loc.name} for loc in staffing.LOCATIONS
        ]})
    return JSONResponse({"ok": False, "error": f"unknown kind: {kind}"}, status_code=400)


@router.get("/api/widget-defs")
def list_defs():
    return JSONResponse({"definitions": widget_definitions_store.list_definitions()})


@router.post("/api/widget-defs")
async def save_def(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    body = body or {}
    name = body.get("name")
    type_ = body.get("type")
    visual = body.get("visual") or {}
    default_data = body.get("default_data") or {}
    row_id = body.get("id")
    if not isinstance(name, str) or not name.strip():
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    if widget_types.get(type_) is None:
        return JSONResponse({"ok": False, "error": f"unknown type: {type_}"}, status_code=400)
    if not isinstance(visual, dict) or not isinstance(default_data, dict):
        return JSONResponse({"ok": False, "error": "visual and default_data must be objects"}, status_code=400)
    saved = widget_definitions_store.save(
        name=name.strip(), type=type_,
        visual=visual, default_data=default_data,
        id=int(row_id) if row_id is not None else None,
    )
    return JSONResponse({"ok": True, "definition": saved})


@router.delete("/api/widget-defs/{def_id}")
def delete_def(def_id: int):
    n = widget_definitions_store.usage_count(def_id)
    if n > 0:
        return JSONResponse(
            {"ok": False, "error": f"in use by {n} placement(s)"},
            status_code=409,
        )
    widget_definitions_store.delete(def_id)
    return JSONResponse({"ok": True})
```

- [ ] **Step 4: Create `src/zira_dashboard/templates/widgets.html`**

```jinja
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="/static/gpi-logo.png">
<title>Widget Workshop — GPI Plant Manager</title>
<style>
  :root {
    --bg: #f1f4f7; --panel: #ffffff; --panel-2: #f1f4f7;
    --border: #d8dee5; --fg: #1f2937; --muted: #6b7280;
    --accent: #16a34a; --accent-dim: #dcfce7; --bad: #ef4444;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, sans-serif;
         background: var(--bg); color: var(--fg); }
  header { padding: 0.9rem 1.25rem; background: var(--panel);
           border-bottom: 1px solid var(--border); display: flex; gap: 1rem; align-items: center; }
  h1 { margin: 0; font-size: 1.1rem; font-weight: 600; }
  nav a { color: var(--muted); text-decoration: none; font-size: 0.9rem;
          padding: 0.25rem 0.6rem; border-radius: 6px; }
  nav a.active { color: var(--accent); background: var(--accent-dim); font-weight: 600; }
  main { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; padding: 1rem; }
  .panel { background: var(--panel); border: 1px solid var(--border);
           border-radius: 10px; padding: 0.8rem 1rem; }
  h2 { margin: 0 0 0.5rem; font-size: 0.75rem; font-weight: 600;
       text-transform: uppercase; letter-spacing: 0.8px; color: var(--muted); }
  .def-row { display: grid; grid-template-columns: 1fr auto auto; gap: 0.5rem;
             align-items: center; padding: 0.35rem 0; border-bottom: 1px solid var(--border); }
  .def-row:last-child { border-bottom: none; }
  .def-row .name { font-weight: 600; }
  .def-row .type { font-size: 0.75rem; color: var(--muted); font-variant: small-caps; }
  .def-row button { background: transparent; border: 1px solid var(--border);
                    border-radius: 5px; padding: 0.2rem 0.6rem; cursor: pointer;
                    font: inherit; font-size: 0.8rem; color: var(--muted); }
  .def-row button.danger { color: var(--bad); }
  form input[type=text], form select, form input[type=color] {
    background: var(--panel-2); color: var(--fg); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.3rem 0.5rem; font: inherit; font-size: 0.9rem; width: 100%;
  }
  form label { display: block; margin-bottom: 0.5rem; font-size: 0.85rem; color: var(--muted); }
  form .submit { background: var(--accent); color: white; border: 1px solid var(--accent);
                 border-radius: 6px; padding: 0.4rem 1rem; font-weight: 700; cursor: pointer;
                 margin-top: 0.5rem; }
</style>
</head>
<body>
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
<main>
  <section class="panel">
    <h2>Saved widgets</h2>
    <div id="defs-list">
      {% for d in definitions %}
        <div class="def-row" data-id="{{ d.id }}">
          <div><span class="name">{{ d.name }}</span> <span class="type">{{ d.type }}</span></div>
          <button type="button" class="edit-btn">Edit</button>
          <button type="button" class="danger delete-btn">Delete</button>
        </div>
      {% else %}
        <div class="def-row"><em>No widgets yet — create one →</em></div>
      {% endfor %}
    </div>
  </section>

  <section class="panel">
    <h2>Create / edit</h2>
    <form id="def-form">
      <input type="hidden" id="def-id" value="">
      <label>Name<input type="text" id="def-name" placeholder="e.g. Repairs Pallets" required></label>
      <label>Type
        <select id="def-type" required>
          <option value="">Pick a type…</option>
          {% for t in types %}
            <option value="{{ t.type }}">{{ t.label }}</option>
          {% endfor %}
        </select>
      </label>
      <div id="type-fields"></div>
      <button type="button" id="save-btn" class="submit">Save widget</button>
      <span id="save-status"></span>
    </form>
  </section>
</main>

<script>
(function() {
  const TYPES = {{ types | tojson }};
  const typeSel = document.getElementById('def-type');
  const fieldsHost = document.getElementById('type-fields');

  function fieldEl(field, currentValue) {
    const label = document.createElement('label');
    label.textContent = field.label;
    let input;
    if (field.input === 'select') {
      input = document.createElement('select');
      if (field.options) {
        for (const o of field.options) {
          const opt = document.createElement('option');
          opt.value = o.value; opt.textContent = o.label;
          if (currentValue === o.value) opt.selected = true;
          input.appendChild(opt);
        }
      }
      if (field.options_from) {
        input.disabled = true;
        fetch('/api/widgets/options/' + field.options_from).then(r => r.json()).then(data => {
          for (const o of (data.options || [])) {
            const opt = document.createElement('option');
            opt.value = o.value; opt.textContent = o.label;
            if (currentValue === o.value) opt.selected = true;
            input.appendChild(opt);
          }
          input.disabled = false;
        });
      }
    } else if (field.input === 'color') {
      input = document.createElement('input');
      input.type = 'color';
      input.value = currentValue || field.default || '#22c55e';
    } else {
      input = document.createElement('input');
      input.type = 'text';
      if (currentValue !== undefined) input.value = currentValue;
    }
    input.dataset.key = field.key;
    input.dataset.section = field.__section;
    label.appendChild(input);
    return label;
  }

  function rebuildFields(typeId, existingVisual, existingData) {
    fieldsHost.innerHTML = '';
    if (!typeId) return;
    const def = TYPES.find(t => t.type === typeId);
    if (!def) return;
    if (def.visual_params_schema.length) {
      const h = document.createElement('h2'); h.textContent = 'Visual';
      fieldsHost.appendChild(h);
      for (const f of def.visual_params_schema) {
        f.__section = 'visual';
        fieldsHost.appendChild(fieldEl(f, (existingVisual || {})[f.key]));
      }
    }
    if (def.data_params_schema.length) {
      const h = document.createElement('h2'); h.textContent = 'Default data scope';
      fieldsHost.appendChild(h);
      for (const f of def.data_params_schema) {
        f.__section = 'data';
        fieldsHost.appendChild(fieldEl(f, (existingData || {})[f.key]));
      }
    }
  }

  typeSel.addEventListener('change', () => rebuildFields(typeSel.value, {}, {}));

  function collectForm() {
    const visual = {}, data = {};
    fieldsHost.querySelectorAll('input, select').forEach(el => {
      const v = el.value;
      if (v === '' || v === null || v === undefined) return;
      if (el.dataset.section === 'visual') visual[el.dataset.key] = v;
      else if (el.dataset.section === 'data') data[el.dataset.key] = v;
    });
    return {visual, data};
  }

  document.getElementById('save-btn').addEventListener('click', () => {
    const id = document.getElementById('def-id').value;
    const name = document.getElementById('def-name').value.trim();
    const type = typeSel.value;
    const status = document.getElementById('save-status');
    if (!name || !type) { status.textContent = 'name + type required'; return; }
    const {visual, data} = collectForm();
    const body = {name, type, visual, default_data: data};
    if (id) body.id = parseInt(id, 10);
    status.textContent = 'Saving…';
    fetch('/api/widget-defs', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    }).then(r => r.json()).then(d => {
      if (d.ok) { status.textContent = 'Saved'; setTimeout(() => location.reload(), 400); }
      else status.textContent = 'Error: ' + (d.error || 'unknown');
    });
  });

  document.querySelectorAll('.delete-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const row = btn.closest('.def-row');
      const id = row.dataset.id;
      if (!confirm('Delete this widget? (will fail if any dashboard uses it)')) return;
      fetch('/api/widget-defs/' + id, {method: 'DELETE'}).then(r => r.json()).then(d => {
        if (d.ok) row.remove();
        else alert('Cannot delete: ' + (d.error || 'unknown'));
      });
    });
  });

  document.querySelectorAll('.edit-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const row = btn.closest('.def-row');
      const id = row.dataset.id;
      fetch('/api/widget-defs').then(r => r.json()).then(data => {
        const def = (data.definitions || []).find(d => d.id == id);
        if (!def) return;
        document.getElementById('def-id').value = def.id;
        document.getElementById('def-name').value = def.name;
        typeSel.value = def.type;
        rebuildFields(def.type, def.visual, def.default_data);
      });
    });
  });
})();
</script>
</body>
</html>
```

- [ ] **Step 5: Register the router in `app.py`**

Open `src/zira_dashboard/app.py`. Add `widgets` to the `from .routes import (...)` alphabetical list, between `value_streams` and `wc_dashboard`:

```python
from .routes import (
    ...
    tv_displays,
    tv_templates,
    value_streams,
    wc_dashboard,
    widgets,
)
```

Then add the include line near the other `app.include_router` calls (alphabetical with the rest):

```python
app.include_router(wc_dashboard.router)
app.include_router(widgets.router)
```

- [ ] **Step 6: Run tests + verify page renders**

```
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; routes = sorted({r.path for r in app.routes if hasattr(r, 'path')}); [print(p) for p in routes if 'widget' in p]"
.venv/Scripts/python.exe -m pytest tests/test_widgets_routes.py -v 2>&1 | tail -15
.venv/Scripts/python.exe -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'), autoescape=True); env.parse(open('src/zira_dashboard/templates/widgets.html', encoding='utf-8').read()); print('parse OK')"
```

Expected: routes listed include `/widgets`, `/api/widget-defs`, `/api/widgets/types`, `/api/widgets/options/{kind}`. Tests SKIP without DB or PASS with DB. Parse OK.

- [ ] **Step 7: Commit**

```
git add src/zira_dashboard/routes/widgets.py src/zira_dashboard/templates/widgets.html src/zira_dashboard/app.py tests/test_widgets_routes.py
git commit -m "$(cat <<'EOF'
feat(widgets): Workshop page + CRUD API

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Custom dashboards routes + index page

**Files:**
- Create: `src/zira_dashboard/routes/custom_dashboards.py`
- Create: `src/zira_dashboard/templates/dashboards.html`
- Modify: `src/zira_dashboard/app.py` — register the router
- Test: `tests/test_custom_dashboards_routes.py` (partial — Task 8 adds editor + TV tests)

- [ ] **Step 1: Write failing tests for dashboard CRUD**

Create `tests/test_custom_dashboards_routes.py`:

```python
"""Integration tests for custom dashboard routes."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from zira_dashboard.app import app

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="custom dashboards routes need Postgres",
)


@pytest.fixture(autouse=True)
def _clean():
    from zira_dashboard import db
    db.init_pool()
    db.bootstrap_schema()
    db.execute("DELETE FROM custom_dashboards WHERE slug LIKE 'cdr-%'")
    db.execute("DELETE FROM widget_definitions WHERE name LIKE 'cdr-%'")
    yield
    db.execute("DELETE FROM custom_dashboards WHERE slug LIKE 'cdr-%'")
    db.execute("DELETE FROM widget_definitions WHERE name LIKE 'cdr-%'")


def test_post_dashboard_creates():
    c = TestClient(app)
    r = c.post("/api/dashboards", json={
        "name": "cdr-repair-1-tv",
        "scope_kind": "wc",
        "scope_value": "Repair 1",
        "theme": "dark",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["dashboard"]["slug"] == "cdr-repair-1-tv"


def test_post_dashboard_rejects_bad_scope():
    c = TestClient(app)
    r = c.post("/api/dashboards", json={
        "name": "cdr-bad", "scope_kind": "fake", "scope_value": "x", "theme": "dark",
    })
    assert r.status_code == 400


def test_delete_dashboard():
    c = TestClient(app)
    add = c.post("/api/dashboards", json={
        "name": "cdr-deleteme", "scope_kind": "wc", "scope_value": "Repair 1", "theme": "dark",
    }).json()
    r = c.delete(f"/api/dashboards/{add['dashboard']['id']}")
    assert r.status_code == 200


def test_get_dashboards_index_page_renders():
    c = TestClient(app)
    c.post("/api/dashboards", json={
        "name": "cdr-shown", "scope_kind": "wc", "scope_value": "Repair 1", "theme": "dark",
    })
    r = c.get("/dashboards")
    assert r.status_code == 200
    assert "cdr-shown" in r.text


def test_add_placement():
    from zira_dashboard import widget_definitions_store
    c = TestClient(app)
    dash = c.post("/api/dashboards", json={
        "name": "cdr-place", "scope_kind": "wc", "scope_value": "Repair 1", "theme": "dark",
    }).json()["dashboard"]
    wd = widget_definitions_store.save(
        name="cdr-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    r = c.post(f"/api/dashboards/{dash['id']}/placements", json={
        "widget_def_id": wd["id"],
        "x": 0, "y": 0, "w": 4, "h": 4,
        "data_overrides": {"group": "Dismantlers"},
    })
    assert r.status_code == 200
    assert r.json()["placement"]["data_overrides"] == {"group": "Dismantlers"}


def test_patch_placement_updates_position():
    from zira_dashboard import widget_definitions_store
    c = TestClient(app)
    dash = c.post("/api/dashboards", json={
        "name": "cdr-patch", "scope_kind": "wc", "scope_value": "Repair 1", "theme": "dark",
    }).json()["dashboard"]
    wd = widget_definitions_store.save(
        name="cdr-patch-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    p = c.post(f"/api/dashboards/{dash['id']}/placements", json={
        "widget_def_id": wd["id"], "x": 0, "y": 0, "w": 4, "h": 4, "data_overrides": {},
    }).json()["placement"]
    r = c.patch(f"/api/placements/{p['id']}", json={"x": 6, "y": 2})
    assert r.status_code == 200


def test_delete_placement():
    from zira_dashboard import widget_definitions_store
    c = TestClient(app)
    dash = c.post("/api/dashboards", json={
        "name": "cdr-delp", "scope_kind": "wc", "scope_value": "Repair 1", "theme": "dark",
    }).json()["dashboard"]
    wd = widget_definitions_store.save(
        name="cdr-delp-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    p = c.post(f"/api/dashboards/{dash['id']}/placements", json={
        "widget_def_id": wd["id"], "x": 0, "y": 0, "w": 4, "h": 4, "data_overrides": {},
    }).json()["placement"]
    r = c.delete(f"/api/placements/{p['id']}")
    assert r.status_code == 200


def test_post_dashboard_layout_bulk_save():
    """Gridstack autosave POSTs a full layout list."""
    from zira_dashboard import widget_definitions_store
    c = TestClient(app)
    dash = c.post("/api/dashboards", json={
        "name": "cdr-bulk", "scope_kind": "wc", "scope_value": "Repair 1", "theme": "dark",
    }).json()["dashboard"]
    wd = widget_definitions_store.save(
        name="cdr-bulk-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    p1 = c.post(f"/api/dashboards/{dash['id']}/placements", json={
        "widget_def_id": wd["id"], "x": 0, "y": 0, "w": 4, "h": 4, "data_overrides": {},
    }).json()["placement"]
    p2 = c.post(f"/api/dashboards/{dash['id']}/placements", json={
        "widget_def_id": wd["id"], "x": 4, "y": 0, "w": 4, "h": 4, "data_overrides": {},
    }).json()["placement"]
    r = c.post(f"/api/dashboards/{dash['id']}/layout", json=[
        {"id": p1["id"], "x": 8, "y": 0, "w": 4, "h": 4},
        {"id": p2["id"], "x": 0, "y": 4, "w": 6, "h": 5},
    ])
    assert r.status_code == 200
    assert r.json()["ok"] is True
```

- [ ] **Step 2: Run to confirm fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_custom_dashboards_routes.py -v 2>&1 | tail -15`
Expected: SKIP without DB; 404s with DB until routes exist.

- [ ] **Step 3: Create `src/zira_dashboard/routes/custom_dashboards.py`**

```python
"""Custom-dashboard pages + CRUD API.

Pages:
  GET /dashboards                       index
  GET /dashboards/{slug}                editor (gridstack enabled, palette visible)
  GET /tv/dashboards/{slug}             TV view (no chrome, TV header on top)

API:
  POST   /api/dashboards                add/update (body {id?, name, scope_kind, scope_value, theme})
  DELETE /api/dashboards/{id}           delete (cascades placements)
  POST   /api/dashboards/{id}/placements   add placement
  PATCH  /api/placements/{id}              update position/overrides
  DELETE /api/placements/{id}              remove placement
  POST   /api/dashboards/{id}/layout    gridstack bulk-save (list of {id, x, y, w, h})
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import (
    custom_dashboards_store,
    widget_data,
    widget_definitions_store,
    widget_types,
)
from ..deps import templates

router = APIRouter()


@router.get("/dashboards", response_class=HTMLResponse)
def dashboards_index(request: Request):
    return templates.TemplateResponse(
        request, "dashboards.html",
        {
            "dashboards": custom_dashboards_store.list_dashboards(),
            "wcs": _wc_options(),
            "groups": _group_options(),
        },
    )


@router.get("/dashboards/{slug}", response_class=HTMLResponse)
def dashboard_editor(request: Request, slug: str):
    return _render_dashboard(request, slug=slug, tv_mode=False, tv_theme=None)


@router.get("/tv/dashboards/{slug}", response_class=HTMLResponse)
def dashboard_tv(request: Request, slug: str, theme: str | None = None):
    return _render_dashboard(request, slug=slug, tv_mode=True, tv_theme=theme)


def _render_dashboard(request: Request, *, slug: str, tv_mode: bool, tv_theme: str | None):
    dash = custom_dashboards_store.get_dashboard(slug)
    if dash is None:
        return HTMLResponse(
            f"<h1>Dashboard not found: {slug}</h1>"
            f"<p><a href=\"/dashboards\">Back to dashboards</a></p>",
            status_code=404,
        )
    placements = custom_dashboards_store.list_placements(dash["id"])
    today = datetime.now(timezone.utc).date()

    # Resolve each placement's data via its type's resolver.
    for p in placements:
        entry = widget_types.get(p["type"])
        if entry is None:
            p["data"] = {}
            continue
        resolver = getattr(widget_data, entry["resolver"], None)
        if resolver is None:
            p["data"] = {}
            continue
        try:
            p["data"] = resolver(p["effective_data"], day=today) or {}
        except Exception:
            p["data"] = {}

    # TV header context
    if tv_mode:
        tv_header_right = _operators_for_scope(dash["scope_kind"], dash["scope_value"], today)
    else:
        tv_header_right = None

    resolved_theme = tv_theme if tv_theme in ("light", "dark") else dash["theme"]

    return templates.TemplateResponse(
        request, "custom_dashboard.html",
        {
            "dashboard": dash,
            "placements": placements,
            "definitions": widget_definitions_store.list_definitions(),
            "tv_mode": tv_mode,
            "tv_theme": resolved_theme,
            "tv_header_right": tv_header_right,
            "today": today.isoformat(),
        },
    )


def _operators_for_scope(scope_kind: str, scope_value: str, day) -> str:
    from .. import work_centers_store
    from ..wc_dashboard_data import assigned_operators_for_wc
    if scope_kind == "wc":
        ops = assigned_operators_for_wc(scope_value, day)
    elif scope_kind == "group":
        members = work_centers_store.members("group", scope_value) or []
        seen: list[str] = []
        for loc in members:
            for op in assigned_operators_for_wc(loc.name, day):
                if op not in seen:
                    seen.append(op)
        ops = seen
    else:
        ops = []
    return " · ".join(ops) if ops else "(unassigned)"


def _wc_options():
    from .. import staffing
    return [{"name": loc.name} for loc in staffing.LOCATIONS]


def _group_options():
    from .. import work_centers_store
    return [{"name": g} for g in work_centers_store.all_group_names("group")]


@router.post("/api/dashboards")
async def post_dashboard(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    body = body or {}
    name = body.get("name")
    scope_kind = body.get("scope_kind")
    scope_value = body.get("scope_value")
    theme = body.get("theme") or "dark"
    row_id = body.get("id")
    if not isinstance(name, str) or not name.strip():
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    if scope_kind not in ("wc", "group"):
        return JSONResponse({"ok": False, "error": "scope_kind must be wc or group"}, status_code=400)
    if not isinstance(scope_value, str) or not scope_value.strip():
        return JSONResponse({"ok": False, "error": "scope_value required"}, status_code=400)
    if theme not in ("light", "dark"):
        return JSONResponse({"ok": False, "error": "theme must be light or dark"}, status_code=400)
    saved = custom_dashboards_store.save_dashboard(
        name=name.strip(), scope_kind=scope_kind, scope_value=scope_value.strip(),
        theme=theme, id=int(row_id) if row_id is not None else None,
    )
    return JSONResponse({"ok": True, "dashboard": saved})


@router.delete("/api/dashboards/{dashboard_id}")
def delete_dashboard(dashboard_id: int):
    custom_dashboards_store.delete_dashboard(dashboard_id)
    return JSONResponse({"ok": True})


@router.post("/api/dashboards/{dashboard_id}/placements")
async def post_placement(dashboard_id: int, request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    body = body or {}
    widget_def_id = body.get("widget_def_id")
    if not isinstance(widget_def_id, int):
        return JSONResponse({"ok": False, "error": "widget_def_id required (int)"}, status_code=400)
    placement = custom_dashboards_store.add_placement(
        dashboard_id=dashboard_id,
        widget_def_id=widget_def_id,
        x=int(body.get("x", 0)),
        y=int(body.get("y", 0)),
        w=int(body.get("w", 4)),
        h=int(body.get("h", 4)),
        data_overrides=body.get("data_overrides") or {},
    )
    return JSONResponse({"ok": True, "placement": placement})


@router.patch("/api/placements/{placement_id}")
async def patch_placement(placement_id: int, request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    body = body or {}
    custom_dashboards_store.update_placement(
        placement_id,
        x=body.get("x"),
        y=body.get("y"),
        w=body.get("w"),
        h=body.get("h"),
        data_overrides=body.get("data_overrides"),
    )
    return JSONResponse({"ok": True})


@router.delete("/api/placements/{placement_id}")
def delete_placement(placement_id: int):
    custom_dashboards_store.delete_placement(placement_id)
    return JSONResponse({"ok": True})


@router.post("/api/dashboards/{dashboard_id}/layout")
async def post_layout(dashboard_id: int, request: Request):
    """Bulk-save layout. Body is a list of {id, x, y, w, h}."""
    try:
        items = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(items, list):
        return JSONResponse({"ok": False, "error": "expected list"}, status_code=400)
    for it in items:
        if not isinstance(it, dict) or "id" not in it:
            continue
        custom_dashboards_store.update_placement(
            int(it["id"]),
            x=it.get("x"), y=it.get("y"), w=it.get("w"), h=it.get("h"),
        )
    return JSONResponse({"ok": True, "count": len(items)})
```

- [ ] **Step 4: Create `src/zira_dashboard/templates/dashboards.html`**

```jinja
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="/static/gpi-logo.png">
<title>My Dashboards — GPI Plant Manager</title>
<style>
  :root {
    --bg: #f1f4f7; --panel: #ffffff; --panel-2: #f1f4f7;
    --border: #d8dee5; --fg: #1f2937; --muted: #6b7280;
    --accent: #16a34a; --accent-dim: #dcfce7; --bad: #ef4444;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: var(--bg); color: var(--fg); }
  header { padding: 0.9rem 1.25rem; background: var(--panel); border-bottom: 1px solid var(--border); display: flex; gap: 1rem; align-items: center; }
  h1 { margin: 0; font-size: 1.1rem; }
  nav a { color: var(--muted); text-decoration: none; font-size: 0.9rem; padding: 0.25rem 0.6rem; border-radius: 6px; }
  nav a.active { color: var(--accent); background: var(--accent-dim); font-weight: 600; }
  main { padding: 1rem; }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 0.8rem 1rem; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border); text-align: left; font-size: 0.9rem; }
  th { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.6px; }
  a.btn { color: var(--accent); text-decoration: none; padding: 0.2rem 0.55rem; border: 1px solid var(--accent); border-radius: 5px; font-size: 0.8rem; }
  a.btn-tv { background: var(--accent); color: white; }
  button.danger { background: transparent; color: var(--bad); border: 1px solid var(--bad); border-radius: 5px; padding: 0.2rem 0.55rem; cursor: pointer; font-size: 0.8rem; }
  .new-form { margin-top: 1rem; display: flex; gap: 0.5rem; align-items: center; }
  .new-form input, .new-form select { background: var(--panel-2); color: var(--fg); border: 1px solid var(--border); border-radius: 6px; padding: 0.3rem 0.5rem; font: inherit; font-size: 0.9rem; }
  .new-form .submit { background: var(--accent); color: white; border: 1px solid var(--accent); border-radius: 6px; padding: 0.35rem 0.9rem; font-weight: 700; cursor: pointer; }
</style>
</head>
<body>
<header>
  <h1>My Dashboards</h1>
  <nav>
    <a href="/recycling">Dashboards</a>
    <a href="/widgets">Widgets</a>
    <a href="/dashboards" class="active">My Dashboards</a>
    <a href="/trophies">Trophy Case</a>
    <a href="/staffing">Staffing</a>
    <a href="/settings">Settings</a>
  </nav>
</header>
<main>
  <section class="panel">
    <table>
      <thead><tr><th>Name</th><th>Scope</th><th>Widgets</th><th>Theme</th><th></th></tr></thead>
      <tbody id="dash-body">
        {% for d in dashboards %}
          <tr data-id="{{ d.id }}">
            <td><a href="/dashboards/{{ d.slug }}">{{ d.name }}</a></td>
            <td>{{ d.scope_kind }}: {{ d.scope_value }}</td>
            <td>{{ d.widget_count }}</td>
            <td>{{ d.theme }}</td>
            <td>
              <a class="btn btn-tv" href="/tv/dashboards/{{ d.slug }}">Open as TV</a>
              <button type="button" class="danger del-btn">Delete</button>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="5"><em>No dashboards yet — create one below.</em></td></tr>
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
      <button type="button" class="submit" id="new-btn">Create dashboard</button>
    </div>
  </section>
</main>

<script>
(function() {
  const kind = document.getElementById('new-scope-kind');
  const wcSel = document.getElementById('new-scope-wc');
  const grpSel = document.getElementById('new-scope-group');
  kind.addEventListener('change', () => {
    wcSel.style.display = kind.value === 'wc' ? '' : 'none';
    grpSel.style.display = kind.value === 'group' ? '' : 'none';
  });

  document.getElementById('new-btn').addEventListener('click', () => {
    const name = document.getElementById('new-name').value.trim();
    if (!name) return;
    const scope_value = kind.value === 'wc' ? wcSel.value : grpSel.value;
    fetch('/api/dashboards', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name, scope_kind: kind.value, scope_value, theme: document.getElementById('new-theme').value,
      }),
    }).then(r => r.json()).then(d => {
      if (d.ok) location.href = '/dashboards/' + d.dashboard.slug;
      else alert('Error: ' + (d.error || 'unknown'));
    });
  });

  document.querySelectorAll('.del-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tr = btn.closest('tr');
      const id = tr.dataset.id;
      if (!confirm('Delete this dashboard?')) return;
      fetch('/api/dashboards/' + id, {method: 'DELETE'}).then(r => r.json()).then(d => {
        if (d.ok) tr.remove();
      });
    });
  });
})();
</script>
</body>
</html>
```

- [ ] **Step 5: Register the router in `app.py`**

In `from .routes import (...)`, add `custom_dashboards` alphabetically (between `changelog` and `dashboard`):

```python
from .routes import (
    admin,
    api_layout,
    changelog,
    custom_dashboards,
    dashboard,
    ...
)
```

Then add the include line near the other routers:

```python
app.include_router(custom_dashboards.router)
```

- [ ] **Step 6: Run tests**

Note: The editor + TV tests (`/dashboards/{slug}` rendering, TV header) come in Task 8 when `custom_dashboard.html` exists. The CRUD/index tests should pass now.

```
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; routes = sorted({r.path for r in app.routes if hasattr(r, 'path')}); [print(p) for p in routes if 'dashboard' in p or 'placement' in p]"
.venv/Scripts/python.exe -m pytest tests/test_custom_dashboards_routes.py -v 2>&1 | tail -20
```

Expected: routes listed include `/dashboards`, `/dashboards/{slug}`, `/tv/dashboards/{slug}`, `/api/dashboards`, `/api/placements/{placement_id}`. Tests SKIP without DB or PASS with DB except editor-render which is in Task 8.

- [ ] **Step 7: Commit**

```
git add src/zira_dashboard/routes/custom_dashboards.py src/zira_dashboard/templates/dashboards.html src/zira_dashboard/app.py tests/test_custom_dashboards_routes.py
git commit -m "$(cat <<'EOF'
feat(widgets): custom dashboards routes + index page

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Custom dashboard editor + TV template

**Files:**
- Create: `src/zira_dashboard/templates/custom_dashboard.html`
- Test: extend `tests/test_custom_dashboards_routes.py` with editor + TV render tests

- [ ] **Step 1: Add editor + TV render tests**

Append to `tests/test_custom_dashboards_routes.py`:

```python
def test_editor_renders_with_placements():
    from zira_dashboard import widget_definitions_store
    c = TestClient(app)
    dash = c.post("/api/dashboards", json={
        "name": "cdr-edit", "scope_kind": "wc", "scope_value": "Repair 1", "theme": "dark",
    }).json()["dashboard"]
    wd = widget_definitions_store.save(
        name="cdr-edit-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    c.post(f"/api/dashboards/{dash['id']}/placements", json={
        "widget_def_id": wd["id"], "x": 0, "y": 0, "w": 4, "h": 4, "data_overrides": {},
    })
    r = c.get(f"/dashboards/{dash['slug']}")
    assert r.status_code == 200
    assert "cdr-edit-wd" in r.text or "Monthly Ribbons" in r.text  # widget title appears


def test_tv_view_renders_with_tv_header():
    from zira_dashboard import widget_definitions_store
    c = TestClient(app)
    dash = c.post("/api/dashboards", json={
        "name": "cdr-tv", "scope_kind": "wc", "scope_value": "Repair 1", "theme": "light",
    }).json()["dashboard"]
    wd = widget_definitions_store.save(
        name="cdr-tv-wd", type="ribbons", visual={}, default_data={"group": "Repairs"},
    )
    c.post(f"/api/dashboards/{dash['id']}/placements", json={
        "widget_def_id": wd["id"], "x": 0, "y": 0, "w": 4, "h": 4, "data_overrides": {},
    })
    r = c.get(f"/tv/dashboards/{dash['slug']}")
    assert r.status_code == 200
    assert 'data-tv-theme="light"' in r.text
    # WC name should appear in the TV header.
    assert "Repair 1" in r.text


def test_tv_view_404_for_unknown_slug():
    c = TestClient(app)
    r = c.get("/tv/dashboards/cdr-not-real")
    assert r.status_code == 404
```

- [ ] **Step 2: Create `src/zira_dashboard/templates/custom_dashboard.html`**

```jinja
{# Custom dashboard — editor + TV variant (gated on tv_mode).
   Reuses /static/wc_dashboard.css for shared widget visuals and
   /static/tv-mode.css for chrome-hide.
#}
{% from "_tv_header.html" import tv_header %}
<!doctype html>
<html lang="en"{% if tv_mode %} data-tv-theme="{{ tv_theme or 'dark' }}"{% endif %}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="/static/gpi-logo.png">
<title>{% if tv_mode %}TV · {% endif %}{{ dashboard.name }} — GPI Plant Manager</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gridstack@10.3.1/dist/gridstack.min.css">
<link rel="stylesheet" href="/static/wc_dashboard.css?v={{ static_v('wc_dashboard.css') }}">
<link rel="stylesheet" href="/static/recycling.css?v={{ static_v('recycling.css') }}">
{% if tv_mode %}
<link rel="stylesheet" href="/static/tv-mode.css?v={{ static_v('tv-mode.css') }}">
<meta http-equiv="refresh" content="60">
{% endif %}
<style>
  body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: var(--bg, #f1f4f7); color: var(--fg, #1f2937); }
  header.app { padding: 0.9rem 1.25rem; background: var(--panel); border-bottom: 1px solid var(--border); display: flex; gap: 1rem; align-items: center; }
  header.app h1 { margin: 0; font-size: 1.05rem; font-weight: 600; }
  header.app nav a { color: var(--muted); text-decoration: none; font-size: 0.9rem; padding: 0.25rem 0.6rem; border-radius: 6px; }
  header.app nav a.active { color: var(--accent); background: var(--accent-dim); font-weight: 600; }
  main { padding: 0.5rem 1rem; }
  .palette {
    position: fixed; right: 0; top: 60px; width: 240px; max-height: 70vh; overflow: auto;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px 0 0 10px;
    padding: 0.6rem; box-shadow: -2px 0 8px rgba(0,0,0,0.05); z-index: 50;
  }
  .palette h3 { margin: 0 0 0.4rem; font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.6px; }
  .palette .pal-row { display: grid; grid-template-columns: 1fr auto; gap: 0.4rem; align-items: center; padding: 0.3rem 0; border-bottom: 1px solid var(--border); }
  .palette .pal-row:last-child { border-bottom: none; }
  .palette .pal-row .name { font-size: 0.85rem; font-weight: 600; }
  .palette .pal-row .type { font-size: 0.7rem; color: var(--muted); }
  .palette .pal-add-btn { background: var(--accent-dim); color: var(--accent); border: 1px solid var(--accent); border-radius: 5px; padding: 0.2rem 0.55rem; font-size: 0.78rem; font-weight: 700; cursor: pointer; }
</style>
</head>
<body>
{% if tv_mode %}
  {{ tv_header(
      dashboard.scope_value,
      crumb=dashboard.scope_kind|upper + " · " + dashboard.name|upper,
      right=tv_header_right,
  ) }}
{% else %}
  <header class="app">
    <h1>{{ dashboard.name }}</h1>
    <nav>
      <a href="/widgets">Widgets</a>
      <a href="/dashboards" class="active">My Dashboards</a>
      <a href="/tv/dashboards/{{ dashboard.slug }}">Open as TV</a>
    </nav>
  </header>
{% endif %}

<main>
<div class="grid-stack">
  {% for p in placements %}
    <div class="grid-stack-item"
         gs-id="{{ p.id }}" gs-x="{{ p.x }}" gs-y="{{ p.y }}" gs-w="{{ p.w }}" gs-h="{{ p.h }}">
      {% with placement = p, data = p.data %}
        {% include "_widget_render.html" %}
      {% endwith %}
    </div>
  {% endfor %}
</div>
</main>

{% if not tv_mode %}
<aside class="palette">
  <h3>Widget palette</h3>
  {% for d in definitions %}
    <div class="pal-row" data-def-id="{{ d.id }}">
      <div>
        <div class="name">{{ d.name }}</div>
        <div class="type">{{ d.type }}</div>
      </div>
      <button type="button" class="pal-add-btn">Add</button>
    </div>
  {% else %}
    <div class="pal-row"><em>No widgets yet — <a href="/widgets">create one</a></em></div>
  {% endfor %}
</aside>
{% endif %}

<script src="https://cdn.jsdelivr.net/npm/gridstack@10.3.1/dist/gridstack-all.js"></script>
<script>
  const grid = GridStack.init({ column: 12, cellHeight: 80, margin: 8, float: false });
  const DASHBOARD_ID = {{ dashboard.id }};

  function persistLayout() {
    const items = grid.save(false).map(it => ({
      id: parseInt(it.id, 10), x: it.x, y: it.y, w: it.w, h: it.h,
    })).filter(it => Number.isFinite(it.id));
    fetch('/api/dashboards/' + DASHBOARD_ID + '/layout', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(items),
    });
  }
  grid.on('change', persistLayout);

  {% if not tv_mode %}
  document.querySelectorAll('.pal-add-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const defId = parseInt(btn.closest('.pal-row').dataset.defId, 10);
      fetch('/api/dashboards/' + DASHBOARD_ID + '/placements', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          widget_def_id: defId, x: 0, y: 0, w: 4, h: 4, data_overrides: {},
        }),
      }).then(r => r.json()).then(d => {
        if (d.ok) location.reload();
      });
    });
  });
  {% endif %}
</script>
</body>
</html>
```

- [ ] **Step 3: Verify parse + run all dashboard tests**

```
.venv/Scripts/python.exe -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'), autoescape=True); env.parse(open('src/zira_dashboard/templates/custom_dashboard.html', encoding='utf-8').read()); print('parse OK')"
.venv/Scripts/python.exe -m pytest tests/test_custom_dashboards_routes.py -v 2>&1 | tail -20
.venv/Scripts/python.exe -m pytest 2>&1 | tail -3
```

Expected: parse OK; dashboard route tests PASS (with DB) or SKIP. Full suite gains the new tests; no new failures.

- [ ] **Step 4: Commit**

```
git add src/zira_dashboard/templates/custom_dashboard.html tests/test_custom_dashboards_routes.py
git commit -m "$(cat <<'EOF'
feat(widgets): custom dashboard editor + TV templates (palette + gridstack)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Settings nav link + CHANGELOG + push

**Files:**
- Modify: `src/zira_dashboard/routes/settings.py` — nothing to change in handler; only the template
- Modify: `src/zira_dashboard/templates/settings.html` — add "Widget Workshop" + "My Dashboards" links to the sidebar
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add sidebar entries to `settings.html`**

In `settings.html`, find the left-rail sidebar block (the `<aside class="settings-sidebar">` block, around line 501). Add two new top-of-list entries (before "Work Centers & Goals"):

Find:

```jinja
  <aside class="settings-sidebar" aria-label="Settings sections">
    <a href="?section=work_centers"
       class="settings-nav-item {% if active_section == 'work_centers' %}active{% endif %}">
      Work Centers &amp; Goals
    </a>
```

Replace with:

```jinja
  <aside class="settings-sidebar" aria-label="Settings sections">
    <a href="/widgets" class="settings-nav-item">Widget Workshop</a>
    <a href="/dashboards" class="settings-nav-item">My Dashboards</a>
    <a href="?section=work_centers"
       class="settings-nav-item {% if active_section == 'work_centers' %}active{% endif %}">
      Work Centers &amp; Goals
    </a>
```

These two new links are external (point at `/widgets` and `/dashboards` directly, not query-string sections) so they don't carry an `active` class on the Settings page.

- [ ] **Step 2: Verify parse**

```
.venv/Scripts/python.exe -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'), autoescape=True); env.parse(open('src/zira_dashboard/templates/settings.html', encoding='utf-8').read()); print('parse OK')"
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; print('app OK')"
.venv/Scripts/python.exe -m pytest 2>&1 | tail -3
```

Expected: parse OK, app OK, full test suite has the new tests added without regressions.

- [ ] **Step 3: Get the current time**

Run: `powershell.exe -Command "Get-Date -Format 'h:mm tt'"`

- [ ] **Step 4: Add CHANGELOG entry**

In `CHANGELOG.md`, insert a new `### <HH:MM TT>` block at the top of today's `## 2026-05-13` section:

```markdown
### <HH:MM TT>

- **Widget Workshop & Custom Dashboards (Phase 1)** — new top-level "Workshop" and "My Dashboards" surfaces under Settings. Workshop at `/widgets` lets you build named widget presets: pick a type, set a default data scope, save. Three types available in this first phase: **Pallets by Work Center**, **Vs. Goat Pace**, **Monthly Ribbons** — covering the most-asked widgets from `/recycling` and `/wc/{slug}`. Custom dashboards at `/dashboards` are your own pages built from those presets: pick widgets from the palette, drag/resize them, override the data scope per placement. Flip any dashboard to a TV view at `/tv/dashboards/{slug}` — strips chrome, swaps in the TV header with the dashboard's scope (a WC's name + operators, or a group's name + all assigned operators across its WCs). Existing `/recycling`, `/new-vs`, `/wc/{slug}` dashboards stay exactly as they are — this is a sibling system, not a replacement. Phases 2 (KPI / daily progress / cumulative / downtime / pallets-banner widgets) and 3 (TV Displays integration so custom dashboards can be saved as TVs and palette UX polish) ship later.
```

- [ ] **Step 5: Commit + push**

```
git add src/zira_dashboard/templates/settings.html CHANGELOG.md
git commit -m "$(cat <<'EOF'
feat(widgets): Settings sidebar links + Phase 1 changelog

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

Railway picks up the push and redeploys. After deploy:
1. Visit `/widgets`, create a widget: name = "Repairs Pallets", type = Pallets by Work Center, default group = Repairs.
2. Visit `/dashboards`, create one: name = "Repair Floor TV", scope = WC: Repair 1, theme = Dark.
3. On the editor for that dashboard, click "Add" on the "Repairs Pallets" palette entry. Drag to size.
4. Visit `/tv/dashboards/repair-floor-tv` — see the dashboard in TV mode with the Repair 1 + assigned-operators header.

---

## Done

Phase 1 ships. Workshop + dashboards + 3 widget types end-to-end. Phase 2 (5 more widget types) and Phase 3 (TV displays integration + palette polish) get their own plans when we get to them.

If the per-placement edit panel (override the data scope for an individual placement after it's been added) isn't sufficient via the API alone — and Dale wants it as inline UI — that's a Phase 3 enhancement: a per-widget "⋮" button on the editor that opens a schema-driven popover identical to the workshop's form but PATCHing the placement instead.
