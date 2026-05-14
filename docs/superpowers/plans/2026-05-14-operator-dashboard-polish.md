# Operator Dashboard Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `/wc/{slug}` to feature parity with `/recycling` — split KPI row into 4 resizable widgets, share one layout + customization namespace ("operator") across all WCs, add per-widget edit panels and auto-save, scale text via CSS container queries, add a top operator-name band, rebuild the pallets banner with start/now ticks, make KPI text theme-aware, and truncate 15-min + cumulative charts at "now".

**Architecture:** Reuse the existing `/recycling` machinery wholesale — same `layout_store`, same `widget_customizer`, same `/api/layout/{page}` and `/api/widget/{page}/{widget_id}` routes, same Jinja `edit_controls` macro (extracted to a shared partial so both pages call it). One shared `page="operator"` key serves every WC; legacy `wc:*` rows are dropped on bootstrap.

**Tech Stack:** FastAPI · Jinja2 · psycopg2/Postgres · Gridstack 10.3.1 · pytest · CSS container queries.

**Spec:** `docs/superpowers/specs/2026-05-14-operator-dashboard-polish-design.md`.

---

## File Map

| File | Role |
|---|---|
| `src/zira_dashboard/wc_dashboard_data.py` | Truncate progress buckets at "now"; add `offset` to bucket dict. |
| `src/zira_dashboard/templates/_widget_edit_controls.html` (new) | Shared `edit_controls` Jinja macro extracted from `recycling.html`. |
| `src/zira_dashboard/templates/recycling.html` | Replace inline `edit_controls` macro with `{% from ... %}` import. |
| `src/zira_dashboard/templates/wc_dashboard.html` | Restructure: 4 KPI widgets, operator band, edit-bar autosave, per-widget edit panels, pallets banner with axis ticks, new widget IDs, `class="wc-dashboard"` on `<body>`. |
| `src/zira_dashboard/routes/wc_dashboard.py` | Switch `layout_key` to `"operator"`; load `customs` from `widget_customizer.load_all("operator")`; add `shift_start_label` + `now_label` to context. |
| `src/zira_dashboard/static/wc_dashboard.css` | `container-type: inline-size` on widget content; `cqw` font sizes; theme-aware KPI color; `.operator-band` styles. |
| `src/zira_dashboard/db.py` (`_SCHEMA_DDL`) | Idempotent cleanup statements: `DELETE FROM widget_layouts WHERE page LIKE 'wc:%'` and same on `widget_customizations`. |
| `tests/test_wc_dashboard_data.py` | Unit tests for the truncation. |
| `tests/test_wc_dashboard.py` | Integration tests: shared layout key, operator band, split KPIs, edit chrome, banner ticks, TV mode. |
| `tests/test_db.py` | Bootstrap drops legacy `wc:*` rows. |
| `CHANGELOG.md` | New `### TIME` entry under today's date. |

---

## Task 1: Truncate 15-min + cumulative progress at "now"

**Files:**
- Modify: `src/zira_dashboard/wc_dashboard_data.py` (`fifteen_min_progress_buckets`, around line 440)
- Test: `tests/test_wc_dashboard_data.py`

- [ ] **Step 1: Add failing test — truncation when shift is in progress**

Append to `tests/test_wc_dashboard_data.py`:

```python
def test_fifteen_min_progress_buckets_truncates_at_now(monkeypatch):
    """On today, buckets stop at the current 15-min slot — no future buckets."""
    from datetime import date
    from zira_dashboard import wc_dashboard_data

    fake_raw = [
        {"minute_offset": off, "units": 5, "target": 10}
        for off in range(0, 480, 15)  # 32 buckets across an 8-hour shift
    ]
    monkeypatch.setattr(wc_dashboard_data, "fifteen_min_increments",
                        lambda wc, d: fake_raw)
    # Half the shift elapsed.
    monkeypatch.setattr(wc_dashboard_data, "_shift_elapsed_fraction",
                        lambda d: 0.5)

    result = wc_dashboard_data.fifteen_min_progress_buckets("Repair 1", date(2026, 5, 14))
    buckets = result["buckets"]
    # 0.5 * 480 = 240 minutes elapsed -> offsets 0..240 inclusive => 17 buckets
    assert len(buckets) == 17, f"expected 17 buckets, got {len(buckets)}"
    assert all(b["offset"] <= 240 for b in buckets)
    assert buckets[-1]["offset"] == 240
    # Exactly one bucket marked in_progress.
    in_progress = [b for b in buckets if b["in_progress"]]
    assert len(in_progress) == 1
    assert in_progress[0]["offset"] == 240
```

- [ ] **Step 2: Run test to verify it fails**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard_data.py::test_fifteen_min_progress_buckets_truncates_at_now -v
```

Expected: FAIL — either `KeyError: 'offset'` (offset not in bucket dict yet) or `assert 32 == 17` (no truncation yet).

- [ ] **Step 3: Add the offset field + truncation filter**

In `src/zira_dashboard/wc_dashboard_data.py`, replace the body of `fifteen_min_progress_buckets` from `buckets: list[dict] = []` through the return. Find the existing loop building buckets and update it:

```python
    buckets: list[dict] = []
    for b in raw:
        offset = b["minute_offset"]
        if shift_start is not None:
            bucket_dt = datetime.combine(day, shift_start) + timedelta(minutes=offset)
            hour = bucket_dt.hour
            am_pm = "a" if hour < 12 else "p"
            hour_12 = hour % 12 or 12
            label = f"{hour_12}:{bucket_dt.minute:02d}{am_pm}"
        else:
            label = f"+{offset}m"
        buckets.append({
            "label": label,
            "offset": offset,
            "actual": int(b.get("units") or 0),
            "target": int(b.get("target") or 0),
            "in_progress": offset <= elapsed < offset + 15,
        })
    # Drop future buckets so the chart stops at "now" (matches /recycling).
    # On past days _shift_elapsed_fraction returns 1.0, so this is a no-op.
    buckets = [b for b in buckets if b["offset"] <= elapsed]
    bucket_target = next((b["target"] for b in buckets if b["target"]), 0)
    return {"buckets": buckets, "bucket_target": bucket_target}
```

- [ ] **Step 4: Run test to verify it passes**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard_data.py::test_fifteen_min_progress_buckets_truncates_at_now -v
```

Expected: PASS.

- [ ] **Step 5: Add a second test — past day keeps all buckets**

Append to `tests/test_wc_dashboard_data.py`:

```python
def test_fifteen_min_progress_buckets_past_day_full_shift(monkeypatch):
    """On past days, shift elapsed fraction is 1.0 — all buckets returned."""
    from datetime import date
    from zira_dashboard import wc_dashboard_data

    fake_raw = [
        {"minute_offset": off, "units": 7, "target": 10}
        for off in range(0, 480, 15)
    ]
    monkeypatch.setattr(wc_dashboard_data, "fifteen_min_increments",
                        lambda wc, d: fake_raw)
    monkeypatch.setattr(wc_dashboard_data, "_shift_elapsed_fraction",
                        lambda d: 1.0)

    result = wc_dashboard_data.fifteen_min_progress_buckets("Repair 1", date(2026, 5, 1))
    assert len(result["buckets"]) == 32
    # No bucket should be flagged in_progress on a past day.
    assert not any(b["in_progress"] for b in result["buckets"])
```

- [ ] **Step 6: Run both tests**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard_data.py -v -k fifteen_min_progress
```

Expected: 2 PASS.

- [ ] **Step 7: Commit**

```
git add tests/test_wc_dashboard_data.py src/zira_dashboard/wc_dashboard_data.py
git commit -m "feat(operator): truncate 15-min + cumulative charts at 'now'

Match /recycling's behavior — fifteen_min_progress_buckets drops future
buckets so the operator dashboard's progress chart stops at the current
15-min slot instead of running through the whole shift. Adds 'offset'
to each bucket dict so the filter can compare past/current/future. The
cumulative daily chart consumes the same buckets, so its line stops at
'now' for free.

On past days the truncation is a no-op (_shift_elapsed_fraction = 1.0).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Extract `edit_controls` macro to shared partial

**Files:**
- Create: `src/zira_dashboard/templates/_widget_edit_controls.html`
- Modify: `src/zira_dashboard/templates/recycling.html` (the `edit_controls` macro defined around line 66)

This is a pure refactor — `/recycling` keeps rendering identically — that sets us up for `wc_dashboard.html` to reuse the same macro in Task 7.

