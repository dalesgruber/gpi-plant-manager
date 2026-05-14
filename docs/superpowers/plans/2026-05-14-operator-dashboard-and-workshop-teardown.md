# Operator Dashboard + Workshop Tear-Down Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the entire widget workshop + custom dashboards system (~30 files, 5 DB tables) and replace it with a single Operator dashboard at `/wc/{slug}` that mirrors `/recycling`'s visual style, scoped to one WC, with a top-of-page WC picker. Sub-nav becomes a fixed 4-tab strip.

**Architecture:** Pure subtraction + one rewrite. Tear out the workshop scaffolding (modules + routes + templates + tables + tests). Replace `_dashboards_subnav.html` with a fixed 4-tab version. Rewrite `wc_dashboard.html` to use inline HTML that matches `/recycling`'s widget markup, with widgets scoped to a single WC. Add a `/operator` redirect and a small `kpi_tiles()` helper.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, psycopg2 + Postgres, pytest, gridstack 10.3. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-14-operator-dashboard-and-workshop-teardown-design.md`

---

## File Structure

**Deleted files (~30):**
- `src/zira_dashboard/widget_types.py`
- `src/zira_dashboard/widget_data.py`
- `src/zira_dashboard/widget_definitions_store.py`
- `src/zira_dashboard/custom_dashboards_store.py`
- `src/zira_dashboard/tv_templates_store.py`
- `src/zira_dashboard/pinned_dashboards_store.py`
- `src/zira_dashboard/dashboard_catalog.py`
- `src/zira_dashboard/routes/widgets.py`
- `src/zira_dashboard/routes/custom_dashboards.py`
- `src/zira_dashboard/routes/tv_templates.py`
- `src/zira_dashboard/templates/widgets.html`
- `src/zira_dashboard/templates/dashboards.html`
- `src/zira_dashboard/templates/custom_dashboard.html`
- `src/zira_dashboard/templates/widgets/_widget_pallets_by_wc.html`
- `src/zira_dashboard/templates/widgets/_widget_pallets_banner.html`
- `src/zira_dashboard/templates/widgets/_widget_daily_progress.html`
- `src/zira_dashboard/templates/widgets/_widget_cumulative.html`
- `src/zira_dashboard/templates/widgets/_widget_kpi.html`
- `src/zira_dashboard/templates/widgets/_widget_goat_race.html`
- `src/zira_dashboard/templates/widgets/_widget_ribbons.html`
- `src/zira_dashboard/templates/widgets/_widget_downtime.html`
- `src/zira_dashboard/templates/_widget_render.html`
- `tests/test_widget_types.py`
- `tests/test_widget_data.py`
- `tests/test_widget_definitions_store.py`
- `tests/test_widgets_routes.py`
- `tests/test_custom_dashboards_store.py`
- `tests/test_custom_dashboards_routes.py`
- `tests/test_pinned_dashboards_store.py`
- `tests/test_dashboard_catalog.py`
- `tests/test_tv_templates_store.py`
- `tests/test_tv_templates_routes.py`

**Modified files:**
- `src/zira_dashboard/db.py` — append `DROP TABLE IF EXISTS` migrations + `DELETE FROM tv_displays WHERE kind='custom'`
- `src/zira_dashboard/app.py` — drop deleted route imports/includes, drop seed calls
- `src/zira_dashboard/routes/tv_displays.py` — drop the `kind='custom'` branch in `/tv/d/{slug}`
- `src/zira_dashboard/routes/settings.py` — drop the `dashboard_catalog` / `custom_dashboards_store` / `tv_templates_store` imports; inline the picker options
- `src/zira_dashboard/routes/wc_dashboard.py` — add `/operator` redirect; refactor `_render_wc_dashboard` to use the new helpers
- `src/zira_dashboard/wc_dashboard_data.py` — add `kpi_tiles()` and `fifteen_min_progress_buckets()` helpers
- `src/zira_dashboard/templates/_dashboards_subnav.html` — full rewrite (fixed 4-tab)
- `src/zira_dashboard/templates/_settings_tvs.html` — drop the Custom optgroup; remove `all_dashboards_for_picker` references
- `src/zira_dashboard/templates/wc_dashboard.html` — full rewrite (operator dashboard layout)
- `src/zira_dashboard/templates/index.html` — drop "My Dashboards" top-nav link
- `src/zira_dashboard/templates/recycling.html` — drop "My Dashboards" top-nav link
- `src/zira_dashboard/templates/new_vs.html` — drop "My Dashboards" top-nav link
- `src/zira_dashboard/templates/_staffing_base.html` — drop "My Dashboards" top-nav link
- `src/zira_dashboard/templates/settings.html` — drop "My Dashboards" top-nav link
- `src/zira_dashboard/static/dashboards-subnav.css` — simplify (no `.pinned-tabs`/`.meta-tabs` split)
- `CHANGELOG.md`

