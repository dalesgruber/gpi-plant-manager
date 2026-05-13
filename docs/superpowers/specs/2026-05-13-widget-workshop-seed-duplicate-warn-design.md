# Widget Workshop: Seed + Duplicate + Edit-Warning (+ TVs URL Hyperlink)

**Date:** 2026-05-13
**Status:** Draft — pending user review
**Goal:** Round out the Widget Workshop with three quality-of-life features: (1) seed the workshop with starter entries that mirror every widget on `/recycling` and `/wc/{slug}`, (2) a Duplicate button on each workshop row so a starter can be cloned and customized without losing the original, (3) an edit-warning popup that fires when editing a widget already placed on N dashboards, offering "Duplicate and edit" as a safer path. Plus a small UX fix: convert the `/tv/d/{slug}` Copy button in the Settings TVs table into a real clickable hyperlink.

## Problem

After the widget-workshop ship today, the workshop starts empty. Dale has to build every preset from scratch even though `/recycling` and `/wc/{slug}` already have well-tuned widget configs. He'd rather start with copies of those, then duplicate-and-edit them for variations (e.g., "Pallets by WC — Repairs" + duplicate → change to Dismantlers).

Also, once a workshop widget is placed on a dashboard, editing it changes every placement. That's the right default but it should be visible — a popup before edit prevents accidents and offers the duplicate path inline.

Finally, the TVs settings table shows each display's URL with a Copy button. Dale wants the URL itself clickable.

## Strategy

Three small, additive pieces. None changes existing data structures.

1. **Seed list.** `widget_definitions_store` gets a `seed_defaults_if_empty()` function that mirrors the pattern from `tv_displays_store`. Called from `lifespan` after `bootstrap_schema`. Seeds 10 starter entries on first boot only — deleted seeds stay deleted.

2. **Duplicate.** New store function `duplicate(id) -> dict` reads the source row, derives a unique name (`"X (copy)"` with `(copy 2)`, `(copy 3)` collision suffixes), and saves a fresh row. New API endpoint `POST /api/widget-defs/{id}/duplicate`. Workshop list gets a Duplicate button per row.

3. **Edit-with-warning.** Pure client-side modal. When Edit is clicked on a row with `usage_count > 0`, a modal appears with three buttons: Cancel, Edit anyway, Duplicate and edit. Rows with `usage_count == 0` open the form immediately (no modal). The Duplicate-and-edit button calls the existing `/api/widget-defs/{id}/duplicate` endpoint, then loads the form with the new row.

4. **URL hyperlink.** Replace the URL `<span>` + Copy button in `_settings_tvs.html` with a single `<a>` opening `/tv/d/{slug}` in a new tab.

## Components

### Seed list (10 entries)

The seed function runs once when `widget_definitions` is empty. Each entry mirrors a widget that exists on `/recycling`, `/new-vs`, or `/wc/{slug}`.

**From `/recycling` (group-scoped):**

| Name | Type | Visual | Default data |
|---|---|---|---|
| Pallets by WC — Dismantlers | `pallets_by_wc` | `{color: "#22c55e", sort: "desc"}` | `{group: "Dismantler"}` |
| Pallets by WC — Repairs | `pallets_by_wc` | `{color: "#22c55e", sort: "desc"}` | `{group: "Repair"}` |
| Total Pallets — Dismantlers | `kpi` | `{color: "#22c55e"}` | `{metric: "units_today_group", group: "Dismantler"}` |
| Total Pallets — Repairs | `kpi` | `{color: "#22c55e"}` | `{metric: "units_today_group", group: "Repair"}` |

**From `/wc/{slug}` (defaults to Repair 1 — duplicate-and-edit to swap WCs):**

