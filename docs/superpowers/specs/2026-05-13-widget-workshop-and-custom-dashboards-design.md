# Widget Workshop & Custom Dashboards

**Date:** 2026-05-13
**Status:** Draft — pending user review
**Goal:** A widget catalog where Dale defines named widget presets (type + visual config + default data scope), and a custom-dashboard builder where he drops those widgets onto his own pages and overrides the data scope per placement. Any dashboard can be flipped into a TV view that swaps the top nav for the WC/group TV header.

## Problem

Today the system has three hardcoded dashboards: `/recycling`, `/new-vs`, and `/wc/{slug}`. Each renders a fixed set of widgets bound to a fixed data scope. Adding a new view — e.g. "Repair 1 with the recycling-VS pallets chart plus the goat race plus today's downtime" — requires writing a new template, a new route, and new data prep. Dale needs to be able to build views like that himself without code.

This spec adds two new top-level surfaces:

1. **Widget Workshop** at `/widgets` — CRUD on widget *presets*. Each preset has a type (one of 8), visual config (color, sort, layout), and a default data scope (group or WC name).
2. **Custom Dashboards** at `/dashboards` index + `/dashboards/{slug}` editor + `/tv/dashboards/{slug}` TV view. Each dashboard has a name, a primary scope (a WC *or* a group, drives the TV header), and a grid of widget placements. Placements reference workshop widgets and can override the data scope.

Existing dashboards (`/recycling`, `/new-vs`, `/wc/{slug}`) stay exactly as they are. The new system is a sibling, not a replacement.

## Strategy

A small **widget type registry** in `widget_types.py` is the linchpin. Each registered type carries a parameter schema (data params + visual params), a data-resolver function, and a Jinja partial. Adding a 9th type later is one entry in the registry, one resolver function, one partial.

Presets live in `widget_definitions` (type + visual + default data + name). Dashboards live in `custom_dashboards`. Placements (the join — which preset goes on which dashboard, where, with what data overrides) live in `dashboard_widgets`.

A **single generic Jinja partial** (`_widget_render.html`) dispatches by widget type to the type's own partial. The edit panel for a placement is also schema-driven — one JS handler reads the type's `data_params_schema` and builds the form. No per-type bespoke JS.

The existing `widget_layouts` table is not reused — `dashboard_widgets` carries its own `(x, y, w, h)` because the placement *is* the layout. Multiple placements of the same preset on the same dashboard are allowed; each is a separate row.

Theme + TV-header rendering reuses the `_tv_header.html` macro from sub-project 2. The TV mode flag and `tv-mode.css` from sub-project 1 carry over as-is.

## Components

### Widget type registry — `src/zira_dashboard/widget_types.py`

A module-level dict keyed by type slug. v1 has 8 entries:

| Type slug | Label | Scope kind | Default visual | Data params |
|---|---|---|---|---|
| `pallets_by_wc` | Pallets by Work Center | group | color, sort, number_position | `{group}` |
| `pallets_banner` | Pallets Banner (single WC) | wc | color | `{wc_name}` |
| `daily_progress` | Daily Progress Chart | wc or group | color, show_target | `{scope_kind, scope_value}` |
| `cumulative` | Cumulative Progress | wc or group | color, show_target | `{scope_kind, scope_value}` |
| `kpi` | KPI Tile | any | label, format, color | `{metric, scope_kind, scope_value}` where `metric ∈ {total_pallets, pallets_per_hour, pallets_per_person, downtime_minutes}` |
| `downtime` | Downtime Report | wc | (none) | `{wc_name}` |
| `goat_race` | Vs. Goat Pace | group | color | `{group}` |
| `ribbons` | Monthly Ribbons | group | (none — month/year auto = current) | `{group}` |

Each registry entry is a dict like:

```python
{
  "type": "pallets_by_wc",
  "label": "Pallets by Work Center",
  "data_params_schema": [
    {"key": "group", "label": "Group", "input": "select", "options_from": "groups", "required": True},
  ],
  "visual_params_schema": [
    {"key": "color", "label": "Bar color", "input": "color", "default": "#22c55e"},
    {"key": "sort", "label": "Sort order", "input": "select",
     "options": [{"value": "preset", "label": "By preset order"},
                 {"value": "desc", "label": "Most pallets first"},
                 {"value": "alpha", "label": "Alphabetical"}],
     "default": "preset"},
    {"key": "number_position", "label": "Number position", "input": "select",
     "options": [{"value": "widget", "label": "Outside bar (right)"},
                 {"value": "bar", "label": "End of bar"},
                 {"value": "inside", "label": "Inside bar"},
                 {"value": "hidden", "label": "Hidden"}],
     "default": "widget"},
  ],
  "resolver": "_resolve_pallets_by_wc",  # name in widget_data.py
  "partial": "_widget_pallets_by_wc.html",
}
```

`options_from` resolves at render time to one of: `groups` (every name from `work_centers_store.all_group_names('group')`), `value_streams` (value-stream group names), `wcs` (every `staffing.LOCATIONS[].name`). Removes the need to hardcode lists in the registry.

### Data model

```sql
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

CREATE TABLE IF NOT EXISTS dashboard_widgets (
  id                  SERIAL PRIMARY KEY,
  dashboard_id        INTEGER NOT NULL REFERENCES custom_dashboards(id) ON DELETE CASCADE,
  widget_def_id       INTEGER NOT NULL REFERENCES widget_definitions(id) ON DELETE RESTRICT,
  x                   INTEGER NOT NULL DEFAULT 0,
  y                   INTEGER NOT NULL DEFAULT 0,
  w                   INTEGER NOT NULL DEFAULT 4,
  h                   INTEGER NOT NULL DEFAULT 4,
  data_overrides_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  sort_order          INTEGER NOT NULL DEFAULT 0,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_dashboard ON dashboard_widgets (dashboard_id);
```

Notes:
- `ON DELETE CASCADE` on `dashboard_widgets.dashboard_id` so deleting a dashboard sweeps its placements.
- `ON DELETE RESTRICT` on `widget_def_id` so a preset can't be deleted while any dashboard still places it — the workshop's delete UI surfaces "in use by N dashboards" and disables the button until those placements are removed.
- `widget_definitions.name` is **not** UNIQUE: Dale may want "Repairs Pallets" with different visual variants. The workshop UI shows full names + type icon so duplicates are visually distinguishable.
- `custom_dashboards.slug` is auto-derived from name with collision suffix, identical pattern to `tv_displays.slug` from sub-project 4.

### Stores

Two new modules:

**`src/zira_dashboard/widget_definitions_store.py`** — CRUD on `widget_definitions`:
- `save(name, type, visual_json, default_data_json, *, id=None) -> dict`
- `list_definitions() -> list[dict]` — ordered by `(type, lower(name))`
- `get(id) -> dict | None`
- `delete(id) -> None` — raises if any `dashboard_widgets.widget_def_id` references it
- `usage_count(id) -> int` — for the workshop's "in use by N" hint

**`src/zira_dashboard/custom_dashboards_store.py`** — CRUD on dashboards + placements:
- `save_dashboard(name, scope_kind, scope_value, theme, *, id=None) -> dict` — slug auto-derived with collision suffix
- `list_dashboards() -> list[dict]` — with `widget_count` precomputed
- `get_dashboard(id_or_slug) -> dict | None`
- `delete_dashboard(id) -> None` — cascades placements
- `list_placements(dashboard_id) -> list[dict]` — joins `widget_definitions` so each placement has both the definition's name/type/visual and the placement's overrides + position
- `add_placement(dashboard_id, widget_def_id, x, y, w, h, data_overrides) -> dict`
- `update_placement(id, *, x=None, y=None, w=None, h=None, data_overrides=None) -> None`
- `delete_placement(id) -> None`

### Data resolvers — `src/zira_dashboard/widget_data.py`