---

## Conventions

- Python interpreter: `.venv/Scripts/python.exe`.
- Postgres-touching tests gate on `DATABASE_URL` via module-level `pytestmark`.
- Commit messages: `feat(operator):` / `chore(teardown):` / `docs:`.
- File deletion via `git rm`.

---

## Task 1: Schema migrations + app.py cleanup

**Files:**
- Modify: `src/zira_dashboard/db.py`
- Modify: `src/zira_dashboard/app.py`

- [ ] **Step 1: Append `DROP TABLE` migrations to `_SCHEMA_DDL` in `db.py`**

Open `src/zira_dashboard/db.py`. Find the closing `"""` of `_SCHEMA_DDL` (after the `pinned_dashboards` block). Insert BEFORE the closing `"""`:

```sql
-- Tear-down (2026-05-14): workshop + custom dashboards experiment is gone.
-- DROP order respects FK references: dashboard_widgets first
-- (it references both custom_dashboards and widget_definitions).
DROP TABLE IF EXISTS dashboard_widgets;
DROP TABLE IF EXISTS custom_dashboards;
DROP TABLE IF EXISTS widget_definitions;
DROP TABLE IF EXISTS tv_dashboard_templates;
DROP TABLE IF EXISTS pinned_dashboards;
DELETE FROM tv_displays WHERE kind = 'custom';
```

- [ ] **Step 2: Update `app.py` imports**

Open `src/zira_dashboard/app.py`. Find the `from .routes import (...)` block. Remove these three lines:

```python
    custom_dashboards,
    tv_templates,
    widgets,
```

