# Dashboards Sub-Nav + Pinning + Unified Index + TVs Flatten

**Date:** 2026-05-13
**Status:** Draft — pending user review
**Goal:** Restructure the Dashboards family so every dashboard (built-in VS, built-in per-WC, user-built custom) lives under a single "Dashboards" top tab. A pinnable sub-nav surfaces the user's favorites next to the always-on `My Dashboards` and `Widget Workshop` links. The `/dashboards` index lists every renderable dashboard with star-toggle pinning. The Settings → TVs panel flattens its kind/wc/custom cascading pickers into one unified dashboard picker.

## Problem

Two related papercuts after today's widget-workshop ship:

1. **Discoverability:** `/widgets` and `/dashboards` only reachable via top-nav and Settings sidebar — neither feels like the right home. They're dashboard-management surfaces, but live above/beside the dashboards themselves.
2. **Fragmentation:** `/dashboards` (My Dashboards) lists only user-built custom dashboards. The hardcoded `/recycling`, `/new-vs`, and per-WC dashboards are invisible there — Dale can't see the whole catalog or favorite the ones he uses most.
3. **TVs picker friction:** the Settings → TVs row picker has three cascading selects (kind / wc / custom) that hide-and-show based on the chosen kind. With every dashboard being a candidate, a single flat picker is simpler.

## Strategy

A "dashboard" becomes a first-class concept covering three kinds — `vs_recycling`, `vs_new`, `wc`, `custom`. A new `pinned_dashboards` table records which ones the user wants as sub-tabs. A new `dashboard_catalog` helper enumerates all renderable dashboards from one entry point, used by three consumers: the unified `/dashboards` index, the dashboards sub-nav partial, and the TVs settings flat picker.

No widget code changes. Hardcoded `/recycling`, `/wc/{slug}`, etc. keep their existing render paths. The pin state is purely metadata that drives navigation.

## Components

### Data model — `pinned_dashboards`

```sql
CREATE TABLE IF NOT EXISTS pinned_dashboards (
  id          SERIAL PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('vs_recycling', 'vs_new', 'wc', 'custom')),
  ref         TEXT NOT NULL,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (kind, ref)
);
```

`ref` semantics:
- `kind = 'vs_recycling'` → `ref = ''` (only one Recycling VS)
- `kind = 'vs_new'` → `ref = ''`
- `kind = 'wc'` → `ref` is the WC name (e.g. `'Repair 1'`)
- `kind = 'custom'` → `ref` is the dashboard slug (e.g. `'floor-hub'`)

**Seed on first boot:** if the table is empty, insert `('vs_recycling', '', 0)` and `('vs_new', '', 1)`. Done. Deleted seeds stay deleted across redeploys (same idempotent pattern as `tv_displays_store.seed_defaults_if_empty()`).

### Store — `pinned_dashboards_store.py`

| Function | Purpose |
|---|---|
| `list_pins() -> list[dict]` | All pins ordered by `sort_order, created_at`. Returns `[{kind, ref, sort_order}, ...]`. |
| `is_pinned(kind, ref) -> bool` | Single membership check. |
| `pin(kind, ref) -> None` | INSERT ... ON CONFLICT DO NOTHING. New pins get `sort_order = (MAX + 1)`. |
| `unpin(kind, ref) -> None` | DELETE WHERE kind = %s AND ref = %s. |
| `seed_defaults_if_empty() -> None` | Insert `(vs_recycling, '')` + `(vs_new, '')` on empty table. |

### Catalog — `dashboard_catalog.py`

A single read-only helper that enumerates every renderable dashboard. Used by three call sites (index page, sub-nav partial, TVs picker) so each renders a consistent list.

```python
def all_dashboards() -> list[dict]:
    """Returns every renderable dashboard as:
      [{kind, ref, name, open_url, tv_url, pinned: bool}, ...]
    Order: vs_recycling, vs_new, then WCs in staffing.LOCATIONS order,
    then custom dashboards in custom_dashboards.list_dashboards() order.
    """
```

| Kind | Name | open_url | tv_url |
|---|---|---|---|
| `vs_recycling` | "Recycling VS" | `/recycling` | `/tv/recycling` |
| `vs_new` | "New VS" | `/new-vs` | `/tv/new-vs` |
| `wc` | `<loc.name>` (e.g. "Repair 1") | `/wc/{slug}` | `/tv/wc/{slug}` |
| `custom` | `<dashboard.name>` | `/dashboards/{slug}` | `/tv/dashboards/{slug}` |