One function per registered type, each takes `data_params: dict` and returns the dict the type's Jinja partial expects. Examples:

- `_resolve_pallets_by_wc(params)` — looks up group from `params["group"]`, calls existing `cached_leaderboard` for that group's WCs today, returns `{"items": [...], "total_u": ..., ...}` — the same shape the `pallets_widget` macro in `recycling.html` consumes today. The new partial mirrors that macro's HTML.
- `_resolve_goat_race(params)` — `awards.goat(params["group"])` + today's elapsed-fraction proration. Returns the same dict shape `wc_dashboard_data.goat_race` produces.
- `_resolve_ribbons(params)` — `awards.monthly_badges(params["group"], year=today.year, month=today.month)`. Returns same shape as `wc_dashboard_data.monthly_ribbons`.
- `_resolve_kpi(params)` — branches on `params["metric"]`, returns `{label, value, format_hint}`.

Each resolver wraps an existing data helper. No widget data prep is rewritten from scratch — the goal is reuse + parameterization.

### Routes — `src/zira_dashboard/routes/widgets.py` and `routes/custom_dashboards.py`

**`routes/widgets.py`** — Widget Workshop:

| Route | Purpose |
|---|---|
| `GET /widgets` | Render workshop page (list + side panel) |
| `GET /api/widgets/types` | Return the type registry for the create form (JSON: list of `{type, label, visual_params_schema, data_params_schema}`) |
| `GET /api/widgets/options/{kind}` | Resolve `options_from` lists at render time (`groups`, `value_streams`, `wcs`) |
| `POST /api/widget-defs` | Create or update (body `{id?, name, type, visual, default_data}`) |
| `GET /api/widget-defs` | List all definitions |
| `DELETE /api/widget-defs/{id}` | Delete; 409 if referenced |

**`routes/custom_dashboards.py`** — Dashboards:

| Route | Purpose |
|---|---|
| `GET /dashboards` | Index page — list of all custom dashboards |
| `GET /dashboards/{slug}` | Editor view (gridstack enabled, palette visible) |
| `GET /tv/dashboards/{slug}` | TV view (chrome stripped, TV header on top) |
| `POST /api/dashboards` | Create or update dashboard meta (body `{id?, name, scope_kind, scope_value, theme}`) |
| `DELETE /api/dashboards/{id}` | Delete dashboard + cascade |
| `POST /api/dashboards/{id}/placements` | Add a placement (body `{widget_def_id, x, y, w, h, data_overrides}`) |
| `PATCH /api/placements/{id}` | Update position or overrides |
| `DELETE /api/placements/{id}` | Remove a placement |
| `POST /api/dashboards/{id}/layout` | Bulk-save the grid layout (called by gridstack autosave; body = list of `{id, x, y, w, h}`) |

### Templates

**Workshop (`templates/widgets.html`):**
- Left column: list of saved definitions. Each row shows name + type icon + visual swatch + "Edit" / "Delete" buttons. Deleting a referenced definition shows the count and disables until placements are removed.
- Right column: create / edit form. Pick a type → the form reveals fields driven by the type's `visual_params_schema` + `data_params_schema`. The form is rendered by JS reading the schema from `/api/widgets/types` so adding a new type later requires no template changes.
- Save → POST `/api/widget-defs` → optimistic UI updates the list.

**Dashboards index (`templates/dashboards.html`):**
- Table: Name | Scope | Widgets | Theme | Actions. Actions: Edit, Open as TV (links to `/tv/dashboards/{slug}`), Delete.
- "Create new" form at bottom: name + scope_kind (radio: WC / group) + scope_value (cascading select).