(Leave `tv_displays` in — that's a different module, the TV registry.)

- [ ] **Step 3: Update `app.py` lifespan seeds**

Find:

```python
    from . import tv_displays_store, widget_definitions_store, pinned_dashboards_store
    tv_displays_store.seed_defaults_if_empty()
    widget_definitions_store.seed_defaults_if_empty()
    pinned_dashboards_store.seed_defaults_if_empty()
```

Replace with:

```python
    from . import tv_displays_store
    tv_displays_store.seed_defaults_if_empty()
```

- [ ] **Step 4: Drop deleted-router `include_router` calls**

Find and DELETE these three lines:

```python
app.include_router(custom_dashboards.router)
app.include_router(widgets.router)
app.include_router(tv_templates.router)
```

(Keep `app.include_router(tv_displays.router)`.)

- [ ] **Step 5: Verify app boots**

```
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; print('OK')"
```

This will FAIL — the deleted route modules are referenced by `routes/settings.py` and `routes/tv_displays.py`. That's fine; Task 2 and Task 3 fix those.

To still verify the schema migration parses, run:

```
.venv/Scripts/python.exe -m pytest tests/test_db.py -v 2>&1 | tail -5
```

Expected: tests SKIP without `DATABASE_URL`.

- [ ] **Step 6: Commit**

```
git add src/zira_dashboard/db.py src/zira_dashboard/app.py
git commit -m "$(cat <<'EOF'
chore(teardown): drop workshop tables + remove route wiring

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Delete workshop + custom-dashboard files (bulk)

**Files:** see deletion list below. Each removed via `git rm`.

This task is a clean cut. After it lands the app still won't boot (settings.py references dashboard_catalog, tv_displays.py references custom kind) — Task 3 + 4 fix.

- [ ] **Step 1: Delete Python modules and tests**

```
git rm src/zira_dashboard/widget_types.py src/zira_dashboard/widget_data.py
git rm src/zira_dashboard/widget_definitions_store.py
git rm src/zira_dashboard/custom_dashboards_store.py
git rm src/zira_dashboard/tv_templates_store.py
git rm src/zira_dashboard/pinned_dashboards_store.py
git rm src/zira_dashboard/dashboard_catalog.py
git rm src/zira_dashboard/routes/widgets.py
git rm src/zira_dashboard/routes/custom_dashboards.py
git rm src/zira_dashboard/routes/tv_templates.py
git rm tests/test_widget_types.py tests/test_widget_data.py
git rm tests/test_widget_definitions_store.py tests/test_widgets_routes.py
git rm tests/test_custom_dashboards_store.py tests/test_custom_dashboards_routes.py
git rm tests/test_pinned_dashboards_store.py tests/test_dashboard_catalog.py
git rm tests/test_tv_templates_store.py tests/test_tv_templates_routes.py
```

- [ ] **Step 2: Delete templates**

```
git rm src/zira_dashboard/templates/widgets.html
git rm src/zira_dashboard/templates/dashboards.html
git rm src/zira_dashboard/templates/custom_dashboard.html
git rm src/zira_dashboard/templates/_widget_render.html
git rm -r src/zira_dashboard/templates/widgets/
```

- [ ] **Step 3: Verify the files are gone**

```
git status --short | findstr /B "D "
```

Expected: 30 lines starting with `D ` (deleted).

- [ ] **Step 4: Commit**

```
git commit -m "$(cat <<'EOF'
chore(teardown): delete workshop + custom dashboards modules / templates / tests

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Simplify `routes/tv_displays.py` (drop `custom` kind)

**Files:**
- Modify: `src/zira_dashboard/routes/tv_displays.py`

The `/tv/d/{slug}` route currently branches on `kind` and has a path for `custom`. Drop that branch and the `_dashboard_removed_html` helper.

- [ ] **Step 1: Drop the custom branch**

Open `src/zira_dashboard/routes/tv_displays.py`. Find:

```python
    if kind == "custom":
        from .. import custom_dashboards_store
        dash_id = row.get("custom_dashboard_id")
        if dash_id is None:
            return HTMLResponse(
                _dashboard_removed_html(row["name"]),
                status_code=404,
            )
        dash = custom_dashboards_store.get_dashboard(int(dash_id))
        if dash is None:
            return HTMLResponse(
                _dashboard_removed_html(row["name"]),
                status_code=404,
            )
        from .custom_dashboards import _render_dashboard
        return _render_dashboard(
            request, slug=dash["slug"], tv_mode=True, tv_theme=tv_theme,
        )
    return JSONResponse(
        {"error": f"unknown kind: {kind}"}, status_code=500,
    )
```

Replace with:

```python
    return JSONResponse(
        {"error": f"unknown kind: {kind}"}, status_code=500,
    )
```

- [ ] **Step 2: Drop the `_dashboard_removed_html` helper**

In the same file, find and delete the entire function:

```python
def _dashboard_removed_html(display_name: str) -> str:
    return (
        f"<!doctype html><html><head><title>Dashboard removed</title>"
        f"<style>body{{font-family:system-ui;padding:3rem;text-align:center}}"
        f"a{{color:#16a34a}}</style></head><body>"
        f"<h1>Custom dashboard removed</h1>"
        f"<p>The display \"{display_name}\" was pointing at a custom dashboard that no longer exists.</p>"
        f"<p><a href=\"/settings?section=tvs\">Go to TVs settings</a></p>"
        f"</body></html>"
    )
```

- [ ] **Step 3: Drop the custom_dashboard_id validation in `post_display`**

In the same file, find:

```python
    elif kind == "custom":
        from .. import custom_dashboards_store
        wc_name = None
        raw_id = body.get("custom_dashboard_id")
        if not isinstance(raw_id, int):
            return JSONResponse(
                {"ok": False, "error": "custom_dashboard_id required when kind=custom"},
                status_code=400,
            )
        if custom_dashboards_store.get_dashboard(raw_id) is None:
            return JSONResponse(
                {"ok": False, "error": f"unknown custom dashboard id: {raw_id}"},
                status_code=400,
            )
        custom_dashboard_id = raw_id
    else:
```

Replace with:

```python
    else:
```

- [ ] **Step 4: Tighten the kind allow-list**

In the same file, find:

```python
    if kind not in ("vs_recycling", "vs_new", "wc", "custom"):
        return JSONResponse({"ok": False, "error": "kind invalid"}, status_code=400)
    custom_dashboard_id = None
```

Replace with:

```python
    if kind not in ("vs_recycling", "vs_new", "wc"):
        return JSONResponse({"ok": False, "error": "kind invalid"}, status_code=400)
    custom_dashboard_id = None
```

- [ ] **Step 5: Verify**

```
.venv/Scripts/python.exe -c "from zira_dashboard.routes import tv_displays; print('OK')"
```

Expected: `OK`. The module no longer references `custom_dashboards_store`.

- [ ] **Step 6: Commit**

```
git add src/zira_dashboard/routes/tv_displays.py
git commit -m "$(cat <<'EOF'
chore(teardown): drop custom-kind branches from /tv/d/{slug}

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Update `routes/settings.py` + TVs picker template

**Files:**
- Modify: `src/zira_dashboard/routes/settings.py`
- Modify: `src/zira_dashboard/templates/_settings_tvs.html`

- [ ] **Step 1: Update `routes/settings.py` TVs section**

Open `src/zira_dashboard/routes/settings.py`. Find:

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

Replace with:

```python
    tv_displays_rows: list[dict] = []
    all_dashboards_for_picker: list[dict] = []
    if section == "tvs":
        from .. import tv_displays_store
        from ..wc_dashboard_data import slug_for_wc
        tv_displays_rows = tv_displays_store.list_displays()
        all_dashboards_for_picker = [
            {"kind": "vs_recycling", "ref": "", "name": "Recycling VS"},
            {"kind": "vs_new", "ref": "", "name": "New VS"},
            {"kind": "vs_work_centers", "ref": "", "name": "Work Centers"},
        ]
        for loc in staffing.LOCATIONS:
            all_dashboards_for_picker.append(
                {"kind": "wc", "ref": loc.name, "name": loc.name}
            )
```

Then in the same handler, find the `return templates.TemplateResponse(...)` context dict. Remove these keys (they no longer have a source):

```python
            "tv_templates_rows": tv_templates_rows,
            "custom_dashboards_rows": custom_dashboards_rows,
```

(Keep `tv_displays_rows`, `all_dashboards_for_picker`, `wc_locations_for_picker`.)

- [ ] **Step 2: Update `_settings_tvs.html`**

Open `src/zira_dashboard/templates/_settings_tvs.html`. Two edits.

**2a — drop the "Custom" optgroup in the per-row picker.** Find:

```jinja
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
```

Delete this whole block. Also drop the `<optgroup label="Built-in">` opening and closing tags around the built-in options (only one group remains — no need for an optgroup).

**2b — drop the "Custom" optgroup in the Add form.** Find:

```jinja
      <optgroup label="Custom">
        {% for dash in all_dashboards_for_picker %}
          {% if dash.kind == 'custom' %}
            <option value="custom|{{ dash.id }}">{{ dash.name }}</option>
          {% endif %}
        {% endfor %}
      </optgroup>
```

Delete this whole block. Also drop the `<optgroup label="Built-in">` wrapper (one group only).

**2c — drop the Layout Templates section** at the bottom of the file. Find:

```jinja
  <h2 style="margin-top: 1.5rem">Layout Templates</h2>
  <p class="note">
    Templates saved from any <code>/wc/{slug}</code> editor view. Delete to clean up;
    templates can be re-saved any time from the editor.
  </p>

  <table class="tv-templates-table">
    ...
  </table>
```

Delete the entire block (from `<h2 ... >Layout Templates</h2>` through the closing `</table>`).

**2d — drop the `.tv-template-delete` JS handler** near the bottom of the file:

```javascript
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
```

Delete the whole block.

- [ ] **Step 3: Verify parse + app boot**

```
.venv/Scripts/python.exe -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'), autoescape=True); env.parse(open('src/zira_dashboard/templates/_settings_tvs.html', encoding='utf-8').read()); env.parse(open('src/zira_dashboard/templates/settings.html', encoding='utf-8').read()); print('parse OK')"
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; print('app OK')"
```

Expected: `parse OK`, `app OK`. The app boots cleanly now that the deleted-module references are gone.

- [ ] **Step 4: Commit**

```
git add src/zira_dashboard/routes/settings.py src/zira_dashboard/templates/_settings_tvs.html
git commit -m "$(cat <<'EOF'
chore(teardown): TVs settings picker — drop custom kind + templates table

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: New `_dashboards_subnav.html` + simpler CSS + drop "My Dashboards" top-nav link

**Files:**
- Modify: `src/zira_dashboard/templates/_dashboards_subnav.html`
- Modify: `src/zira_dashboard/static/dashboards-subnav.css`
- Modify: `src/zira_dashboard/templates/index.html`
- Modify: `src/zira_dashboard/templates/recycling.html`
- Modify: `src/zira_dashboard/templates/new_vs.html`
- Modify: `src/zira_dashboard/templates/_staffing_base.html`
- Modify: `src/zira_dashboard/templates/settings.html`

- [ ] **Step 1: Replace `templates/_dashboards_subnav.html`**

Open `src/zira_dashboard/templates/_dashboards_subnav.html`. Replace the entire file with:

```jinja
{# Dashboards sub-nav. Fixed 4-tab strip — no pinning, no workshop.

   Context required:
     active_dashboard_key — one of 'vs_recycling', 'vs_new',
                            'vs_work_centers', or a string starting with
                            'wc:' (any WC = Operator tab active).
#}
<nav class="dash-subnav">
  <a href="/recycling"
     class="subnav-item {% if active_dashboard_key == 'vs_recycling' %}active{% endif %}">
    Recycling VS
  </a>
  <a href="/new-vs"
     class="subnav-item {% if active_dashboard_key == 'vs_new' %}active{% endif %}">
    New VS
  </a>
  <a href="/operator"
     class="subnav-item {% if active_dashboard_key and active_dashboard_key.startswith('wc:') %}active{% endif %}">
    Operator
  </a>
  <a href="/work-centers"
     class="subnav-item {% if active_dashboard_key == 'vs_work_centers' %}active{% endif %}">
    Work Centers
  </a>
</nav>
```

- [ ] **Step 2: Replace `static/dashboards-subnav.css`**

Open `src/zira_dashboard/static/dashboards-subnav.css`. Replace contents with:

```css
.dash-subnav {
  display: flex;
  gap: 0.5rem;
  padding: 0.4rem 1rem;
  border-bottom: 1px solid var(--border, #d8dee5);
  background: var(--panel, #fff);
  align-items: center;
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

- [ ] **Step 3: Drop "My Dashboards" link in 5 top-nav templates**

For each of `index.html`, `recycling.html`, `new_vs.html`, `_staffing_base.html`, `settings.html`: find and delete the line containing `<a href="/dashboards">My Dashboards</a>` (each template's variant — the inline-styled version on `index.html`, the plain version on the others). The link text "My Dashboards" inside an `<a>` pointing at `/dashboards` is the unique marker.

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

Repeat for each of the 5 templates. Use Read first if the exact format isn't obvious.

- [ ] **Step 4: Verify**

```
.venv/Scripts/python.exe -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'), autoescape=True); [env.parse(open(f'src/zira_dashboard/templates/{n}', encoding='utf-8').read()) for n in ['_dashboards_subnav.html','index.html','recycling.html','new_vs.html','_staffing_base.html','settings.html']]; print('parse OK')"
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; print('app OK')"
.venv/Scripts/python.exe -m pytest 2>&1 | tail -3
```

Expected: parse OK, app OK, no test regressions (~240 passed).

- [ ] **Step 5: Commit**

```
git add src/zira_dashboard/templates/_dashboards_subnav.html src/zira_dashboard/static/dashboards-subnav.css src/zira_dashboard/templates/index.html src/zira_dashboard/templates/recycling.html src/zira_dashboard/templates/new_vs.html src/zira_dashboard/templates/_staffing_base.html src/zira_dashboard/templates/settings.html
git commit -m "$(cat <<'EOF'
feat(operator): fixed 4-tab sub-nav + drop My Dashboards top-nav link

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: New `wc_dashboard_data` helpers — KPI tiles + progress buckets

**Files:**
- Modify: `src/zira_dashboard/wc_dashboard_data.py`

Two new helpers:
- `kpi_tiles(wc_name, day)` → `{units_today, up_time_pct, downtime_minutes, pallets_per_hour, hours_elapsed}`
- `fifteen_min_progress_buckets(wc_name, day)` → list of `{label, actual, target, in_progress}` for the `progress_chart` macro

- [ ] **Step 1: Append `kpi_tiles` to `wc_dashboard_data.py`**

At the end of the file, append:

```python
def kpi_tiles(wc_name: str, day: date) -> dict:
    """KPI tile values for the operator dashboard. Single-WC scope.

    Returns:
      units_today (int)
      downtime_minutes (int)
      hours_elapsed (float) — elapsed shift hours so far (0 on weekends/pre-shift)
      up_time_pct (float) — 0..100; 0 if no shift has elapsed
      pallets_per_hour (float, 1 decimal)
    """
    from . import shift_config
    units = _units_today_for_wc(wc_name, day)
    report = downtime_report(wc_name, day) or {}
    down = int(report.get("total_minutes", 0))
    try:
        full_minutes = shift_config.productive_minutes_per_day()
    except Exception:
        full_minutes = 480
    elapsed_minutes = int(full_minutes * _shift_elapsed_fraction(day))
    hours_elapsed = elapsed_minutes / 60.0 if elapsed_minutes > 0 else 0.0
    if elapsed_minutes > 0:
        up_time_pct = max(0.0, (elapsed_minutes - down) / elapsed_minutes * 100.0)
    else:
        up_time_pct = 0.0
    pallets_per_hour = round(units / hours_elapsed, 1) if hours_elapsed > 0 else 0.0
    return {
        "units_today": units,
        "downtime_minutes": down,
        "hours_elapsed": round(hours_elapsed, 2),
        "up_time_pct": round(up_time_pct, 1),
        "pallets_per_hour": pallets_per_hour,
    }


def fifteen_min_progress_buckets(wc_name: str, day: date) -> dict:
    """Per-15-min progress buckets in the shape /recycling's progress_chart
    macro consumes:
      {buckets: [{label, actual, target, in_progress}, ...], bucket_target}
    """
    from . import shift_config
    raw = fifteen_min_increments(wc_name, day) or []
    if not raw:
        return {"buckets": [], "bucket_target": 0}
    try:
        full_minutes = shift_config.productive_minutes_per_day()
    except Exception:
        full_minutes = 480
    elapsed = int(full_minutes * _shift_elapsed_fraction(day))
    try:
        shift_start = shift_config.shift_start_for(day)
    except Exception:
        shift_start = None
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
            "actual": int(b.get("units") or 0),
            "target": int(b.get("target") or 0),
            "in_progress": offset <= elapsed < offset + 15,
        })
    bucket_target = next((b["target"] for b in buckets if b["target"]), 0)
    return {"buckets": buckets, "bucket_target": bucket_target}
```

- [ ] **Step 2: Add `timedelta` import**

Find the existing imports at the top of `wc_dashboard_data.py`. There's already:

```python
from datetime import date, datetime, timezone
```

Replace with:

```python
from datetime import date, datetime, timedelta, timezone
```

- [ ] **Step 3: Verify**

```
.venv/Scripts/python.exe -c "from zira_dashboard import wc_dashboard_data; print('OK')"
.venv/Scripts/python.exe -m pytest 2>&1 | tail -3
```

Expected: `OK`. Full test suite unchanged (the new helpers have no tests yet — they're exercised by the route render in Task 7).

- [ ] **Step 4: Commit**

```
git add src/zira_dashboard/wc_dashboard_data.py
git commit -m "$(cat <<'EOF'
feat(operator): kpi_tiles + fifteen_min_progress_buckets helpers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `/operator` redirect + rewrite `wc_dashboard.html`

**Files:**
- Modify: `src/zira_dashboard/routes/wc_dashboard.py`
- Modify: `src/zira_dashboard/templates/wc_dashboard.html`

This is the biggest task. Two edits to the route, full rewrite of the template.

- [ ] **Step 1: Add `/operator` redirect route**

Open `src/zira_dashboard/routes/wc_dashboard.py`. At the top, find:

```python
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
```

Replace with:

```python
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
```

Then append a new route at the bottom of the file (before `_pinned_for_subnav`):

```python
@router.get("/operator")
def operator_default():
    """Entry point for the Operator dashboard sub-tab.

    Redirects to the first work center's /wc/{slug} URL. Order is
    staffing.LOCATIONS order — usually alphabetical by name.
    """
    from .. import staffing
    from ..wc_dashboard_data import slug_for_wc
    if not staffing.LOCATIONS:
        return JSONResponse(
            {"error": "no work centers configured — set them up in Settings"},
            status_code=404,
        )
    first = staffing.LOCATIONS[0]
    return RedirectResponse(url=f"/wc/{slug_for_wc(first.name)}", status_code=302)
```

- [ ] **Step 2: Drop `_pinned_for_subnav` helper (and its call)**

It uses `dashboard_catalog` which is gone. Find:

```python
def _pinned_for_subnav():
    from .. import dashboard_catalog
    return dashboard_catalog.pinned_dashboards_for_subnav()
```

Delete the function entirely.

Then in `_render_wc_dashboard`, find the context dict and remove the `"pinned_dashboards"` key:

```python
            "pinned_dashboards": _pinned_for_subnav(),
```

Delete that line.

- [ ] **Step 3: Refactor `_render_wc_dashboard` data prep**

Find the existing `_render_wc_dashboard` body (still uses `widget_data` which is gone — must change). Replace from `def _render_wc_dashboard(...):` through the `return templates.TemplateResponse(...)` block with:

```python
def _render_wc_dashboard(
    request: Request,
    *,
    slug: str,
    tv_mode: bool,
    tv_theme: str,
):
    """Render the Operator dashboard for one WC.

    Layout mirrors /recycling's widget set, scoped to a single WC:
      - KPI tiles row
      - Pallets banner
      - 15-min progress chart
      - Cumulative daily progress
      - Downtime stacked bar
      - GOAT race (group)
      - Monthly Ribbons (group)
    """
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
    # Single-row stacked working/down for this WC (mirrors /recycling shape).
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

    layout_key = f"wc:{slug}"

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
            "wc_options": [{"name": l.name, "slug": wc_dashboard_data.slug_for_wc(l.name)} for l in staffing.LOCATIONS],
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
            "tv_mode": tv_mode,
            "tv_theme": tv_theme,
        },
    )