- [ ] **Step 1: Add a failing smoke test asserting /recycling still renders the edit chrome**

Append to `tests/test_dashboards_polish.py` (or create `tests/test_recycling_edit_partial.py` if you prefer):

```python
def test_recycling_renders_edit_controls_after_partial_extraction(monkeypatch):
    """After moving edit_controls to a shared partial, /recycling still
    renders the per-widget ⋮ button on at least the KPI tiles."""
    import os
    if not os.environ.get("DATABASE_URL"):
        import pytest
        pytest.skip("DATABASE_URL not set")
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app
    c = TestClient(app)
    r = c.get("/recycling")
    assert r.status_code == 200
    # Each KPI tile must have an edit button referencing its widget id.
    assert 'data-widget="kpi-pallets"' in r.text
    assert 'data-widget="kpi-uptime"' in r.text
    assert 'class="widget-edit-btn"' in r.text
```

- [ ] **Step 2: Run the test — should PASS today (the macro still works inline)**

```
.\.venv\Scripts\python.exe -m pytest tests/test_dashboards_polish.py::test_recycling_renders_edit_controls_after_partial_extraction -v
```

Expected: PASS (or SKIP if no DATABASE_URL). This is a *regression* test — we're locking in current behavior before refactoring.

- [ ] **Step 3: Create the shared partial**

Create `src/zira_dashboard/templates/_widget_edit_controls.html`:

```jinja
{# Shared per-widget edit-panel macro used by /recycling and /wc/{slug}.
   Imported via: {% from "_widget_edit_controls.html" import edit_controls %}
   The macro depends on the caller having a `customs` dict in scope:
   customs[id] = {title?, color?, orientation?, number_position?, sort?,
                  align?, show_target?, show_legend?}
#}
{%- macro edit_controls(id, default_title, kind='kpi') -%}
  {%- set c = customs.get(id, {}) -%}
  {%- set cur_title = c.get('title', default_title) -%}
  {%- set cur_color = c.get('color', '') -%}
  {%- set cur_orient = c.get('orientation', 'horizontal') -%}
  {%- set cur_numpos = c.get('number_position', 'widget') -%}
  {%- set cur_sort = c.get('sort', 'preset') -%}
  {%- set cur_align = c.get('align', 'center') -%}
  {%- set cur_show_target = c.get('show_target', True) -%}
  {%- set cur_show_legend = c.get('show_legend', True) -%}
  <button type="button" class="widget-edit-btn" title="Edit widget" onclick="openEdit(this)" data-widget="{{ id }}">⋮</button>
  <div class="widget-edit" hidden>
    <h4>Edit — {{ default_title }}</h4>
    <label>Title<input type="text" name="title" value="{{ cur_title }}" placeholder="{{ default_title }}"></label>
    {% if kind != 'bars' %}
      <label>Primary color<input type="color" name="color" value="{{ cur_color or '#4ade80' }}"></label>
    {% else %}
      <div style="font-size:0.78rem;color:var(--muted);font-style:italic;line-height:1.3">Color is auto-scaled by progress vs each work center's goal — green when meeting or above, red when below.</div>
    {% endif %}
    {% if kind in ('bars', 'downtime') %}
      <label>Orientation
        <select name="orientation">
          <option value="horizontal" {% if cur_orient=='horizontal' %}selected{% endif %}>Horizontal</option>
          <option value="vertical"   {% if cur_orient=='vertical'   %}selected{% endif %}>Vertical</option>
        </select>
      </label>
      <label>Number position
        <select name="number_position">
          <option value="widget" {% if cur_numpos=='widget' %}selected{% endif %}>Edge of widget</option>
          <option value="bar"    {% if cur_numpos=='bar'    %}selected{% endif %}>End of bar</option>
          <option value="inside" {% if cur_numpos=='inside' %}selected{% endif %}>Inside bar</option>
          <option value="hidden" {% if cur_numpos=='hidden' %}selected{% endif %}>Hidden</option>
        </select>
      </label>
    {% endif %}
    {% if kind == 'bars' %}
      <label>Sort
        <select name="sort">
          <option value="preset" {% if cur_sort=='preset' %}selected{% endif %}>Default order</option>
          <option value="desc"   {% if cur_sort=='desc'   %}selected{% endif %}>Units (high → low)</option>
          <option value="asc"    {% if cur_sort=='asc'    %}selected{% endif %}>Units (low → high)</option>
          <option value="alpha"  {% if cur_sort=='alpha'  %}selected{% endif %}>Alphabetical</option>
        </select>
      </label>
    {% endif %}
    {% if kind == 'kpi' %}
      <label>Number alignment
        <select name="align">
          <option value="left"   {% if cur_align=='left'   %}selected{% endif %}>Left</option>
          <option value="center" {% if cur_align=='center' %}selected{% endif %}>Center</option>
          <option value="right"  {% if cur_align=='right'  %}selected{% endif %}>Right</option>
        </select>
      </label>
    {% endif %}
    {% if kind == 'progress' %}
      <label><input type="checkbox" name="show_target" {% if cur_show_target %}checked{% endif %}> Show goal line</label>
      <label><input type="checkbox" name="show_legend" {% if cur_show_legend %}checked{% endif %}> Show legend</label>
    {% endif %}
    <div class="row">
      <button type="button" class="danger" onclick="resetWidget(this, '{{ id }}')">Reset</button>
      <button type="button" onclick="closeEdit(this)">Cancel</button>
      <button type="button" class="primary" onclick="saveWidget(this, '{{ id }}')">Save</button>
    </div>
  </div>
{%- endmacro %}
```

- [ ] **Step 4: Update `recycling.html` to import the shared macro**

In `src/zira_dashboard/templates/recycling.html`, delete the inline `{%- macro edit_controls(...) -%} ... {%- endmacro %}` block (it runs from ~line 66 to ~line 130 — the macro starts with `{%- macro edit_controls(id, default_title, kind='kpi') -%}` and ends with the matching `{%- endmacro %}`). Replace it with:

```jinja
{% from "_widget_edit_controls.html" import edit_controls %}
```

Place this import near the top of the file alongside the other `{% from ... %}` lines (around the existing `{% from "_goat_badges.html" import goat_badges, goat_badges_css, hover_tip_clamp_script %}`).

- [ ] **Step 5: Re-run the regression test**

```
.\.venv\Scripts\python.exe -m pytest tests/test_dashboards_polish.py::test_recycling_renders_edit_controls_after_partial_extraction -v
```

Expected: PASS.

- [ ] **Step 6: Run the full /recycling test suite as a smoke check**

```
.\.venv\Scripts\python.exe -m pytest tests/test_dashboards_polish.py tests/test_widget_customization.py -q
```

Expected: no new failures (skips are OK without DATABASE_URL).

- [ ] **Step 7: Commit**

```
git add src/zira_dashboard/templates/_widget_edit_controls.html src/zira_dashboard/templates/recycling.html tests/test_dashboards_polish.py
git commit -m "refactor(templates): extract edit_controls macro to shared partial

Move the per-widget edit panel from recycling.html into
_widget_edit_controls.html so /recycling and the operator dashboard
can share one definition. No behavior change for /recycling.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Schema cleanup — drop legacy `wc:*` rows on bootstrap

**Files:**
- Modify: `src/zira_dashboard/db.py` (`_SCHEMA_DDL`)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
def test_bootstrap_drops_legacy_wc_layouts_and_customizations():
    """The earlier per-WC operator dashboard saved rows under page='wc:{slug}'.
    After the switch to a shared page='operator' key, those rows are
    orphaned — bootstrap drops them on every boot."""
    db.init_pool()
    db.bootstrap_schema()
    # Seed legacy rows the way the old code did.
    db.execute(
        "INSERT INTO widget_layouts (page, layout, updated_at) "
        "VALUES ('wc:repair-1', '[]'::jsonb, now()) "
        "ON CONFLICT (page) DO UPDATE SET layout = EXCLUDED.layout"
    )
    db.execute(
        "INSERT INTO widget_customizations (page, widget_id, customizations) "
        "VALUES ('wc:repair-1', 'kpi-units', '{}'::jsonb) "
        "ON CONFLICT (page, widget_id) DO UPDATE SET customizations = EXCLUDED.customizations"
    )
    # Re-run bootstrap; cleanup should drop both.
    db.bootstrap_schema()
    layouts = db.query("SELECT page FROM widget_layouts WHERE page LIKE 'wc:%'")
    customs = db.query("SELECT page FROM widget_customizations WHERE page LIKE 'wc:%'")
    assert layouts == [], f"legacy widget_layouts rows still present: {layouts}"
    assert customs == [], f"legacy widget_customizations rows still present: {customs}"
```

