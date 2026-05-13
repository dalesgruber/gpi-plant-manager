# TV Displays Settings Panel (Sub-Project 4)

**Date:** 2026-05-13
**Status:** Draft — pending user review
**Parent spec:** `docs/superpowers/specs/2026-05-13-tv-dashboards-design.md`
**Goal:** A "TVs" section in Settings that lists every TV Dale has mounted, lets him toggle each one to light or dark, and gives each TV its own short bookmarkable URL. Replaces the per-URL `?theme=light` workaround from sub-projects 1–2 with persistent theme storage. Also surfaces the layout templates from sub-project 3 with a delete button.

## Problem

After sub-projects 1–3 shipped, the only way to control a TV's theme is to bookmark `/tv/wc/repair-1?theme=light`. That works but it's invisible — there's no central list of which TVs exist, what they show, and what theme each one runs in. Dale also has no way to clean up old layout templates short of hitting the DELETE endpoint directly.

This sub-project adds a "TVs" settings section with two stacked tables:

1. **Displays** — every TV Dale wants to track. Each row is a friendly name + which dashboard it shows + a light/dark toggle + a bookmarkable URL.
2. **Layout templates** — the existing `tv_dashboard_templates` rows from sub-project 3, with a delete button.

The seed list on first run is exactly the 11 TVs Dale plans to mount: Recycling VS, New VS, Junior 2, Repair 1/2/3, Dismantler 1/2/3/4.

## Strategy

The existing `/tv/recycling`, `/tv/new-vs`, `/tv/wc/{wc_slug}` routes shipped yesterday and keep working as default-dark fallbacks — no bookmarks shipped in sub-projects 1–2 break. A new `/tv/d/{slug}` route is the preferred URL for any TV created from the panel; the route looks up the display row, resolves the theme, and dispatches to the same render helper the legacy route already calls.

Display rows are user-managed, not auto-derived. The user picked the user-added model in brainstorming — they want flexibility to have two displays pointing at the same WC with different themes (e.g. "Repair 1 — Wall TV (dark)" and "Repair 1 — Desk Monitor (light)"), so each row has its own slug and URL.

## Components

### Route map

| Route | Purpose |
|---|---|
| `GET /tv/d/{slug}` | New: resolve display row → dispatch to underlying dashboard with row.theme |
| `GET /settings?section=tvs` | New settings sub-section |
| `POST /api/tv-displays` | Add or update a display (body: `{id?, name, kind, wc_name?, theme}`) |
| `POST /api/tv-displays/{id}/theme` | Toggle theme (body: `{theme}`) |
| `DELETE /api/tv-displays/{id}` | Remove a display |
| `GET /tv/recycling` (existing) | Unchanged — fallback, defaults dark, `?theme=` override still works |
| `GET /tv/new-vs` (existing) | Unchanged — fallback |
| `GET /tv/wc/{wc_slug}` (existing) | Unchanged — fallback |

### Data model