| Name | Type | Visual | Default data |
|---|---|---|---|
| Pallets Banner — Repair 1 | `pallets_banner` | `{color: "#22c55e"}` | `{wc_name: "Repair 1"}` |
| Daily Progress — Repair 1 | `daily_progress` | `{}` | `{wc_name: "Repair 1"}` |
| Cumulative Progress — Repair 1 | `cumulative` | `{color: "#22c55e", show_target: "true"}` | `{wc_name: "Repair 1"}` |
| Downtime Report — Repair 1 | `downtime` | `{}` | `{wc_name: "Repair 1"}` |
| GOAT Race — Repairs | `goat_race` | `{color: "#22c55e"}` | `{group: "Repair"}` |
| Monthly Ribbons — Repairs | `ribbons` | `{}` | `{group: "Repair"}` |

Seed names that reference a group (Dismantler / Repair) are skipped if the group doesn't exist in `work_centers_store.all_group_names("group")`. Seed names that reference a WC are skipped if the WC isn't in `staffing.LOCATIONS`. Skipped rows log a warning — they don't fail boot.

### Store changes — `widget_definitions_store.py`

Two new functions:

- `seed_defaults_if_empty() -> None`: if `SELECT 1 FROM widget_definitions LIMIT 1` returns nothing, insert the 10 starter rows. Group/WC name validation as above. Idempotent — re-running on a non-empty table is a no-op.
- `duplicate(id) -> dict`: reads the source row via `get(id)`. If not found, raises. Derives a unique name by trying `"{original} (copy)"`, then `(copy 2)`, `(copy 3)`, until no row with that name exists. Returns the inserted dict (via `save(...)` with no id). Name uniqueness is by exact match — `widget_definitions.name` does NOT have a UNIQUE constraint, so the function checks via SELECT rather than catching an error.

### App boot — `app.py`

In `lifespan`, after `bootstrap_schema()` and `tv_displays_store.seed_defaults_if_empty()`, add:

```python
from . import widget_definitions_store
widget_definitions_store.seed_defaults_if_empty()
```

Same pattern as `tv_displays_store.seed_defaults_if_empty()` — boots aren't slowed since the check is a single `SELECT 1 LIMIT 1`.

### Route — `routes/widgets.py`

New endpoint:

```
POST /api/widget-defs/{id}/duplicate    → {ok, definition}
```

Body is ignored. Returns the duplicate's full row dict. 404 if the source id doesn't exist.

### Workshop UI — `templates/widgets.html`

**Per-row changes (the `.def-row` block):**

- Add a **Duplicate** button between Edit and Delete. Single-click → POST `/api/widget-defs/{id}/duplicate` → optimistic UI: append a new row to the list, then auto-trigger the new row's Edit handler so the form loads pre-filled with the duplicate.

**Edit-warning modal:**

A new modal HTML block at the bottom of the page, hidden by default:

```html
<div id="edit-warning-modal" hidden>
  <div class="warn-card">
    <h3 id="warn-title"></h3>
    <p id="warn-body"></p>
    <div class="warn-actions">
      <button type="button" id="warn-cancel">Cancel</button>
      <button type="button" id="warn-edit">Edit anyway</button>
      <button type="button" id="warn-duplicate" class="primary">Duplicate and edit</button>
    </div>
  </div>
</div>
```

JS handler on the Edit button:

1. If `usage_count == 0` → existing fast path (load form, no modal)
2. If `usage_count > 0` → populate the modal title with the widget's name + N, show it
3. Cancel → hide modal, do nothing
4. Edit anyway → hide modal, run the existing edit flow with the original row's id
5. Duplicate and edit → POST `/api/widget-defs/{id}/duplicate`, on success load the form pre-filled with the new row's data + id

To support all this, each `.def-row` carries `data-usage-count="{{ d.usage_count }}"` so the JS can branch without re-fetching.

### TVs settings hyperlink — `templates/_settings_tvs.html`

The per-row URL cell currently:

```jinja
<td class="tv-url-cell">
  <span class="tv-url">/tv/d/{{ d.slug }}</span>
  <button type="button" class="tv-copy-btn">Copy</button>
</td>
```

Becomes:

```jinja
<td class="tv-url-cell">
  <a class="tv-url" href="/tv/d/{{ d.slug }}" target="_blank" rel="noopener">/tv/d/{{ d.slug }}</a>
</td>
```

