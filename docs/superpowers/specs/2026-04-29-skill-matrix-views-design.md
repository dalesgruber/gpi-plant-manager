# People Matrix Views — Design

**Date:** 2026-04-29
**Status:** Approved (brainstorming → implementation planning)

## Context

The People Matrix today has only a column-visibility filter (the
"Columns ▾" dropdown, persisted to `app_settings['skill_filter']`).
Dale wants richer filtering: column visibility + active/inactive +
reserve handling + an explicit person picker, all bundled into named,
saveable Views.

## Goals

1. Filter the matrix on four dimensions: hidden columns, active state,
   reserve state, person subset.
2. Save those four dimensions together as a named **View** in Postgres
   (carries across devices and users).
3. One View can be marked **default** (auto-loaded for any session that
   hasn't picked a different one).
4. Filter edits are **session-scoped by default** — they affect what's
   on screen but don't persist until the user clicks Save. Save
   either overrides the currently-loaded View or creates a new one.

## Non-goals

- Per-user filters / personal views. There's no auth/login model. All
  Views are shared.
- Saving sort order or the search-box value. Those stay ephemeral.
- Importing/exporting Views as JSON. Maybe later.

## Design

### Schema

Add one table (managed by `bootstrap_schema`):

```sql
CREATE TABLE IF NOT EXISTS skill_matrix_views (
  id              SERIAL PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  is_default      BOOLEAN NOT NULL DEFAULT FALSE,
  hidden_skills   TEXT[]  NOT NULL DEFAULT '{}',
  -- NULL = "all people". Non-NULL = subset (case-sensitive name match).
  visible_people  TEXT[],
  active_filter   TEXT NOT NULL DEFAULT 'active'
                  CHECK (active_filter IN ('active','inactive','all')),
  reserve_filter  TEXT NOT NULL DEFAULT 'all'
                  CHECK (reserve_filter IN ('include','exclude','only')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- At most one default; partial unique index.
CREATE UNIQUE INDEX IF NOT EXISTS skill_matrix_views_default_idx
  ON skill_matrix_views (is_default) WHERE is_default = TRUE;
```

The legacy `app_settings['skill_filter']` is kept as a one-time seed
for migration (see "Migration" below) and otherwise unused after this
ships.

### Server-side: `skill_matrix_views_store.py`

Public API:

```python
def list_views() -> list[dict]: ...
def get_view(name: str) -> dict | None: ...
def get_default_view() -> dict | None: ...
def create_view(name: str, payload: dict) -> dict: ...
def update_view(name: str, payload: dict) -> dict: ...
def delete_view(name: str) -> None: ...
def set_default(name: str | None) -> None: ...  # None clears default
```

Each `dict` matches the row shape: `{name, is_default, hidden_skills,
visible_people, active_filter, reserve_filter}`.

Validation rules:
- `name` non-empty, ≤ 80 chars, unique.
- `hidden_skills` only contains names from the `skills` table — invalid
  entries are dropped.
- `visible_people` either NULL or a non-empty list of strings; invalid
  entries dropped; if list ends up empty → set to NULL ("all").
- `active_filter` and `reserve_filter` must be one of the allowed values.

### Server-side: routes

- `GET /staffing/skills` — already returns the matrix. Now also passes
  `views` (list), `default_view` (name|null), and the **default view
  applied to the page** as the initial state for `hidden_skills` etc.
- `POST /staffing/skills/views` — create a new view. JSON body:
  `{name, hidden_skills, visible_people, active_filter, reserve_filter}`.
- `PUT  /staffing/skills/views/{name}` — overwrite an existing view.
- `DELETE /staffing/skills/views/{name}` — delete.
- `POST /staffing/skills/views/{name}/default` — set as default.
- `DELETE /staffing/skills/views/default` — clear default (no view
  auto-loads).

The legacy `POST /staffing/skills/filter` (which only saved hidden
columns) is removed — the new endpoints subsume it.

### Client-side: state model

In-browser session state lives in `localStorage` under a single key
(`skillMatrixSession`):

```json
{
  "loaded_view_name": "All Repairs" | null,  // last view selected
  "current": {
    "hidden_skills": [...],
    "visible_people": [...] | null,
    "active_filter": "active" | "inactive" | "all",
    "reserve_filter": "include" | "exclude" | "only"
  },
  "dirty": true | false  // current differs from loaded_view
}
```

On page load: if there's session state, apply it. Otherwise, apply the
server's default View. The "loaded view" dropdown reflects
`loaded_view_name`. The dirty flag drives the "● unsaved" indicator.

### UX

Replace the existing "Columns ▾" button with a single **"View ▾"**
button. Clicking it opens a richer popover:

```
View:  [Default ▾]                ← dropdown of all views, currently-loaded ticked
                                   "+ Save new view…" at the bottom
                                   ● after the name = unsaved changes

──────── Active ────────
( ) Active only   ( ) Inactive only   ( ) All

──────── Reserve ────────
( ) Include  ( ) Exclude  ( ) Reserves only

──────── People ────────
( ) All people
( ) Selected only   [Edit selection…]   ← opens a sub-popover with a
                                          searchable checkbox list of all
                                          active+inactive people

──────── Skill Columns ────────
[Production Skills]  ☑ all
   ☑ Chop/Notch  ☑ Dismantle  …
[Supervisor Skills]  ☑ all
   ☑ CPUs/VDOs  ☑ Heat Treat  …

──────── Actions ────────
[ Save changes ]   ← updates the currently-loaded View (disabled if no view loaded)
[ Save as new… ]   ← prompts for a name, creates a new View
[ Set as default ] ← currently-loaded View becomes default for new sessions
[ Delete view ]    ← removes the currently-loaded View (confirm first)
```

The applied state updates the matrix DOM live (CSS `display:none` for
hidden columns/rows). No page reload needed.

### Migration

On first deploy after this ships:
1. `bootstrap_schema()` adds the `skill_matrix_views` table.
2. If `app_settings['skill_filter']` exists and there are no views yet,
   auto-create a "Default" view from the legacy hidden_skills list and
   mark it default. Idempotent — only runs once when the views table is
   empty.

Otherwise, the user opens the matrix and sees the same hidden columns
they had before, just under the new "Default" view.

### Edge cases

- **Renaming a skill or person in Odoo** — saved Views referencing the
  old name silently drop the stale entry on render. The View row stays;
  only the resolved-at-render-time list trims missing items.
- **Deleting a person** — same; their name is silently dropped from
  every View's `visible_people`.
- **Two browsers / two users editing the same View simultaneously** —
  last-write-wins. We don't implement optimistic concurrency; in
  practice this is fine for ops use.
- **Empty View** (everything hidden, no people selected) — renders an
  empty matrix with a banner: "This View hides all rows/columns. [Reset
  filters]". Better than a blank page with no signal.