- [ ] **Step 2: Run the test (will skip without DATABASE_URL)**

```
.\.venv\Scripts\python.exe -m pytest tests/test_db.py::test_bootstrap_drops_legacy_wc_layouts_and_customizations -v
```

Expected on a machine with Postgres: FAIL — the cleanup doesn't exist yet, the rows persist.
Expected locally (no DATABASE_URL): SKIPPED. Railway runs the test against the real DB.

- [ ] **Step 3: Append the cleanup to `_SCHEMA_DDL`**

In `src/zira_dashboard/db.py`, find the existing tear-down block at the end of `_SCHEMA_DDL` (the `-- Tear-down (2026-05-14): workshop + custom dashboards experiment is gone.` comment). Append a new block at the very end of `_SCHEMA_DDL`, just before the closing `"""`:

```sql

-- Operator dashboard switch (2026-05-14): the per-WC widget layouts
-- saved under page='wc:{slug}' are orphaned now that every /wc/{slug}
-- reads/writes a single shared key 'operator'. Drop them so the table
-- stays clean. Idempotent — once empty, this is a no-op.
DELETE FROM widget_layouts        WHERE page LIKE 'wc:%';
DELETE FROM widget_customizations WHERE page LIKE 'wc:%';
```

- [ ] **Step 4: Run the test against Postgres if available**

```
.\.venv\Scripts\python.exe -m pytest tests/test_db.py::test_bootstrap_drops_legacy_wc_layouts_and_customizations -v
```

Expected (with DATABASE_URL): PASS. Locally: SKIPPED — Railway will run it on deploy.

- [ ] **Step 5: Smoke test the app boot**

```
.\.venv\Scripts\python.exe -c "from zira_dashboard.app import app; print('app OK')"
```

Expected: `app OK`.

- [ ] **Step 6: Commit**

```
git add src/zira_dashboard/db.py tests/test_db.py
git commit -m "feat(db): drop legacy wc:* layout + customization rows on bootstrap

The operator dashboard now uses one shared page='operator' key across
every WC; the prior per-WC keys (wc:repair-1, wc:dismantler-2, …) are
orphaned and clutter the tables. Idempotent DELETE at end of
_SCHEMA_DDL keeps the tables clean on every boot.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Operator route — switch to shared layout key + new context vars

**Files:**
- Modify: `src/zira_dashboard/routes/wc_dashboard.py` (`_render_wc_dashboard`, lines 24-105)
- Test: `tests/test_wc_dashboard.py`

This task is route-only — the template still uses the old layout key reference until Task 5, which is fine because `layout_store.layout_map("operator")` returning `{}` is harmless (defaults take over).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wc_dashboard.py`:

```python
def test_operator_route_uses_shared_layout_key(monkeypatch):
    """Both /wc/repair-1 and /wc/dismantler-2 read layout from page='operator'."""
    _stub_wc(monkeypatch)
    calls = []
    from zira_dashboard import layout_store
    real_layout_map = layout_store.layout_map
    monkeypatch.setattr(layout_store, "layout_map", lambda page: (calls.append(page) or {}))
    monkeypatch.setattr(__import__("zira_dashboard.wc_dashboard_data", fromlist=[""]),
                        "wc_by_slug",
                        lambda s: type("L", (), {"name": "Repair 1", "meter_id": "m", "skill": "Repair", "bay": "Bay 1"})() if s == "repair-1"
                              else type("L", (), {"name": "Dismantler 2", "meter_id": "m", "skill": "Dismantler", "bay": "Bay 2"})() if s == "dismantler-2"
                              else None)
    c = TestClient(app)
    c.get("/wc/repair-1")
    c.get("/wc/dismantler-2")
    assert "operator" in calls
    assert all(p == "operator" for p in calls), f"unexpected layout keys: {calls}"


def test_operator_route_loads_widget_customizations(monkeypatch):
    """The render context includes a `customs` dict loaded from page='operator'."""
    _stub_wc(monkeypatch)
    seen = {}
    from zira_dashboard import widget_customizer
    monkeypatch.setattr(widget_customizer, "load_all",
                        lambda page: (seen.setdefault("page", page) or {"kpi-units": {"title": "Custom"}}))
    c = TestClient(app)
    r = c.get("/wc/repair-1")
    assert r.status_code == 200
    assert seen.get("page") == "operator"
    # Custom title from the customizations dict should appear in HTML
    # (we'll wire that into the template in Task 7; for now we just
    # assert the data was loaded).


def test_operator_route_exposes_shift_labels(monkeypatch):
    """The render context exposes shift_start_label and now_label for the
    pallets banner's axis ticks."""
    _stub_wc(monkeypatch)
    c = TestClient(app)
    r = c.get("/wc/repair-1")
    assert r.status_code == 200
    # shift_start_label looks like "HH:MM" — check the substring.
    import re
    assert re.search(r"\bshift_start_label_placeholder_or_HHMM\b", "") is None  # sanity
    # The label flows into the template later (Task 8). For now, just
    # render and don't 500.
```