**Dashboard editor (`templates/custom_dashboard.html`):**
- Full-page gridstack grid. Each placement rendered via the generic dispatcher partial `_widget_render.html` which switches on `widget.type` to load the per-type partial.
- Right-side collapsible drawer "Widget Palette": one row per workshop definition with name + type icon + "Add" button. Click "Add" → POST `/api/dashboards/{id}/placements` with default x/y/w/h for that type → new placement appears top-left of the grid → editor opens its per-placement edit panel for data overrides.
- Per-placement edit button (⋮): opens a popover with the same schema-driven form as the workshop but pre-filled with `{def.default_data | placement.data_overrides}`. Save → PATCH placement.
- Gridstack autosave POSTs to `/api/dashboards/{id}/layout` on drag/resize stop.

**TV view (`templates/custom_dashboard.html` with `tv_mode=True`):**
- Same template, same widgets. Differences gated on `tv_mode`:
  - Top nav and palette drawer hidden
  - Gridstack stays enabled (matches sub-project 4 behavior — TV pages are editable too)
  - `_tv_header.html` macro renders: scope name top-left, operator names top-right. For `scope_kind = wc`, operators = `wc_dashboard_data.assigned_operators_for_wc(scope_value, today)`. For `scope_kind = group`, operators = union of every WC's operators in that group.

### Per-type widget partials

One partial per type under `templates/widgets/` (8 files in v1). Each takes the same context: `{def, placement, data, visual, scope}`. Each renders the actual widget HTML. They reuse the existing CSS classes (`.bar-row`, `.kpi`, `.daily-progress-chart`, `.ribbons-list`, etc.) so visual styling is consistent with the existing dashboards.

The generic dispatcher `_widget_render.html` is a single Jinja `{% if type == ... %}{% include "widgets/_widget_X.html" %}{% endif %}` chain. Simple and explicit.

### TV-displays integration (sub-project 4 extension)

The `tv_displays` table from sub-project 4 gains a new value: `kind = 'custom'`. A `custom_dashboard_slug` column is added (nullable; populated only when kind=custom). The existing `wc_name` and `kind ∈ (vs_recycling, vs_new, wc)` paths are unchanged.

The `/tv/d/{slug}` route handler from sub-project 4 gets a new branch: `kind = 'custom'` → look up `custom_dashboards.slug` via the column, render `_render_custom_dashboard(slug, tv_mode=True, tv_theme=row.theme)`.

The TV Displays settings panel gets a fourth kind option in the kind picker, with a cascading dashboard-slug picker populated from `list_dashboards()`.

## Data flow

**Creating a widget preset:**
1. User visits `/widgets`, clicks "Create new", picks type from a dropdown.
2. Form reveals fields from the type's `visual_params_schema` + `data_params_schema`. User fills in name, visual config, default data scope.
3. Save → POST `/api/widget-defs` → row inserted in `widget_definitions`. UI updates the list.

**Creating a dashboard + adding widgets:**
1. User visits `/dashboards`, clicks "Create new", fills in name + scope (WC or group). Save → row inserted in `custom_dashboards`.
2. User clicks "Edit" → routes to `/dashboards/{slug}` editor.
3. Palette drawer shows every widget definition. User clicks "Add" next to "Repairs Pallets" → POST `/api/dashboards/{id}/placements` with defaults → new row in `dashboard_widgets` → page re-renders with the new placement → per-placement edit panel auto-opens for data overrides.
4. User drags / resizes → gridstack autosave POSTs the new positions in bulk.
5. User clicks per-placement edit (⋮) → popover form lets them override the data scope for this placement → PATCH placement on save.

**Rendering a dashboard (editor or TV):**
1. Route handler loads `custom_dashboards` row + `list_placements(dashboard_id)`.
2. For each placement: merge `widget_def.default_data_json` + `placement.data_overrides_json` → effective data params.
3. Call the type's resolver function with the effective params → widget data dict.
4. Render template with all placements + their resolved data.

**TV header resolution (custom dashboard, TV mode):**
1. `scope_kind = wc`: WC name top-left, operators = `assigned_operators_for_wc(scope_value, today)`.
2. `scope_kind = group`: group name top-left, operators = union over `work_centers_store.members('group', scope_value)` of each WC's operators (deduplicated, sorted).

## Edge cases