## Acceptance criteria

- New `skill_matrix_views` table exists; bootstrap is idempotent.
- The matrix renders with the default View applied (or the user's
  session state if dirtier).
- A user can: pick an existing View, edit any of the four filters
  (changes are session-only), Save changes (overwrites loaded View),
  Save as new (creates), Set as default, Delete view.
- Edits to the active filter, reserve filter, person subset, and
  skill columns all reflect immediately on the matrix.
- The matrix shows an "● unsaved" indicator when session state diverges
  from the loaded view.
- Other devices/users opening the page see the new default view (or
  whatever they last loaded; their session state takes precedence over
  the global default).
- Deleting a view that's currently loaded resets the session to the
  default view.
- Skills or people that no longer exist (e.g., dropped from Odoo) are
  silently absent from rendered output but stay in the View row.

## Risks

- **localStorage divergence** — If a user has session state pointing to
  a deleted view, we need to detect that on page load and silently
  fall back to default. Easy to handle.
- **Person picker UX scale** — With 30 employees the searchable
  checkbox panel is fine. If headcount grows past ~200, a virtualized
  list would be needed. Out of scope for now.
- **Default-flip race** — If two admins set different views as default
  simultaneously, the partial unique index causes one to fail. The UI
  surfaces "Could not set default — another change conflicted." Retry
  resolves it.

## File touch list

- New: `src/zira_dashboard/skill_matrix_views_store.py`
- New: SQL DDL block in `src/zira_dashboard/db.py`
- Modified: `src/zira_dashboard/routes/skills.py` (GET passes views,
  POST endpoints for CRUD, removed legacy filter endpoint)
- Modified: `src/zira_dashboard/templates/skills.html` (View ▾ popover
  + JS managing session state in localStorage + AJAX to save endpoints)
- Removed: `src/zira_dashboard/skill_filter_store.py` (subsumed by
  views store + migration seeds it once)
- Modified: `tests/test_postgres_stores.py` or new
  `tests/test_views_store.py` for the views CRUD round-trip