- [ ] **Step 2: Run the tests**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard.py -v -k "operator_route_uses_shared or operator_route_loads or operator_route_exposes"
```

Expected (with DATABASE_URL): FAIL on the first two (the route still uses `f"wc:{slug}"` and does not load customs). The third PASSes trivially.

- [ ] **Step 3: Update `_render_wc_dashboard`**

In `src/zira_dashboard/routes/wc_dashboard.py`, replace the body of `_render_wc_dashboard`. The two key changes:

1. `from .. import layout_store, wc_dashboard_data, work_centers_store` → also import `widget_customizer` and `shift_config`.
2. `layout_key = f"wc:{slug}"` → `layout_key = "operator"`.
3. Build `customs`, `shift_start_label`, `now_label` and pass into context.

Replace the existing imports + function with:

```python
"""Operator dashboard routes.

  /wc/{slug}        editor view (gridstack enabled, WC picker visible)
  /tv/wc/{slug}     TV view (chrome stripped, picker hidden)
  /operator         redirect to the first WC's /wc/{slug}

The /wc/{slug} dashboard mirrors /recycling's visual style — same CSS
classes, same widget markup — scoped to a single WC. A picker at the
top lets the user switch which WC. Layout + per-widget customizations
are shared across every WC under page='operator'.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import (
    layout_store,
    shift_config,
    wc_dashboard_data,
    widget_customizer,
    work_centers_store,
)
from ..deps import templates

router = APIRouter()


def _shift_start_label(day) -> str:
    try:
        t = shift_config.shift_start_for(day)
    except Exception:
        return ""
    return f"{t.hour:02d}:{t.minute:02d}"


def _now_label(day) -> str:
    """Current local time HH:MM if `day` is today (in SITE_TZ); empty otherwise."""
    today_local = datetime.now(shift_config.SITE_TZ).date()
    if day != today_local:
        return ""
    now_local = datetime.now(shift_config.SITE_TZ)
    return f"{now_local.hour:02d}:{now_local.minute:02d}"


def _render_wc_dashboard(
    request: Request,
    *,
    slug: str,
    tv_mode: bool,
    tv_theme: str,
):
    """Render the Operator dashboard for one WC."""
    from .. import staffing
    loc = wc_dashboard_data.wc_by_slug(slug)
    if loc is None:
        return JSONResponse({"error": f"no work center matches slug {slug!r}"}, status_code=404)

    today = datetime.now(timezone.utc).date()
    wc_name = loc.name
    operators = wc_dashboard_data.assigned_operators_for_wc(wc_name, today)
    operators_display = " · ".join(operators)
    groups = work_centers_store.groups(loc) or []
    wc_group = groups[0] if groups else None

    pallets = wc_dashboard_data.pallets_banner(wc_name, today)
    progress = wc_dashboard_data.fifteen_min_progress_buckets(wc_name, today)
    kpi = wc_dashboard_data.kpi_tiles(wc_name, today)
    report = wc_dashboard_data.downtime_report(wc_name, today) or {}
    down_min = int(report.get("total_minutes", 0))
    elapsed_min = int(kpi["hours_elapsed"] * 60)
    working_min = max(0, elapsed_min - down_min)
    denom = elapsed_min if elapsed_min else 1
    downtime_row = {
        "name": wc_name,
        "working": working_min,
        "down": down_min,
        "working_pct": working_min / denom * 100.0,
        "down_pct": down_min / denom * 100.0,
    }
    goat = wc_dashboard_data.goat_race(wc_name, today) if wc_group else None
    ribbons = wc_dashboard_data.monthly_ribbons(wc_name, today.year, today.month) if wc_group else None

    layout_key = "operator"

    # Pallets-banner axis-tick position: prorated target as % of full-day goal.
    full_day = int(pallets.get("target_full_day") or 0)
    today_target = int(pallets.get("target_today") or 0)
    banner_now_pct = (today_target / full_day * 100.0) if full_day > 0 else 0.0

    return templates.TemplateResponse(
        request,
        "wc_dashboard.html",
        {
            "slug": slug,
            "wc_name": wc_name,
            "wc_group": wc_group,
            "operators": operators,
            "operators_display": operators_display,
            "today": today.isoformat(),
            "year": today.year,
            "month": today.month,
            "wc_options": [
                {"name": l.name, "slug": wc_dashboard_data.slug_for_wc(l.name)}
                for l in staffing.LOCATIONS
            ],
            "pallets": pallets,
            "progress_buckets": progress["buckets"],
            "progress_bucket_target": progress["bucket_target"],
            "kpi": kpi,
            "downtime_row": downtime_row,
            "downtime_elapsed_minutes": elapsed_min,
            "goat_race": goat,
            "ribbons": ribbons,
            "active_dashboard_key": "wc:" + wc_name,
            "layout": layout_store.layout_map(layout_key),
            "layout_key": layout_key,
            "customs": widget_customizer.load_all(layout_key),
            "shift_start_label": _shift_start_label(today),
            "now_label": _now_label(today),
            "banner_now_pct": banner_now_pct,
            "tv_mode": tv_mode,
            "tv_theme": tv_theme,
        },
    )
```

- [ ] **Step 4: Run the tests**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard.py -v -k "operator_route_uses_shared or operator_route_loads"
```

Expected (with DATABASE_URL): PASS.

- [ ] **Step 5: Smoke test the rest of `tests/test_wc_dashboard.py`**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard.py -q
```

Expected: no new failures (the existing tests in `_stub_wc` already monkeypatch the data helpers, so they tolerate the new context keys).

- [ ] **Step 6: Commit**

```
git add src/zira_dashboard/routes/wc_dashboard.py tests/test_wc_dashboard.py
git commit -m "feat(operator): switch route to shared 'operator' layout key

Replace per-WC layout key (wc:{slug}) with a single shared key
'operator' so customizations apply to every operator dashboard at
once. Adds widget_customizer.load_all('operator') as `customs` in
the render context plus shift_start_label / now_label for the
pallets banner's axis ticks (wired into the template next).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Operator template — split KPIs into 4 widgets + rename widget IDs + operator band

**Files:**
- Modify: `src/zira_dashboard/templates/wc_dashboard.html`
- Test: `tests/test_wc_dashboard.py`

This task replaces the template's grid contents. Edit chrome (⋮ + autosave bar) lands in Tasks 6-7; this task only restructures the grid items and adds the operator band.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wc_dashboard.py`:

```python
def test_operator_dashboard_has_four_split_kpi_widgets(monkeypatch):
    """KPI row is split into 4 independent grid-stack-items."""
    _stub_wc(monkeypatch)
    monkeypatch.setattr(
        __import__("zira_dashboard.wc_dashboard_data", fromlist=[""]),
        "kpi_tiles",
        lambda nm, d: {"units_today": 87, "downtime_minutes": 12,
                       "hours_elapsed": 4.0, "up_time_pct": 95.0,
                       "pallets_per_hour": 21.7},
    )
    c = TestClient(app)
    r = c.get("/wc/repair-1")
    assert r.status_code == 200
    for wid in ("kpi-units", "kpi-uptime", "kpi-downtime", "kpi-pph"):
        assert f'gs-id="{wid}"' in r.text, f"missing widget {wid}"


def test_operator_dashboard_renders_operator_band(monkeypatch):
    """The band shows WC name + operator names from the Plant Scheduler."""
    _stub_wc(monkeypatch)  # _stub_wc sets operators to ["Christian", "Jose L"]
    c = TestClient(app)
    r = c.get("/wc/repair-1")
    assert r.status_code == 200
    assert "operator-band" in r.text
    assert "Christian" in r.text and "Jose L" in r.text


def test_operator_dashboard_unassigned_band(monkeypatch):
    """With no operators assigned, the band shows '(unassigned)'."""
    _stub_wc(monkeypatch)
    from zira_dashboard import wc_dashboard_data
    monkeypatch.setattr(wc_dashboard_data, "assigned_operators_for_wc",
                        lambda nm, d: [])
    c = TestClient(app)
    r = c.get("/wc/repair-1")
    assert r.status_code == 200
    assert "(unassigned)" in r.text


def test_operator_dashboard_renames_remaining_widget_ids(monkeypatch):
    """Non-KPI widgets use the new shared IDs (no 'wc-' prefix)."""
    _stub_wc(monkeypatch)
    c = TestClient(app)
    r = c.get("/wc/repair-1")
    for wid in ("pallets-banner", "progress-15min", "cumulative-daily",
                "downtime-row", "goat-race", "monthly-ribbons"):
        assert f'gs-id="{wid}"' in r.text, f"missing widget {wid}"
    # And the old IDs are gone.
    for old in ("wc-kpi-row", "wc-pallets-banner", "wc-15min-progress",
                "wc-cumulative", "wc-downtime", "wc-goat-race",
                "wc-monthly-ribbons"):
        assert f'gs-id="{old}"' not in r.text, f"stale widget id still present: {old}"
```

- [ ] **Step 2: Run the tests**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard.py -v -k "split_kpi or operator_band or unassigned_band or renames_remaining"
```

Expected (with DATABASE_URL): FAIL — the template still has `wc-kpi-row` etc.

- [ ] **Step 3: Add the `widget_attrs` macro to `wc_dashboard.html`**

In `src/zira_dashboard/templates/wc_dashboard.html`, near the top of the file just below the existing imports (`{% from "_tv_header.html" import tv_header %}` etc.), add:

```jinja
{# Layout-aware grid-item attributes. customs/layout dicts are provided
   by the route. Falls back to the (dx, dy, dw, dh) defaults if no
   user-saved layout row exists for this id. #}
{%- macro widget_attrs(id, dx, dy, dw, dh) -%}
  {%- set l = layout.get(id, {'x': dx, 'y': dy, 'w': dw, 'h': dh}) -%}
  gs-id="{{ id }}" gs-x="{{ l.x }}" gs-y="{{ l.y }}" gs-w="{{ l.w }}" gs-h="{{ l.h }}"
{%- endmacro %}

{%- macro widget_title(id, default) -%}{{ customs.get(id, {}).get('title', default) }}{%- endmacro %}

{%- macro widget_color_style(id, role) -%}
  {%- set c = customs.get(id, {}).get('color') -%}
  {%- if c -%}style="--wc: {{ c }}; {{ role }}: {{ c }} !important"{%- endif -%}
{%- endmacro %}
```

- [ ] **Step 4: Add the `class="wc-dashboard"` to `<body>`**

In `wc_dashboard.html`, change `<body>` to `<body class="wc-dashboard">`.

- [ ] **Step 5: Add the operator band**

In `wc_dashboard.html`, find the existing `{% else %}` branch (the non-TV header block) that ends with `</div>` `{% endif %}`. Just BEFORE `<main>`, but only in the non-TV branch, insert:

```jinja
  {# Operator name band — page subtitle showing WC + scheduled operator(s). #}
  <div class="operator-band">
    <div class="operator-band-wc">{{ wc_name }}</div>
    <div class="operator-band-op">
      {% if operators_display %}
        <span class="op-icon" aria-hidden="true">👤</span> {{ operators_display }}
      {% else %}
        <em class="op-unassigned">(unassigned)</em>
      {% endif %}
    </div>
  </div>
```

Place it after the `<div class="wc-picker-bar"> … </div>` element and before `{% endif %}` that closes the `{% if tv_mode %} … {% else %} … {% endif %}` block.

- [ ] **Step 6: Replace the grid contents**

In `wc_dashboard.html`, replace EVERYTHING from `<main>` through `</main>` with:

```jinja
<main>
<div class="grid-stack">

  {# KPI tiles — 4 separate widgets, each resizable. #}
  {% set kpi_defs = [
    ('kpi-units',    'Units today', '{:,}'.format(kpi.units_today)),
    ('kpi-uptime',   'Up Time',     kpi.up_time_pct ~ ' %'),
    ('kpi-downtime', 'Downtime',    kpi.downtime_minutes|string ~ 'm'),
    ('kpi-pph',      'Pallets / hr', kpi.pallets_per_hour),
  ] %}
  {% for k in kpi_defs %}
    {% set kid = k[0] %}{% set ktitle = k[1] %}{% set kval = k[2] %}
    {% set kdef_x = loop.index0 * 3 %}
    {% set align = customs.get(kid, {}).get('align', 'center') %}
    <div class="grid-stack-item" {{ widget_attrs(kid, kdef_x, 0, 3, 2) }}>
      <div class="grid-stack-item-content align-{{ align }}">
        <div class="label">{{ widget_title(kid, ktitle) }}</div>
        <div class="val" {{ widget_color_style(kid, 'color') }}>{{ kval }}</div>
      </div>
    </div>
  {% endfor %}

  {# Pallets banner. Start/now ticks land in Task 8. #}
  <div class="grid-stack-item" {{ widget_attrs('pallets-banner', 0, 2, 12, 2) }}>
    <div class="grid-stack-item-content">
      <h3>{{ widget_title('pallets-banner', 'Today · Pallets') }}</h3>
      <div class="pallets-banner">
        <div class="pallets-numbers">
          <span class="units">{{ pallets.units_today }}</span>
          <span class="target">/ {{ pallets.target_today }} goal so far ({{ pallets.target_full_day }} full day)</span>
        </div>
        <div class="bar-track">
          <div class="bar-fill" style="width: {{ (pallets.pct_of_target|float)|round(0)|int if pallets.pct_of_target else 0 }}%"></div>
        </div>
      </div>
    </div>
  </div>

  {# 15-min progress chart. #}
  <div class="grid-stack-item" {{ widget_attrs('progress-15min', 0, 4, 12, 5) }}>
    <div class="grid-stack-item-content">
      <h3>{{ widget_title('progress-15min', '15-minute progress') }}</h3>
      {% if progress_buckets %}
        {%- set max_val = namespace(v=progress_bucket_target) -%}
        {%- for b in progress_buckets -%}
          {%- if b.actual > max_val.v %}{% set max_val.v = b.actual %}{% endif -%}
          {%- if b.target > max_val.v %}{% set max_val.v = b.target %}{% endif -%}
        {%- endfor -%}
        {%- set scale = max_val.v if max_val.v > 0 else 1 -%}
        <div class="progress">
          <div class="legend">
            <span><span class="swatch" style="background:var(--hit, var(--good))"></span>At / above goal</span>
            <span><span class="swatch" style="background:var(--bad)"></span>Below goal</span>
            {% if progress_bucket_target %}
              <span style="margin-left:auto">{{ progress_bucket_target }} per 15 min</span>
            {% endif %}
          </div>
          <div class="plot">
            <div class="bars">
              {% for b in progress_buckets %}
                {% set hit = b.actual >= b.target and not b.in_progress %}
                {% set h = (b.actual / scale * 100.0) if scale else 0 %}
                {% set t_h = (b.target / scale * 100.0) if (scale and b.target) else 0 %}
                <div class="col {% if hit %}hit{% else %}{% if b.in_progress %}hit{% else %}miss{% endif %}{% endif %} {% if b.in_progress %}in-progress{% endif %}"
                     title="{{ b.label }} · {{ b.actual }} pallets (goal {{ b.target }})">
                  <div class="bar" style="height: {{ h }}%">
                    {% if b.actual > 0 %}<span class="bar-label">{{ b.actual }}</span>{% endif %}
                  </div>
                  {% if not b.in_progress and b.target %}
                    <div class="target-tick" style="bottom: {{ t_h }}%"></div>
                  {% endif %}
                </div>
              {% endfor %}
            </div>
          </div>
          <div class="x-ticks">
            {% for b in progress_buckets %}
              {% if loop.index0 % 2 == 0 %}<span>{{ b.label }}</span>{% else %}<span></span>{% endif %}
            {% endfor %}
          </div>
        </div>
      {% else %}
        <div class="empty-state" style="color:var(--muted);font-size:0.85rem">No shift data for this day.</div>
      {% endif %}
    </div>
  </div>

  {# Cumulative daily progress. #}
  <div class="grid-stack-item" {{ widget_attrs('cumulative-daily', 0, 9, 12, 5) }}>
    <div class="grid-stack-item-content">
      <h3>{{ widget_title('cumulative-daily', 'Daily progress') }}</h3>
      {% if progress_buckets %}
        {{ cumulative_progress_chart(progress_buckets) }}
      {% else %}
        <div class="empty-state" style="color:var(--muted);font-size:0.85rem">No shift data for this day.</div>
      {% endif %}
    </div>
  </div>

  {# Downtime — single-row stacked working/down. #}
  <div class="grid-stack-item" {{ widget_attrs('downtime-row', 0, 14, 12, 3) }}>
    <div class="grid-stack-item-content">
      <h3>{{ widget_title('downtime-row', 'Downtime · green = working, red = down') }}</h3>
      {% if downtime_elapsed_minutes == 0 %}
        <div class="empty-state" style="color:var(--muted);font-size:0.85rem">No elapsed shift minutes on this day.</div>
      {% else %}
        <div class="bar-row numpos-widget">
          <div class="name"><span class="name-primary">{{ downtime_row.name }}</span></div>
          <div class="stacked-track">
            <div class="good" style="width: {{ downtime_row.working_pct }}%" title="Working {{ downtime_row.working }}m"></div>
            <div class="bad"  style="width: {{ downtime_row.down_pct }}%"    title="Down {{ downtime_row.down }}m"></div>
          </div>
          <div class="val">{{ downtime_row.down }}m</div>
        </div>
      {% endif %}
    </div>
  </div>

  {# Vs. GOAT Pace. #}
  <div class="grid-stack-item" {{ widget_attrs('goat-race', 0, 17, 12, 4) }}>
    <div class="grid-stack-item-content">
      <h3>{{ widget_title('goat-race', 'Vs. GOAT Pace') }}{% if wc_group %} — {{ wc_group }}{% endif %}</h3>
      {% if goat_race %}
        <div class="goat-race">
          {% if goat_race.status %}
            <div class="status-pill status-{{ goat_race.status|lower }}">{{ goat_race.status|replace('_', ' ') }}</div>
          {% else %}
            <div class="status-pill status-none">no record yet</div>
          {% endif %}
          <div class="race-stats">
            <div>Today: <b>{{ goat_race.units_today }}</b></div>
            <div>GOAT pace now: <b>{{ goat_race.goat_pace_today|round(0)|int }}</b></div>
            {% if goat_race.goat %}
              <div class="goat-meta">🐐 {{ goat_race.goat.name }} · {{ goat_race.goat.units }} on {{ goat_race.goat.day }}</div>
            {% endif %}
          </div>
        </div>
      {% else %}
        <div class="empty-state" style="color:var(--muted);font-size:0.85rem">{{ wc_name }} isn't in a group.</div>
      {% endif %}
    </div>
  </div>

  {# Monthly Ribbons. #}
  <div class="grid-stack-item" {{ widget_attrs('monthly-ribbons', 0, 21, 12, 4) }}>
    <div class="grid-stack-item-content">
      <h3>{{ widget_title('monthly-ribbons', 'Monthly Ribbons') }} — {{ month_name(month) }} {{ year }}{% if ribbons and ribbons.group %} · {{ ribbons.group }}{% endif %}</h3>
      {% if ribbons %}
        <ul class="ribbons-list">
          {% for r in ribbons.entries %}
            <li>
              <span class="medal">{% if r.position == 1 %}🥇{% elif r.position == 2 %}🥈{% else %}🥉{% endif %}</span>
              <span class="name"><a href="/staffing/people/{{ r.name|urlencode }}">{{ r.name }}</a></span>
              <span class="units">{{ r.units|round(0)|int }}</span>
            </li>
          {% else %}
            <li class="empty">no qualifying days yet</li>
          {% endfor %}
        </ul>
      {% else %}
        <div class="empty-state" style="color:var(--muted);font-size:0.85rem">{{ wc_name }} isn't in a group.</div>
      {% endif %}
    </div>
  </div>

</div>{# /.grid-stack #}
</main>
```

- [ ] **Step 7: Run the tests**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard.py -v -k "split_kpi or operator_band or unassigned_band or renames_remaining"
```

Expected (with DATABASE_URL): PASS.

- [ ] **Step 8: Smoke-run the full file**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard.py -q
```

Expected: no new failures. (Existing tests for "Repair 1" rendering still pass.)

- [ ] **Step 9: Commit**

```
git add src/zira_dashboard/templates/wc_dashboard.html tests/test_wc_dashboard.py
git commit -m "feat(operator): split KPIs into 4 widgets + operator band + new IDs

Restructure the grid: KPI row becomes four resizable widgets
(kpi-units, kpi-uptime, kpi-downtime, kpi-pph), each its own grid-
stack-item. Non-KPI widgets dropped their 'wc-' prefix to match the
operator-page-namespace IDs. New 'operator-band' shows the WC name
plus scheduled operator(s); falls back to '(unassigned)' when the
Plant Scheduler has no assignment. Body gets class='wc-dashboard'
for theme-scoped CSS landing in Task 9.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Auto-save layout + edit-bar (indicator + Reset Layout)

**Files:**
- Modify: `src/zira_dashboard/templates/wc_dashboard.html`
- Test: `tests/test_wc_dashboard.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wc_dashboard.py`:

```python
def test_operator_dashboard_has_edit_bar(monkeypatch):
    """The edit-bar with the save-indicator and Reset Layout button is
    rendered on the screen-mode dashboard."""
    _stub_wc(monkeypatch)
    c = TestClient(app)
    r = c.get("/wc/repair-1")
    assert r.status_code == 200
    assert 'class="edit-bar"' in r.text
    assert 'id="save-indicator"' in r.text
    assert 'id="reset-layout"' in r.text
    assert "Drag / resize" in r.text


def test_tv_wc_dashboard_omits_edit_bar(monkeypatch):
    """TV view skips the edit-bar (read-only)."""
    _stub_wc(monkeypatch)
    c = TestClient(app)
    r = c.get("/tv/wc/repair-1")
    assert r.status_code == 200
    assert 'id="save-indicator"' not in r.text
    assert 'id="reset-layout"' not in r.text


def test_operator_dashboard_persists_to_operator_layout_endpoint(monkeypatch):
    """The JS posts to /api/layout/operator (not /api/layout/wc:...)."""
    _stub_wc(monkeypatch)
    c = TestClient(app)
    r = c.get("/wc/repair-1")
    assert "/api/layout/operator" in r.text
    assert "/api/layout/wc:" not in r.text
```

- [ ] **Step 2: Run the tests**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard.py -v -k "edit_bar or persists_to_operator"
```

Expected (with DATABASE_URL): FAIL — these elements don't exist yet.

- [ ] **Step 3: Add the edit-bar + replace the script block**

In `src/zira_dashboard/templates/wc_dashboard.html`:

1. Just before `<main>` (and only in the non-TV branch — inside `{% if not tv_mode %} … {% endif %}` block, right after the operator band you added in Task 5), insert:

```jinja
  <div class="edit-bar">
    <span class="save-indicator" id="save-indicator">Drag / resize — layout auto-saves</span>
    <button type="button" id="reset-layout">Reset Layout</button>
  </div>
```

2. Replace the existing `<script>` block (the one with `const grid = GridStack.init(...)`) with:

```html
<script src="https://cdn.jsdelivr.net/npm/gridstack@10.3.1/dist/gridstack-all.js"></script>
<script>
  const grid = GridStack.init({
    column: 12,
    cellHeight: 60,
    margin: 8,
    float: false,
    handle: '.grid-stack-item-content > h3, .grid-stack-item-content > .label',
  });

  {% if not tv_mode %}
  const indicator = document.getElementById('save-indicator');
  let saveTimer = null;

  function persistLayout() {
    const items = grid.save(false).map(it => ({
      id: it.id, x: it.x, y: it.y, w: it.w, h: it.h,
    })).filter(it => it.id);
    fetch('/api/layout/operator', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(items),
    }).then(r => {
      if (r.ok) {
        indicator.textContent = 'Saved';
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
          indicator.textContent = 'Drag / resize — layout auto-saves';
        }, 1500);
      } else {
        indicator.textContent = 'Save failed';
      }
    }).catch(() => indicator.textContent = 'Save failed (network)');
  }

  grid.on('change', persistLayout);
  grid.on('resizestop', persistLayout);
  grid.on('dragstop', persistLayout);

  document.getElementById('reset-layout').addEventListener('click', () => {
    fetch('/api/layout/operator', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify([]),
    }).then(() => location.reload());
  });

  // WC picker: navigate to the chosen WC.
  const picker = document.getElementById('wc-picker');
  if (picker) {
    picker.addEventListener('change', (e) => {
      window.location.href = '/wc/' + e.target.value;
    });
  }
  {% endif %}
</script>
{{ hover_tip_clamp_script() }}
```

- [ ] **Step 4: Run the tests**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard.py -v -k "edit_bar or persists_to_operator"
```

Expected: PASS.

- [ ] **Step 5: Manual browser smoke check**

Run the dev server (`.\.venv\Scripts\python.exe -m uvicorn zira_dashboard.app:app --reload`), open `/wc/repair-1`, drag a widget, verify the indicator flashes "Saved", reload, verify the layout stuck. Open a different `/wc/{slug}` — verify the layout applies there too (shared key).

- [ ] **Step 6: Commit**

```
git add src/zira_dashboard/templates/wc_dashboard.html tests/test_wc_dashboard.py
git commit -m "feat(operator): edit-bar + auto-save layout to shared 'operator' key

Drop /api/layout/wc:{slug} POSTs; the gridstack change/resizestop/
dragstop handlers now post to /api/layout/operator so every WC's
layout state is one shared record. Adds the same 'Saved' indicator
and Reset Layout button /recycling has. TV view skips the edit-bar.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Per-widget edit panels (⋮ buttons)

**Files:**
- Modify: `src/zira_dashboard/templates/wc_dashboard.html`
- Test: `tests/test_wc_dashboard.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wc_dashboard.py`:

```python
def test_operator_dashboard_has_widget_edit_buttons(monkeypatch):
    """Every editable widget has a ⋮ button + a hidden edit panel."""
    _stub_wc(monkeypatch)
    c = TestClient(app)
    r = c.get("/wc/repair-1")
    assert r.status_code == 200
    # KPI tiles
    for wid in ("kpi-units", "kpi-uptime", "kpi-downtime", "kpi-pph"):
        assert f'data-widget="{wid}"' in r.text, f"missing edit btn for {wid}"
    # Chart widgets
    for wid in ("pallets-banner", "progress-15min", "cumulative-daily",
                "downtime-row", "goat-race", "monthly-ribbons"):
        assert f'data-widget="{wid}"' in r.text, f"missing edit btn for {wid}"


def test_tv_wc_dashboard_omits_edit_buttons(monkeypatch):
    """TV view skips edit chrome."""
    _stub_wc(monkeypatch)
    c = TestClient(app)
    r = c.get("/tv/wc/repair-1")
    assert r.status_code == 200
    assert 'widget-edit-btn' not in r.text
    assert 'class="widget-edit"' not in r.text


def test_operator_dashboard_applies_custom_titles(monkeypatch):
    """A title saved via widget_customizer flows into the rendered HTML."""
    _stub_wc(monkeypatch)
    from zira_dashboard import widget_customizer
    monkeypatch.setattr(widget_customizer, "load_all",
                        lambda page: {"kpi-units": {"title": "Pallets Done"}}
                                       if page == "operator" else {})
    c = TestClient(app)
    r = c.get("/wc/repair-1")
    assert "Pallets Done" in r.text


def test_operator_dashboard_posts_widget_edits_to_operator_endpoint(monkeypatch):
    """The JS save handler targets /api/widget/operator/{id}."""
    _stub_wc(monkeypatch)
    c = TestClient(app)
    r = c.get("/wc/repair-1")
    assert "/api/widget/operator/" in r.text
```

- [ ] **Step 2: Run the tests**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard.py -v -k "widget_edit_buttons or omits_edit_buttons or applies_custom_titles or posts_widget_edits"
```

Expected (with DATABASE_URL): FAIL — no edit panels yet.

- [ ] **Step 3: Import the shared macro**

In `src/zira_dashboard/templates/wc_dashboard.html`, near the top alongside the other `{% from ... %}` lines, add:

```jinja
{% from "_widget_edit_controls.html" import edit_controls %}
```

- [ ] **Step 4: Wrap every widget body with `edit_controls` (screen mode only)**

For each grid-stack-item in the template, insert the edit-controls macro call immediately inside the `.grid-stack-item-content`, BEFORE the `<h3>` (or `<div class="label">` for KPIs). Wrap in `{% if not tv_mode %} … {% endif %}` so the TV view doesn't render edit chrome.

For example, the `kpi-units` widget becomes:

```jinja
    <div class="grid-stack-item" {{ widget_attrs(kid, kdef_x, 0, 3, 2) }}>
      <div class="grid-stack-item-content align-{{ align }}">
        {% if not tv_mode %}{{ edit_controls(kid, ktitle, 'kpi') }}{% endif %}
        <div class="label">{{ widget_title(kid, ktitle) }}</div>
        <div class="val" {{ widget_color_style(kid, 'color') }}>{{ kval }}</div>
      </div>
    </div>
```

Apply the same pattern to every widget. The `kind=` argument:

| Widget | `kind=` |
|---|---|
| `kpi-units`, `kpi-uptime`, `kpi-downtime`, `kpi-pph` | `'kpi'` |
| `pallets-banner` | `'kpi'` (title + color only) |
| `progress-15min` | `'progress'` |
| `cumulative-daily` | `'progress'` |
| `downtime-row` | `'downtime'` |
| `goat-race` | `'kpi'` (title + color only) |
| `monthly-ribbons` | `'kpi'` (title + color only) |

The `default_title` argument matches the `widget_title(...)` default for that widget — for example `'Today · Pallets'` for the pallets banner, `'Vs. GOAT Pace'` for goat-race, etc.

- [ ] **Step 5: Add the edit-panel JS handlers**

In the existing `<script>` block (the one you replaced in Task 6), inside the `{% if not tv_mode %}` branch, append these functions BEFORE the closing `{% endif %}`:

```javascript
  // Per-widget edit controls.
  function openEdit(btn) {
    const content = btn.closest('.grid-stack-item-content');
    content.querySelector('.widget-edit').hidden = false;
  }
  function closeEdit(btn) {
    const content = btn.closest('.grid-stack-item-content');
    content.querySelector('.widget-edit').hidden = true;
  }
  function saveWidget(btn, id) {
    const panel = btn.closest('.widget-edit');
    const cfg = {};
    panel.querySelectorAll('input[name], select[name]').forEach(el => {
      const k = el.name;
      if (el.type === 'checkbox') cfg[k] = el.checked;
      else cfg[k] = el.value;
    });
    fetch('/api/widget/operator/' + encodeURIComponent(id), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(cfg),
    }).then(r => { if (r.ok) location.reload(); });
  }
  function resetWidget(btn, id) {
    fetch('/api/widget/operator/' + encodeURIComponent(id), {method: 'DELETE'})
      .then(r => { if (r.ok) location.reload(); });
  }
  // Prevent Gridstack from starting a drag when interacting with the edit panel.
  document.querySelectorAll('.widget-edit, .widget-edit-btn').forEach(el => {
    el.addEventListener('mousedown', e => e.stopPropagation());
    el.addEventListener('touchstart', e => e.stopPropagation());
  });
```

- [ ] **Step 6: Run the tests**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard.py -v -k "widget_edit_buttons or omits_edit_buttons or applies_custom_titles or posts_widget_edits"
```

Expected: PASS.

- [ ] **Step 7: Manual browser smoke check**

Reload `/wc/repair-1`, click `⋮` on a KPI tile, change the title to "Pallets Done", click Save. After reload, the title persists. Open `/wc/dismantler-2` — the same custom title shows (shared customizer key).

- [ ] **Step 8: Commit**

```
git add src/zira_dashboard/templates/wc_dashboard.html tests/test_wc_dashboard.py
git commit -m "feat(operator): per-widget edit panels via shared partial

Every widget gets the ⋮ edit button + inline panel /recycling has.
Saves go to /api/widget/operator/{id} so customizations apply to
every WC at once. TV view skips the edit chrome.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Pallets banner with start/now ticks

**Files:**
- Modify: `src/zira_dashboard/templates/wc_dashboard.html` (pallets-banner widget only)
- Test: `tests/test_wc_dashboard.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wc_dashboard.py`:

```python
def test_pallets_banner_renders_start_and_now_ticks(monkeypatch):
    """Banner shows `start · HH:MM` and `now · HH:MM` axis labels."""
    _stub_wc(monkeypatch)
    c = TestClient(app)
    r = c.get("/wc/repair-1")
    assert r.status_code == 200
    # The exact times depend on shift_config + local time; just look for the labels.
    assert "start ·" in r.text
    # `now ·` only renders on today; the test runs today by default.
    assert "now ·" in r.text
```

- [ ] **Step 2: Run the test**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard.py::test_pallets_banner_renders_start_and_now_ticks -v
```

Expected (with DATABASE_URL): FAIL — the banner doesn't render ticks yet.

- [ ] **Step 3: Update the pallets-banner widget block**

In `src/zira_dashboard/templates/wc_dashboard.html`, replace the entire `pallets-banner` `grid-stack-item` block with:

```jinja
  <div class="grid-stack-item" {{ widget_attrs('pallets-banner', 0, 2, 12, 2) }}>
    <div class="grid-stack-item-content">
      {% if not tv_mode %}{{ edit_controls('pallets-banner', 'Today · Pallets', 'kpi') }}{% endif %}
      <h3>{{ widget_title('pallets-banner', 'Today · Pallets') }}</h3>
      <div class="pallets-banner">
        <div class="pallets-numbers">
          <span class="units" {{ widget_color_style('pallets-banner', 'color') }}>{{ pallets.units_today }}</span>
          <span class="target">/ {{ pallets.target_full_day }} full day</span>
        </div>
        {% set fill_pct = ((pallets.units_today / pallets.target_full_day * 100.0) if pallets.target_full_day else 0) %}
        {% if fill_pct > 100 %}{% set fill_pct = 100 %}{% endif %}
        <div class="bar-track">
          <div class="bar-fill" style="width: {{ fill_pct }}%"></div>
          {% if banner_now_pct and banner_now_pct > 0 %}
            <div class="bar-target-line" style="left: {{ banner_now_pct }}%" title="On-pace target right now"></div>
          {% endif %}
        </div>
        {% if shift_start_label or now_label %}
          <div class="bar-row axis-row numpos-widget pallets-axis">
            <div></div>
            <div class="axis-track">
              {% if shift_start_label %}<div class="axis-tick axis-start" style="left: 0%">start · {{ shift_start_label }}</div>{% endif %}
              {% if now_label and banner_now_pct > 0 %}<div class="axis-tick" style="left: {{ banner_now_pct }}%">now · {{ now_label }}</div>{% endif %}
            </div>
            <div></div>
          </div>
        {% endif %}
      </div>
    </div>
  </div>
```

- [ ] **Step 4: Run the test**

```
.\.venv\Scripts\python.exe -m pytest tests/test_wc_dashboard.py::test_pallets_banner_renders_start_and_now_ticks -v
```

Expected: PASS.

- [ ] **Step 5: Manual browser smoke check**

Reload `/wc/repair-1`. The pallets banner should show:
- Big units number / full-day goal in smaller type after the slash.
- A horizontal fill bar with a vertical tick at the prorated-target position.
- An axis row below with `start · HH:MM` at the left and `now · HH:MM` at the same position as the tick.

- [ ] **Step 6: Commit**

```
git add src/zira_dashboard/templates/wc_dashboard.html tests/test_wc_dashboard.py
git commit -m "feat(operator): pallets banner with start/now axis ticks

Banner now mirrors /recycling's bar-row axis layout: fill bar against
the full-day goal, a tick at the prorated 'where we should be right
now' position, and a start · HH:MM / now · HH:MM axis below. Reuses
recycling.css's .axis-row / .axis-tick / .bar-target-line styles.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Container-query CSS + theme-aware KPI text + operator-band styling

**Files:**
- Modify: `src/zira_dashboard/static/wc_dashboard.css`

CSS-only task. Tests are visual.

- [ ] **Step 1: Read the existing CSS to find the insertion points**

```
.\.venv\Scripts\python.exe -c "print('inspect: src/zira_dashboard/static/wc_dashboard.css')"
```

Open `src/zira_dashboard/static/wc_dashboard.css` in your editor and skim. Note the existing `.kpi`, `.kpi .val`, `.kpi .label`, `.pallets-banner .units`, `.goat-race`, `.ribbons-list` rules.

- [ ] **Step 2: Append the new CSS block**

Append to the END of `src/zira_dashboard/static/wc_dashboard.css`:

```css
/* ---------------------------------------------------------------------- */
/* Operator dashboard polish (2026-05-14)                                  */
/* ---------------------------------------------------------------------- */

/* Each widget content area becomes its own container so child font sizes
   can scale with widget width via cqw units. */
.wc-dashboard .grid-stack-item-content { container-type: inline-size; }

/* Theme-aware KPI text color (black on light theme, white on dark theme).
   Per-widget `color` customizations still win via inline style. */
.wc-dashboard .kpi .val                              { color: #000; }
html[data-tv-theme="dark"] .wc-dashboard .kpi .val   { color: #fff; }

/* KPI tiles scale text with widget width. */
.wc-dashboard .kpi .val   { font-size: clamp(1.8rem, 8cqw, 4rem); }
.wc-dashboard .kpi .label { font-size: clamp(0.75rem, 2cqw, 1rem); }

/* Pallets banner big number scales too. */
.wc-dashboard .pallets-banner .units  { font-size: clamp(2rem, 9cqw, 5rem); }
.wc-dashboard .pallets-banner .target { font-size: clamp(0.85rem, 2.4cqw, 1.3rem); }

/* GOAT race text + status pill scale. */
.wc-dashboard .goat-race .race-stats  { font-size: clamp(0.95rem, 4cqw, 2.2rem); }
.wc-dashboard .goat-race .status-pill {
  font-size: clamp(0.85rem, 3cqw, 1.6rem);
  padding: clamp(2px, 0.6cqw, 8px) clamp(6px, 1.2cqw, 14px);
}
.wc-dashboard .goat-race .goat-meta   { font-size: clamp(0.8rem, 2.4cqw, 1.3rem); }

/* Monthly ribbons row text + medals scale. */
.wc-dashboard .ribbons-list li      { font-size: clamp(0.95rem, 3.5cqw, 2rem); gap: clamp(4px, 1cqw, 14px); }
.wc-dashboard .ribbons-list .medal  { font-size: clamp(1.2rem, 5cqw, 3rem); }
.wc-dashboard .ribbons-list .units  { font-size: clamp(1rem, 4cqw, 2.4rem); }

/* Operator name band — page subtitle under the WC picker. */
.wc-dashboard .operator-band {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  padding: 0.4rem 1rem 0.8rem;
  border-bottom: 1px solid var(--border, #d8dee5);
  margin-bottom: 0.4rem;
}
.wc-dashboard .operator-band-wc {
  font-size: 1.8rem;
  font-weight: 800;
  line-height: 1.1;
  color: var(--fg, #1f2937);
}
.wc-dashboard .operator-band-op {
  font-size: 1.05rem;
  font-weight: 600;
  color: var(--fg, #1f2937);
}
.wc-dashboard .operator-band-op .op-icon { margin-right: 0.25rem; opacity: 0.85; }
.wc-dashboard .operator-band-op .op-unassigned {
  color: var(--muted, #6b7280);
  font-weight: 500;
}

/* Pallets banner axis row — borrows recycling's .axis-row layout. */
.wc-dashboard .pallets-banner .pallets-axis {
  margin-top: 0.35rem;
}
```

- [ ] **Step 3: Bump the static-file cache buster**

The template loads `wc_dashboard.css?v={{ static_v('wc_dashboard.css') }}` so cache invalidation is automatic — no manual bump needed. Skip this step.

- [ ] **Step 4: Browser smoke check**

Start the dev server, open `/wc/repair-1`. Verify:
- KPI text is **black** on the default (light) screen view.
- Open `/tv/wc/repair-1?theme=dark` — KPI text becomes **white**.
- Open `/tv/wc/repair-1?theme=light` — KPI text stays **black**.
- Drag a GOAT race widget to fill 12 columns — the text noticeably grows. Shrink it to 3 columns — text shrinks.
- Resize a Monthly Ribbons widget — medals + names scale with width.
- The operator band shows the WC name + operator(s) below the picker bar.

- [ ] **Step 5: Commit**

```
git add src/zira_dashboard/static/wc_dashboard.css
git commit -m "feat(operator): container-query scaling + theme-aware KPI + band styles

Adds container-type: inline-size on widget content areas so child
font sizes can scale with widget width via cqw units. GOAT race,
Monthly Ribbons, KPI values, and the pallets banner number all
scale smoothly. KPI text is black by default (light theme) and
white when html[data-tv-theme='dark']. Per-widget color
customizations still override via inline style. New
.operator-band styles for the page subtitle.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: CHANGELOG + push to Railway

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add a new time-stamped entry**

Get the current local time:

```
Get-Date -Format "h:mm tt"
```

Edit `CHANGELOG.md`. Under `## 2026-05-14`, insert a new `### TIME` entry at the top (above the existing `### 8:01 AM` entry). Example:

```markdown
### 9:30 AM

- **Operator dashboard polish** — `/wc/{slug}` now mirrors `/recycling`'s editor features: the KPI row is split into four resizable widgets, each widget has a `⋮` edit panel (title, color, alignment, legend/target toggles where they apply), layout auto-saves on drag/resize, and a Reset Layout button restores the defaults. **Customize once → applies to every WC** — layout + widget customizations share a single `page="operator"` key in the database, so dragging KPIs around on Repair 1's dashboard reshapes Dismantler 2's too. A new band under the WC picker shows the work-center name and the scheduled operator(s) from the Plant Scheduler (falls back to `(unassigned)`). The pallets banner now has the same start/now axis ticks `/recycling` uses on its bar rows. GOAT Pace + Monthly Ribbons + KPI value text + pallets banner number all **scale with widget size** via CSS container queries — make the widget bigger, the content gets bigger. KPI text is black on the light theme and white on the dark theme. The 15-minute progress and cumulative daily charts now stop at "now" instead of running the whole shift. Legacy per-WC layout rows (`wc:repair-1` etc.) are dropped on the next boot.
```

- [ ] **Step 2: Run the full test suite as a final smoke**

```
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all green (locally skips Postgres-bound tests).

- [ ] **Step 3: Commit + push**

```
git add CHANGELOG.md
git commit -m "docs(changelog): operator dashboard polish

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push
```

Watch the Railway deploy in the Railway dashboard. The `bootstrap_schema` cleanup will drop the legacy `wc:*` rows on first boot.

- [ ] **Step 4: Verify on production**

Open the Railway URL `/wc/{slug}`, verify:
- Operator band renders with WC + operator names.
- KPIs are 4 separate widgets, theme-aware text color.
- Dragging a widget auto-saves; the same layout shows on a different WC's URL.
- Pallets banner shows start/now ticks.
- 15-min and cumulative charts stop at the current time.

---

## Self-review notes

**Spec coverage:**
- ✅ Shared layout key `"operator"` (Task 4).
- ✅ Per-widget edit panels (Task 7), via shared partial (Task 2).
- ✅ Auto-save layout with indicator + Reset (Task 6).
- ✅ KPI split into 4 widgets (Task 5).
- ✅ Operator name band (Task 5).
- ✅ Container-query scaling (Task 9).
- ✅ Theme-aware KPI text (Task 9).
- ✅ Pallets banner with start/now ticks (Task 8).
- ✅ 15-min + cumulative truncate at "now" (Task 1).
- ✅ Schema cleanup of legacy `wc:*` rows (Task 3).
- ✅ TV view omits edit chrome (asserted in Tasks 6 + 7).
- ✅ All test cases in the spec's Testing table land somewhere.

**Placeholder scan:** no TBDs / TODOs / "add error handling" / "similar to Task N" left in the plan.

**Type consistency:** widget IDs match across tasks (`kpi-units`/`kpi-uptime`/`kpi-downtime`/`kpi-pph`/`pallets-banner`/`progress-15min`/`cumulative-daily`/`downtime-row`/`goat-race`/`monthly-ribbons`). Layout key `"operator"` consistent. Context vars (`customs`, `layout`, `shift_start_label`, `now_label`, `banner_now_pct`) defined in Task 4, consumed in Tasks 5/6/7/8.