- **Resolver fails / returns empty data:** the partial renders an empty-state placeholder ("no data yet" or similar). No error bubbles to the page.
- **Definition deleted while a placement exists:** prevented by FK `ON DELETE RESTRICT`. Workshop UI disables delete with "in use by N dashboards" hint.
- **Scope value points at a removed WC / group:** render an "⚠ scope removed" placeholder for that placement and surface the issue in the dashboard editor.
- **Multiple placements of the same definition on one dashboard:** allowed and works correctly — each placement has its own row, own position, own overrides.
- **Workshop type registry vs DB:** the `type` column is a free TEXT, not an enum, so registering a new type in code doesn't require a migration. Existing rows with an unknown type render an "unknown widget type" placeholder.
- **Custom dashboard slug collision with an existing route:** `/dashboards/widgets` would collide with `/widgets` — not actually a collision because routes are separate paths, but if Dale names a dashboard "widgets" the slug is `widgets` and the URL is `/dashboards/widgets`. Fine.

## Testing

**Phase 1 minimum:**

- **Widget definitions store** (Postgres-gated): save/get/list/delete, delete-while-referenced raises, usage_count
- **Custom dashboards store** (Postgres-gated): save dashboard, slug collision suffix, list with widget_count, delete cascades placements, add/update/delete placement, list_placements joins definition correctly
- **Type registry** (unit, no DB): every registered type has a label, valid schema entries, a resolver name that exists, a partial path that exists; `options_from` values are in the allow-list
- **Resolvers** (unit, mock the underlying data helpers): each resolver returns the documented dict shape for happy-path inputs; missing required params return an empty-state dict instead of raising
- **Workshop routes** (integration): POST creates, GET lists, DELETE 409s when referenced
- **Dashboard routes** (integration): full CRUD + placement CRUD + layout bulk save round-trip
- **TV header**: `scope_kind=wc` → operators from `assigned_operators_for_wc`; `scope_kind=group` → operators unioned across members; render smoke tests for editor + TV view of a dashboard with 3 placements

**Phase 2:** add resolver tests for the 5 additional types.

## Out of scope (v1)

- **Migrating `/recycling` to be a custom dashboard.** It stays hardcoded. If Dale later wants to retire the hardcoded one, that's a separate project that seeds widget definitions matching its widgets and creates a "Recycling VS" custom dashboard.
- **Per-placement visual overrides.** Visual config (color, sort, etc.) lives on the definition and applies to every placement. To get different visuals, create a different definition.
- **Time-window picker (today / week / month) per placement.** All widgets show "today" except `ribbons` which uses the current month. A future enhancement could add a per-placement time-range param.
- **Custom KPI metrics.** The KPI type supports a fixed set (`total_pallets`, `pallets_per_hour`, `pallets_per_person`, `downtime_minutes`). Adding a new metric requires a code change.
- **Dashboard sharing / permissions.** All dashboards visible to all users. The app is single-tenant.
- **Drag from palette directly onto grid.** v1 uses a click-to-add button; a future polish pass could enable HTML5 drag-and-drop from the palette.

## Rollout

Three phases, shipped in order:

1. **Phase 1: Foundation** — schemas + registry + workshop UI + dashboard CRUD + TV view + 3 widget types (Pallets-by-WC, Goat Race, Monthly Ribbons). End-to-end working: Dale can create a preset, create a dashboard, drop the widget, override data, see the result in editor and TV view.
2. **Phase 2: Remaining widget types** — add KPI, Daily Progress, Cumulative, Downtime, Pallets Banner. Each is one resolver function + one partial + one registry entry.
3. **Phase 3: TV Displays integration + polish** — `tv_displays.kind = 'custom'` option, palette UX refinements, "in use by N dashboards" badge in the workshop list.

Each phase ships independently. Phase 1 is the largest because it stands up the framework; phases 2 and 3 are additive.

## Open items

None — every requirement above maps to a concrete component, route, or test. The plan can proceed directly to writing-plans for Phase 1.