```sql
CREATE TABLE IF NOT EXISTS tv_displays (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL,                 -- friendly name e.g. "Repair 1"
  slug        TEXT NOT NULL UNIQUE,          -- URL slug e.g. "repair-1"
  kind        TEXT NOT NULL CHECK (kind IN ('vs_recycling', 'vs_new', 'wc')),
  wc_name     TEXT,                          -- non-null when kind = 'wc'
  theme       TEXT NOT NULL DEFAULT 'dark' CHECK (theme IN ('light', 'dark')),
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The CHECK constraint on `kind` matches the master spec. `wc_name` is semantically a soft FK to `staffing.LOCATIONS[].name`; archived WCs are tolerated at read time (see edge cases).

### Data layer — `src/zira_dashboard/tv_displays_store.py`

| Function | Purpose |
|---|---|
| `list_displays() -> list[dict]` | All rows ordered by `(sort_order, name)`, returns `{id, name, slug, kind, wc_name, theme}` |
| `save(name, kind, wc_name, theme, *, id=None) -> dict` | INSERT new row or UPDATE existing (when `id` given). Derives slug from name; appends `-2`, `-3` etc. on collision (skipping the current row's own slug when updating). Returns the saved row. |
| `set_theme(id, theme) -> None` | Lightweight theme-only update without slug re-derivation |
| `delete(id) -> None` | DELETE FROM tv_displays WHERE id = %s |
| `by_slug(slug) -> dict \| None` | Used by `/tv/d/{slug}` route |
| `seed_defaults_if_empty() -> None` | If `COUNT(*) = 0`, INSERT the 11-row seed list (idempotent at empty-state). Called once at boot. |

**Slug derivation:** reuse `wc_dashboard_data.slug_for_wc` for consistency with the existing WC slug logic — lowercase, replace non-alphanumeric runs with `-`, strip leading/trailing `-`. For a name like "Repair 1 Wall TV" → "repair-1-wall-tv". On UNIQUE collision the store appends `-2`, then `-3`, etc.

**Seed list (10 rows):**

| Name | Kind | wc_name |
|---|---|---|
| Recycling VS | vs_recycling | (null) |
| New VS | vs_new | (null) |
| Junior 2 | wc | Junior 2 |
| Repair 1 | wc | Repair 1 |
| Repair 2 | wc | Repair 2 |
| Repair 3 | wc | Repair 3 |
| Dismantler 1 | wc | Dismantler 1 |
| Dismantler 2 | wc | Dismantler 2 |
| Dismantler 3 | wc | Dismantler 3 |
| Dismantler 4 | wc | Dismantler 4 |

If a seed row's `wc_name` is not present in `staffing.LOCATIONS` at boot, log a warning and skip that row — don't fail boot.

### Route layer — `src/zira_dashboard/routes/tv_displays.py`

`GET /tv/d/{slug}`:
1. `row = tv_displays_store.by_slug(slug)`. If `None`, return a 404 HTML response with a link back to `/settings?section=tvs` ("This display isn't configured — visit settings").
2. Resolve theme: `?theme=` query param if `in ('light', 'dark')`, else `row['theme']`, else `'dark'`.
3. Dispatch by kind:
   - `vs_recycling` → call the existing `_render_recycling(request, tv_mode=True, tv_theme=theme)` helper from `routes/value_streams.py` (or wherever the helper lives — sub-project 1 extracted it).
   - `vs_new` → `_render_new_vs(...)` analog.
   - `wc` → look up `wc_name`, derive its slug, call `_render_wc_dashboard(request, wc_slug=slug_for_wc(row['wc_name']), tv_mode=True, tv_theme=theme)`. If the WC is no longer in `staffing.LOCATIONS`, return a 404 with the same settings link and a "the work center this display was pointing at was removed" message.

`POST /api/tv-displays`:
- Body: `{id?: int, name: str, kind: str, wc_name?: str, theme: str}`. If `id` is omitted, INSERT; otherwise UPDATE.
- Validate: name non-empty, kind ∈ allowed set, wc_name required when kind='wc' and present in `staffing.LOCATIONS`, theme ∈ {light, dark}.
- Returns `{ok, id, slug, url}` where `url = "/tv/d/" + slug`.

`POST /api/tv-displays/{id}/theme`:
- Body: `{theme}`. Validate ∈ {light, dark}. Update only.
- Returns `{ok: true}`.

`DELETE /api/tv-displays/{id}`:
- Removes the row. Returns `{ok: true}`.

### Settings sub-section UI

New left-rail entry "TVs" between "Work Centers & Goals" and "Roster Filter". `routes/settings.py` extended to handle `section=tvs`; reads `tv_displays_store.list_displays()` and `tv_templates_store.list_templates()` for context.

Two stacked panels rendered from a new `templates/_settings_tvs.html` partial, included from `settings.html` (settings.html is already ~800 lines — a new section gets its own partial):

**Panel 1 — Displays table:**

| Column | Content |
|---|---|
| Name | Editable inline (click to edit, blur to save) |
| Target | Dropdown for `kind`; if `wc`, second dropdown for `wc_name` populated from `staffing.LOCATIONS` |
| Theme | Two-button toggle "Dark ● ○ Light" — click switches; fires `POST .../theme` |
| URL | `https://gpiplantmanager.com/tv/d/{slug}` shown read-only with a "Copy" button next to it (writes to clipboard via `navigator.clipboard.writeText`) |
| Actions | `×` delete (with confirm) |

Below the table, an "Add display" form row: name input + kind select + cascading wc_name select (shown only when kind='wc') + theme select + Add button. Submitting POSTs to `/api/tv-displays`, then the JS appends the new row to the table without reload.

**Panel 2 — Layout Templates table** (compact, since this is a deletion UI for templates created via sub-project 3):

| Column | Content |
|---|---|
| Name | Read-only |
| Theme | Read-only ("dark" / "light") |
| Updated | Relative ("2 hr ago") with absolute timestamp as title tooltip |
| Actions | `×` delete (with confirm; hits the existing `DELETE /api/tv-templates/{id}` from sub-project 3) |

If the table is empty, show "No templates saved yet — visit any `/wc/{slug}` editor to save a layout as a template."

### Theme dispatch refactor

Sub-projects 1 and 2 already have render helpers that accept `tv_mode` + `tv_theme`:
- `routes/value_streams.py` — `_render_recycling(request, ...)` and `_render_new_vs(request, ...)` (used by the `/tv/recycling` and `/tv/new-vs` route handlers)
- `routes/wc_dashboard.py` — `_render_wc_dashboard(request, wc_slug, tv_mode, tv_theme)`