The `pinned` field is computed by joining against `pinned_dashboards_store.list_pins()` once at the top of the function — single set lookup per entry.

A second helper for sub-nav rendering:

```python
def pinned_dashboards_for_subnav() -> list[dict]:
    """Just the pinned subset of all_dashboards(), in sort_order.
    [{kind, ref, name, open_url, key}, ...] where key is the active-tab
    identifier used by templates: '{kind}:{ref}'.
    """
```

### Pin/Unpin API — `routes/custom_dashboards.py`

Single endpoint added to the existing routes module (it's the dashboards-related home):

```
POST /api/pinned-dashboards
  body: {kind, ref, pinned: bool}
  effect: pin if pinned=true else unpin
  returns: {ok: true, pinned: bool}
```

Validation:
- kind in allowed set → 400 otherwise
- ref is a string (can be empty for vs_*) → 400 otherwise
- For `kind = 'wc'`, ref must be in `staffing.LOCATIONS` → 400 otherwise
- For `kind = 'custom'`, ref must be a valid `custom_dashboards.slug` → 400 otherwise

### Sub-nav partial — `templates/_dashboards_subnav.html`

```jinja
{# Sub-nav for the Dashboards top tab. Context:
   - pinned_dashboards: list from dashboard_catalog.pinned_dashboards_for_subnav()
   - active_dashboard_key: '{kind}:{ref}' for the current page, or None
   Pages set active_dashboard_key before including this partial.
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

CSS lives in a new `static/dashboards-subnav.css` linked by every page that includes the partial:
- `.dash-subnav`: `display: flex; gap: 0.5rem; padding: 0.4rem 1rem; border-bottom: 1px solid var(--border); background: var(--panel);`
- `.pinned-tabs`: `flex: 1 1 auto; display: flex; gap: 0.4rem; overflow-x: auto;`
- `.meta-tabs`: `flex: 0 0 auto; display: flex; gap: 0.4rem;`
- `.subnav-item`: `color: var(--muted); text-decoration: none; padding: 0.3rem 0.7rem; border-radius: 6px; font-size: 0.88rem; white-space: nowrap;`
- `.subnav-item.active`: `color: var(--accent); background: var(--accent-dim); font-weight: 600;`
- `.subnav-item:hover:not(.active)`: `color: var(--fg);`

### Page wiring

Every dashboard-family page includes the partial just below the top nav. Each handler sets `active_dashboard_key` so the partial can highlight the right tab.

| Page | Handler | `active_dashboard_key` |
|---|---|---|
| `/recycling` | `_render_recycling` (screen) | `'vs_recycling:'` |
| `/new-vs` | `_render_new_vs` (screen) | `'vs_new:'` |
| `/wc/{slug}` | `_render_wc_dashboard` (screen) | `'wc:{wc_name}'` |
| `/dashboards/{slug}` | `_render_dashboard` (editor) | `'custom:{slug}'` |
| `/dashboards` | `dashboards_index` | `'meta:dashboards'` |
| `/widgets` | `widgets_page` | `'meta:widgets'` |

TV-mode pages (`/tv/*`) DO NOT include the sub-nav — the TV header replaces it (chrome-strip is the whole point of TV mode).

Each handler gets one extra line at the top of its context-build block:

```python
from .. import dashboard_catalog
context["pinned_dashboards"] = dashboard_catalog.pinned_dashboards_for_subnav()
context["active_dashboard_key"] = "wc:" + wc_name  # or whatever fits
```

### Redesigned `/dashboards` index

Two sections, replacing the current single-table layout.

**Built-in dashboards section:**

| Star | Name | Actions |
|---|---|---|
| ★/☆ | Recycling VS | Open · Open as TV |
| ★/☆ | New VS | Open · Open as TV |
| ★/☆ | Junior 2 | Open · Open as TV |
| ★/☆ | Repair 1 | Open · Open as TV |
| ... | (one row per WC) | ... |

**My custom dashboards section:**

| Star | Name | Scope | Widgets | Actions |
|---|---|---|---|---|
| ★/☆ | Floor Hub | wc: Repair 1 | 3 | Edit · Open as TV · × |
| ★/☆ | Hand-build Wall | group: Repairs | 5 | Edit · Open as TV · × |

Below the custom section: **+ Create new dashboard** (opens the existing create form).

**Star toggle JS:** click any ★/☆ → POST `/api/pinned-dashboards` with `{kind, ref, pinned: !currently_pinned}` → on `ok`, swap the icon, no reload.

### Top nav + Settings sidebar cleanup

**Top nav** — every template's header drops "My Dashboards":
- `index.html`, `recycling.html`, `new_vs.html`, `_staffing_base.html`, `settings.html` lose the `<a href="/dashboards">My Dashboards</a>` link added earlier today.
- Top tabs return to `Dashboards | Trophy Case | Staffing | Settings`.
- The "Dashboards" link still points to `/recycling` (the existing default).

**Settings sidebar** — `settings.html` drops the two top entries:
- `<a href="/widgets">Widget Workshop</a>`
- `<a href="/dashboards">My Dashboards</a>`

These were added earlier today; now redundant with the dashboards sub-nav.

**Existing inline nav** in `widgets.html` and `dashboards.html` (each currently has its own `<header><nav>...</nav></header>`) is replaced by the shared top nav block from elsewhere PLUS the new sub-nav partial. So those two pages get the same chrome as every other dashboard-family page.

### TVs settings flat picker

In `_settings_tvs.html`, the per-row block currently has 3 cascading selects (`.tv-kind-select`, `.tv-wc-select`, `.tv-custom-select`) with show/hide JS. Replace with one:

```jinja
<select class="tv-dashboard-select">
  <optgroup label="Built-in">
    {% for d in all_dashboards %}
      {% if d.kind in ('vs_recycling', 'vs_new', 'wc') %}
        <option value="{{ d.kind }}|{{ d.ref }}"
                {% if (row.kind == d.kind) and ((row.kind in ('vs_recycling','vs_new')) or (row.wc_name == d.ref)) %}selected{% endif %}>
          {{ d.name }}
        </option>
      {% endif %}
    {% endfor %}
  </optgroup>
  <optgroup label="Custom">
    {% for d in all_dashboards %}
      {% if d.kind == 'custom' %}
        <option value="custom|{{ d.id }}"
                {% if row.kind == 'custom' and row.custom_dashboard_id == d.id %}selected{% endif %}>
          {{ d.name }}
        </option>
      {% endif %}
    {% endfor %}
  </optgroup>
</select>
```

(For custom kinds, the option value carries the dashboard's `id`, not its slug, since `tv_displays.custom_dashboard_id` is an INTEGER. The catalog helper includes `id` for custom-kind entries.)

**JS:**

```javascript
function parsePickerValue(v) {
  const [kind, ref] = v.split('|', 2);
  if (kind === 'wc')     return {kind, wc_name: ref, custom_dashboard_id: null};
  if (kind === 'custom') return {kind, wc_name: null, custom_dashboard_id: parseInt(ref, 10)};
  return {kind, wc_name: null, custom_dashboard_id: null};
}
```

`saveRow` uses `parsePickerValue` on the single picker's value to build the POST body. The three-select cascading visibility logic is deleted entirely (the show/hide handlers, the per-row `.tv-kind-select` / `.tv-wc-select` / `.tv-custom-select` queries).

**Add form** flattens the same way: name input + flat dashboard picker + theme select + Add button.

**Routes side:** no change — the existing `POST /api/tv-displays` validation already accepts all four kinds via `kind + wc_name + custom_dashboard_id` fields.

## Data flow

**Boot:**
1. `bootstrap_schema` creates `pinned_dashboards` if missing
2. `tv_displays_store.seed_defaults_if_empty()` (existing)
3. `widget_definitions_store.seed_defaults_if_empty()` (existing)
4. `pinned_dashboards_store.seed_defaults_if_empty()` (new) — pins Recycling VS + New VS on empty table

**Visiting `/recycling`:**
1. Handler calls `dashboard_catalog.pinned_dashboards_for_subnav()` → returns the pinned list with names + URLs resolved
2. Handler sets `active_dashboard_key = 'vs_recycling:'`
3. Template renders, the sub-nav partial highlights "Recycling VS"

**Toggling a pin:**
1. User clicks ★ on `/dashboards` next to "Repair 1"
2. JS: `POST /api/pinned-dashboards {kind:'wc', ref:'Repair 1', pinned:true}`
3. Server inserts the row (or no-ops if already present)
4. JS swaps ☆ → ★ on the row, no reload
5. Next page load (any dashboards-family page) sees the new pin in the sub-nav

**Saving a TV display with a custom dashboard:**
1. User picks "Floor Hub" from the flat dashboard select (value `custom|7`)
2. `parsePickerValue` returns `{kind:'custom', wc_name:null, custom_dashboard_id:7}`
3. `saveRow` POSTs to `/api/tv-displays` → existing validation passes → row saved
4. URL hyperlink updates to `/tv/d/<slug>`

## Edge cases

- **Pinning a custom dashboard then deleting it:** the pin row stays in `pinned_dashboards` with `ref='<deleted-slug>'`. `dashboard_catalog.pinned_dashboards_for_subnav()` filters out pins whose target no longer exists (logs a warning the first time). A future cleanup task could prune orphaned pins.
- **Pinning every WC (10):** sub-nav scrolls horizontally. Fine.
- **No pins at all:** the pinned-tabs flex container is empty; My Dashboards + Workshop sit on the right alone. Acceptable.
- **Active dashboard not pinned:** the sub-nav just doesn't highlight any pinned item. The page still renders. Active dashboards are not implicitly added to the sub-nav — the user pins explicitly.
- **TV view of a custom dashboard with a deleted target:** already handled by sub-project 4's "dashboard removed" page.
- **Concurrent pin toggles:** last writer wins. UNIQUE (kind, ref) + ON CONFLICT DO NOTHING make pin idempotent; unpin is a simple DELETE.

## Testing

- **Store unit tests (Postgres-gated):**
  - `pin(kind, ref)` inserts; calling twice is idempotent (no duplicate row)
  - `unpin(kind, ref)` removes; calling on missing row is a no-op
  - `list_pins()` returns rows ordered by `sort_order, created_at`
  - `is_pinned(kind, ref)` returns True/False correctly
  - `seed_defaults_if_empty` inserts 2 rows on empty; no-op on non-empty
- **Catalog tests (unit, with monkeypatched stores):**
  - `all_dashboards()` returns 2 + N_wcs + M_custom entries in stable order
  - Each entry has the right fields and `pinned` reflects the pinned store
- **Route tests (integration):**
  - `POST /api/pinned-dashboards` with pinned=true on a valid kind+ref → 200, store has the row
  - same with pinned=false → 200, row removed
  - invalid kind → 400
  - invalid wc ref (not in staffing.LOCATIONS) → 400
  - invalid custom ref (no matching slug) → 400
- **Render smoke tests:**
  - `/dashboards` page renders both sections, every WC appears, custom rows have edit/delete actions
  - `/recycling` page includes the sub-nav with "Recycling VS" highlighted
  - `/wc/Repair 1` page includes the sub-nav with the WC name in pinned list if pinned
  - `/widgets` page no longer has its own inline nav block; includes the shared sub-nav
  - `/tv/recycling` does NOT include the sub-nav
  - Settings TVs panel: `<select class="tv-dashboard-select">` exists; cascading selects gone

## Out of scope

- **Drag-to-reorder pin order in the sub-nav.** `sort_order` column is present but v1 sets it auto-incrementally and exposes no UI for changing it.
- **Pruning orphaned pins (deleted-target).** v1 filters at read time. A scheduled cleanup is a future polish.
- **Pin a TV-mode URL directly.** Pins reference dashboards; TV view of any pinned dashboard is still one click away via the sub-nav row's hover or the index page.
- **Schema migration for tv_displays** to a single ref column. Decided in brainstorm: keep existing columns, flatten UI only.
- **Custom dashboard scope label in pinned sub-nav.** Tab shows just the dashboard name. The scope shows on the index page.

## Rollout

One deploy, two commits:

1. **Schema + store + catalog + routes + sub-nav + page wiring + index redesign + nav cleanup** — the big commit. Touches ~15 files.
2. **TVs flat picker + CHANGELOG + push.**

No backwards incompatibility. Existing bookmarks and TV URLs keep working unchanged.