The corresponding `.tv-copy-btn` JS handler is removed. The slug is always current via the `data-slug` attribute on the row when renames happen — the hyperlink updates on rename too (one extra line to re-sync the `<a href>`).

## Data flow

**Boot:** lifespan runs → bootstrap_schema → `tv_displays_store.seed_defaults_if_empty()` (existing) → `widget_definitions_store.seed_defaults_if_empty()` (new). On first deploy after this ships, the 10 seeded rows appear in `/widgets`. Re-deploys are a no-op since the table is non-empty.

**Duplicate flow:**
1. Click Duplicate on row → POST `/api/widget-defs/{id}/duplicate`
2. Store derives unique name + inserts via `save(...)` → returns new row
3. JS appends to list, opens form with the new row's id pre-filled
4. Save closes the loop by reloading

**Edit-with-warning flow:**
1. Click Edit on row with usage_count = 3
2. Modal shows: "Pallets by WC — Repairs is used on 3 dashboards"
3. User picks "Duplicate and edit" → POST `/api/widget-defs/{id}/duplicate` → form opens with the new id
4. Original row's placements are unaffected
5. User saves → reloads → both rows visible in the workshop

## Edge cases

- **Seed list references a group that doesn't exist:** the seed function logs `WARNING: widget_definitions seed skipping <name> — group <X> not in work_centers_store.all_group_names("group")`. Boot continues.
- **Seed list references a WC not in `staffing.LOCATIONS`:** same warning pattern. The seed list defaults to "Repair 1" — if Dale renamed/removed that WC, those seeds skip.
- **Duplicate of a duplicate:** "Pallets by WC — Repairs (copy)" → duplicate → "Pallets by WC — Repairs (copy) (copy)" → duplicate → "Pallets by WC — Repairs (copy) (copy) (copy)". Acceptable. Dale can rename to clean up.
- **Empty workshop + first action is Duplicate:** can't happen — Duplicate buttons only exist on existing rows.
- **Concurrent edits:** last writer wins. Same as the existing widget-edit flow.
- **Edit-warning bypassed via direct API call:** the warning is client-side only. `POST /api/widget-defs` with an id of a placed widget still updates the row. That's by design — the API is the lower-level surface. The warning is a safety net for the UI flow.
- **Hyperlink + rename:** when a TVs row is renamed, the row's `data-slug` updates and the `<a href>` re-syncs in the same JS handler. Confirmed below.

## Testing

- **Store:** new `seed_defaults_if_empty` is a no-op when the table has rows; seeds 10 when empty; skips group-or-WC-missing entries with a warning log.
- **Store:** `duplicate(id)` returns a row with a unique name; subsequent duplicates of the same source append `(copy 2)`, `(copy 3)`; missing id raises `LookupError`.
- **Route:** `POST /api/widget-defs/{id}/duplicate` returns 200 with the new dict; 404 on unknown id.
- **Workshop UI render:** rows have a Duplicate button; `data-usage-count` is set; the edit-warning modal element exists.
- **Hyperlink:** the rendered `/settings?section=tvs` page has `<a href="/tv/d/{slug}">` for each row.

(JS behavior is verified by hand at deploy — the test scope is the Python + template surface area.)

## Out of scope

- **Editing seeded entries in-place when they're NOT placed yet:** that's fine — the warning only fires if placements exist.
- **"Seed reset" button** that re-seeds deleted starters: deleted seeds stay deleted (matches the tv_displays pattern); a future enhancement could add a "Restore starters" button.
- **Marking seed entries visually distinct from user-created ones:** no badge — they're indistinguishable once created. If Dale renames/edits them they're his.
- **Live-binding the seed entries to `/recycling`:** rejected during brainstorm. The hardcoded pages stay independent.

## Rollout

One deploy. The four pieces ship together:

1. Seed function + boot call → workshop has 10 starters
2. Duplicate API + button → cloning works
3. Edit-warning modal → safety net active
4. URL hyperlink → small TVs-panel UX fix

No schema changes. Existing data unaffected. Safe to deploy mid-shift.