```

Also remove the obsolete `widget_data` import at the top of the file. Find:

```python
from .. import layout_store, wc_dashboard_data, widget_data, work_centers_store
```

Replace with:

```python
from .. import layout_store, wc_dashboard_data, work_centers_store
```

- [ ] **Step 4: Rewrite `templates/wc_dashboard.html`**

Open `src/zira_dashboard/templates/wc_dashboard.html`. Replace the ENTIRE file contents with:

```jinja
{# Operator dashboard. Two routes share this template:
   - /wc/{slug}        screen editor view (gridstack enabled, WC picker visible)
   - /tv/wc/{slug}     TV view (chrome stripped, picker hidden)

   Widgets mirror /recycling's layout exactly, scoped to a single WC.
   The cumulative chart and the 15-min progress chart share the same
   buckets data (rendered differently by each).
#}
{% from "_tv_header.html" import tv_header %}
{% from "_goat_badges.html" import goat_badges, goat_badges_css, hover_tip_clamp_script %}
{% from "_cumulative_progress_chart.html" import cumulative_progress_chart %}
<!doctype html>
<html lang="en"{% if tv_mode %} data-tv-theme="{{ tv_theme or 'dark' }}"{% endif %}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="/static/gpi-logo.png">
<title>{% if tv_mode %}TV · {% endif %}{{ wc_name }} — GPI Plant Manager</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gridstack@10.3.1/dist/gridstack.min.css">
<link rel="stylesheet" href="/static/wc_dashboard.css?v={{ static_v('wc_dashboard.css') }}">
<link rel="stylesheet" href="/static/recycling.css?v={{ static_v('recycling.css') }}">
{% if tv_mode %}
<link rel="stylesheet" href="/static/tv-mode.css?v={{ static_v('tv-mode.css') }}">
<meta http-equiv="refresh" content="60">
{% endif %}
<link rel="stylesheet" href="/static/dashboards-subnav.css?v={{ static_v('dashboards-subnav.css') }}">
<style>{{ goat_badges_css() }}</style>
<style>
  .wc-picker-bar { display: flex; justify-content: flex-end; padding: 0.5rem 1rem 0; }
  .wc-picker-bar select {
    background: var(--panel-2, #f1f4f7); color: var(--fg, #1f2937);
    border: 1px solid var(--border, #d8dee5); border-radius: 6px;
    padding: 0.3rem 0.5rem; font: inherit; font-size: 0.9rem;
  }
</style>
</head>
<body>
{% if tv_mode %}
  {{ tv_header(
      wc_name,
      crumb="OPERATOR · " + (wc_group or "")|upper,
      right=operators_display or "(unassigned)",
  ) }}
{% else %}
  <header>
    <div style="display:flex;gap:1.25rem;align-items:center">
      <a href="/recycling" style="display:flex;align-items:center;gap:0.55rem;text-decoration:none;color:inherit">
        <img src="/static/gpi-logo.png" alt="GPI" style="height:28px;width:auto;display:block">
        <h1 style="margin:0">Plant Manager</h1>
      </a>
      <nav>
        <a href="/recycling">Dashboards</a>
        <a href="/trophies">Trophy Case</a>
        <a href="/staffing">Staffing</a>
        <a href="/settings">Settings</a>
      </nav>
    </div>
  </header>
  {% include "_dashboards_subnav.html" %}
  <div class="wc-picker-bar">
    <label style="display:flex;gap:0.4rem;align-items:center;font-size:0.85rem;color:var(--muted)">
      Work center:
      <select id="wc-picker">
        {% for w in wc_options %}
          <option value="{{ w.slug }}" {% if w.slug == slug %}selected{% endif %}>{{ w.name }}</option>
        {% endfor %}
      </select>
    </label>
  </div>
{% endif %}

<main>
<div class="grid-stack">

  {# KPI tiles row #}
  <div class="grid-stack-item" gs-id="wc-kpi-row" gs-x="0" gs-y="0" gs-w="12" gs-h="2">
    <div class="grid-stack-item-content">
      <div class="kpi-row">
        <div class="kpi"><div class="label">Units today</div><div class="val">{{ '{:,}'.format(kpi.units_today) }}</div></div>
        <div class="kpi"><div class="label">Up Time</div><div class="val">{{ kpi.up_time_pct }} %</div></div>
        <div class="kpi"><div class="label">Downtime</div><div class="val">{{ kpi.downtime_minutes }}m</div></div>
        <div class="kpi"><div class="label">Pallets / hr</div><div class="val">{{ kpi.pallets_per_hour }}</div></div>
      </div>
    </div>
  </div>

  {# Pallets banner — big number vs goal #}
  <div class="grid-stack-item" gs-id="wc-pallets-banner" gs-x="0" gs-y="2" gs-w="12" gs-h="2">
    <div class="grid-stack-item-content">
      <h3>Today · Pallets</h3>
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

  {# 15-min progress chart — same markup as /recycling's progress_chart macro #}
  <div class="grid-stack-item" gs-id="wc-15min-progress" gs-x="0" gs-y="4" gs-w="12" gs-h="5">
    <div class="grid-stack-item-content">
      <h3>{{ wc_name }} — 15-minute progress</h3>
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

  {# Cumulative daily progress — shared macro #}
  <div class="grid-stack-item" gs-id="wc-cumulative" gs-x="0" gs-y="9" gs-w="12" gs-h="5">
    <div class="grid-stack-item-content">
      <h3>{{ wc_name }} — Daily Progress</h3>
      {% if progress_buckets %}
        {{ cumulative_progress_chart(progress_buckets) }}
      {% else %}
        <div class="empty-state" style="color:var(--muted);font-size:0.85rem">No shift data for this day.</div>
      {% endif %}
    </div>
  </div>

  {# Downtime — single-row stacked working/down #}
  <div class="grid-stack-item" gs-id="wc-downtime" gs-x="0" gs-y="14" gs-w="12" gs-h="3">
    <div class="grid-stack-item-content">
      <h3>{{ wc_name }} — Downtime · green = working, red = down</h3>
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

  {# Vs. GOAT Pace — group of this WC #}
  <div class="grid-stack-item" gs-id="wc-goat-race" gs-x="0" gs-y="17" gs-w="12" gs-h="4">
    <div class="grid-stack-item-content">
      <h3>Vs. GOAT Pace{% if wc_group %} — {{ wc_group }}{% endif %}</h3>
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

  {# Monthly Ribbons — group of this WC #}
  <div class="grid-stack-item" gs-id="wc-monthly-ribbons" gs-x="0" gs-y="21" gs-w="12" gs-h="4">
    <div class="grid-stack-item-content">
      <h3>{{ month_name(month) }} {{ year }}{% if ribbons and ribbons.group %} · {{ ribbons.group }}{% endif %} — Ribbons</h3>
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

<script src="https://cdn.jsdelivr.net/npm/gridstack@10.3.1/dist/gridstack-all.js"></script>
<script>
  const grid = GridStack.init({
    column: 12,
    cellHeight: 80,
    margin: 8,
    float: false,
  });
  function persistLayout() {
    const items = grid.save(false).map(it => ({
      id: it.id, x: it.x, y: it.y, w: it.w, h: it.h,
    })).filter(it => it.id);
    fetch('/api/layout/{{ layout_key }}', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(items),
    });
  }
  grid.on('change', persistLayout);

  {% if not tv_mode %}
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
</body>
</html>
```

- [ ] **Step 5: Verify**

```
.venv/Scripts/python.exe -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'), autoescape=True); env.parse(open('src/zira_dashboard/templates/wc_dashboard.html', encoding='utf-8').read()); print('parse OK')"
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; routes = sorted({r.path for r in app.routes if hasattr(r, 'path')}); [print(p) for p in routes if '/operator' in p or '/wc/' in p]"
.venv/Scripts/python.exe -m pytest 2>&1 | tail -3
```

Expected: parse OK; routes listed include `/operator` and `/wc/{slug}` and `/tv/wc/{slug}`; full test suite green (no new failures).

- [ ] **Step 6: Commit**

```
git add src/zira_dashboard/routes/wc_dashboard.py src/zira_dashboard/templates/wc_dashboard.html
git commit -m "$(cat <<'EOF'
feat(operator): /operator redirect + WC picker + Recycling-VS-style layout

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: CHANGELOG + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run full test suite**

```
.venv/Scripts/python.exe -m pytest 2>&1 | tail -5
```

Expected: pass count dropped (~50 tests removed); no new failures.

- [ ] **Step 2: Get current time**

```
powershell.exe -Command "Get-Date -Format 'h:mm tt'"
```

- [ ] **Step 3: Add CHANGELOG entry under today's date**

Open `CHANGELOG.md`. If a `## 2026-05-14` section doesn't exist yet, add it at the top of the file (after the header). Insert this entry under that date as the first `### <HH:MM TT>` block:

```markdown
### <HH:MM TT>

- **Workshop tear-down + new Operator dashboard** — the widget workshop / custom dashboards / pinned dashboards / layout templates experiments are removed entirely. Roughly 30 files deleted, 5 DB tables dropped (`widget_definitions`, `custom_dashboards`, `dashboard_widgets`, `tv_dashboard_templates`, `pinned_dashboards`); any TV display rows with `kind = 'custom'` are also deleted. Sub-nav is now a fixed 4-tab strip: **Recycling VS · New VS · Operator · Work Centers**. The top-nav "My Dashboards" link is gone (page removed). The new **Operator dashboard** lives at `/wc/{slug}` (TV: `/tv/wc/{slug}`) and mirrors `/recycling`'s visual style scoped to a single work center: KPI tiles row, Pallets banner, 15-min progress chart, Cumulative Daily Progress, Downtime stacked bar, Vs. GOAT Pace, Monthly Ribbons. A WC dropdown at the top lets you switch which work center the page shows. `/operator` redirects to the first WC. The Settings → TVs panel drops the Custom optgroup and the Layout Templates section.
```

- [ ] **Step 4: Commit + push**

```
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(changelog): operator dashboard + workshop tear-down

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

Railway picks up the push and redeploys. After deploy:

1. `/widgets`, `/dashboards`, `/dashboards/*` all return 404.
2. Top nav: Dashboards · Trophy Case · Staffing · Settings (no "My Dashboards").
3. Dashboards sub-nav: Recycling VS · New VS · Operator · Work Centers.
4. Clicking "Operator" lands on the first WC's `/wc/{slug}`. The picker at the top switches WCs.
5. Each per-WC dashboard renders the seven widgets in /recycling's style.
6. `/tv/wc/{slug}` keeps working in TV mode (no picker, no sub-nav).
7. Settings → TVs picker: Built-in only (Recycling VS, New VS, Work Centers, every WC). Custom optgroup gone.

---

## Done

Code base shrinks by ~30 files and 5 DB tables. The widget framework abstraction is gone. The Operator dashboard is one route + one template + a handful of helpers — simple to understand, easy to evolve.

Future enhancements (not in this plan): operator-relevant KPIs beyond the four shown, alternate widget orderings via the gridstack drag/save (already supported), more granular shift-period selection (currently always today).