The new `routes/tv_displays.py` imports these helpers and calls them directly. No widget code moves. If a helper's current signature doesn't accept `tv_theme` explicitly (some thread it via a kwarg dict), the plan task adds the explicit kwarg without changing call sites elsewhere.

### Seed timing

`db.bootstrap_schema` already creates tables idempotently on every boot. The seed runs at a layer above schema: inside `app.py`'s `lifespan` context, **after** `db.bootstrap_schema()`, call `tv_displays_store.seed_defaults_if_empty()`. The check (`SELECT COUNT(*) FROM tv_displays`) is a single query and runs once per app start. If Dale deletes a seeded row, the next redeploy sees a non-empty table and doesn't re-seed.

## Data flow

**First boot after deploy:**
1. `bootstrap_schema` creates `tv_displays` (empty)
2. `seed_defaults_if_empty()` inserts 10 rows
3. `/settings?section=tvs` renders them; Dale toggles themes as needed

**Adding a new display:**
1. Fill form, click Add
2. `POST /api/tv-displays {name, kind, wc_name?, theme}` → store derives slug, INSERTs, returns `{id, slug, url}`
3. JS appends a new row to the table; URL copy button is wired

**Toggling theme:**
1. Click "Light" on a row currently set to dark
2. `POST /api/tv-displays/{id}/theme {theme: "light"}` → updates row
3. JS swaps the toggle's active button; the copied URL hasn't changed but next page-load of `/tv/d/{slug}` renders light

**TV visiting its URL:**
1. `GET /tv/d/repair-1` → store lookup → row found, theme='light'
2. Route dispatches to `_render_wc_dashboard(wc_slug='repair-1', tv_mode=True, tv_theme='light')`
3. TV sees the same per-WC dashboard, in light mode

## Edge cases

- **Slug collisions on add:** store appends `-2`, `-3`, etc. until unique.
- **Slug collisions on rename:** same logic, but skips the row's own current slug so re-saving without changing the name doesn't suffix.
- **Renamed display breaks the old URL:** documented in the panel — small "Renaming changes the URL, existing bookmarks will break" hint under the rename input.
- **Archived WC referenced by a display:** the `/tv/d/{slug}` route returns a 404 with a clear message. The panel marks the row "⚠ work center removed" (cross-check `wc_name` against `staffing.LOCATIONS` at render time).
- **kind=vs_* with wc_name set:** ignored at read; the route only dereferences wc_name when kind='wc'.
- **Theme query string override:** kept for preview workflow (e.g. `?theme=light` on a dark-stored display lets Dale check what light would look like without changing the stored value).
- **Concurrent rename:** last writer wins. No lock. Acceptable for a one-admin app.

## Testing

- **Store unit tests (Postgres-gated):**
  - `save` inserts and returns slug for fresh row
  - `save` upserts on `id` and keeps slug when name unchanged
  - `save` regenerates slug on name change with collision suffix
  - `set_theme` updates only the theme column
  - `delete` removes the row
  - `by_slug` returns the row for an existing slug, None otherwise
  - `seed_defaults_if_empty` inserts 10 rows on empty table; is a no-op on a non-empty table
  - Slug collision suffix: save two rows with "Repair 1" name → second gets `repair-1-2`
- **Route integration tests:**
  - POST add → GET `/tv/d/{slug}` → 200 with the dashboard chrome stripped and `data-tv-theme` matching the row's theme
  - POST theme toggle → next GET reflects new theme
  - DELETE → next GET `/tv/d/{slug}` → 404
  - GET `/tv/d/missing` → 404 with the "not configured" message
  - GET `/tv/d/{slug}` with `?theme=light` overrides a dark-stored display
  - GET `/tv/d/{slug}` for a row whose `wc_name` isn't in LOCATIONS → 404 with the "work center removed" message
- **Settings page render:** GET `/settings?section=tvs` → 200, contains both panel headers, contains at least one seeded row's name.

## Out of scope (v1)

- **Editable slug decoupled from name** — slugs are always derived from name. A future enhancement could add a separate "URL slug" input.
- **Move between kinds** — to convert a display from `wc` to `vs_recycling`, delete and re-add.
- **Per-display layout overrides** — already deferred in the master spec; every TV showing the same dashboard sees the same arrangement.
- **Bulk theme toggle / "set all to light"** — one click per row is fine for ~10 rows.
- **Template rename / clone** — only delete is offered for templates in v1.
- **Sort drag-and-drop** — `sort_order` column exists but the v1 UI sorts by name. Drag-to-reorder is a quick follow-on if needed.

## Rollout

Single ship — schema + store + route + settings UI go out in one push. CHANGELOG entry describes the new "TVs" settings section, the persistent theme, and the new `/tv/d/{slug}` URL pattern. Existing bookmarks (`/tv/wc/repair-1` etc.) continue to work unchanged.
