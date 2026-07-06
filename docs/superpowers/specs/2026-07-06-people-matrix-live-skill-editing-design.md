# People Matrix Live Skill Editing

**Date:** 2026-07-06
**Status:** Approved (design)

## Problem

The People Matrix shows production and supervisor skills synced from Odoo, but
skill cells are read-only. Supervisors need to adjust a person's level from the
matrix and have that change update Odoo live, so Odoo stays the source of truth
and the dashboard immediately reflects the accepted value.

## Decisions

- **Interaction:** clicking a skill cell opens a compact picker with exact choices:
  `0 not trained`, `1 practicing`, `2 competent`, `3 proficient`.
- **Write behavior:** choosing a level writes to Odoo immediately.
- **Source of truth:** Odoo wins. The local database updates only after the Odoo
  write succeeds.
- **Failure behavior:** keep the previous cell value, show an error toast, and do
  not leave a local dirty shadow edit.
- **Refresh behavior:** the existing "Refresh from Odoo" button still pulls the
  matrix from Odoo and reconciles any external changes.

## Approaches Considered

1. **Small picker per clicked cell (selected).** Keeps the dense matrix readable,
   avoids accidental cycle-through edits, works for mouse and keyboard users, and
   makes the supervisor choose the exact target level.
2. **Click-to-cycle.** Fastest for repeated edits, but too easy to overshoot or
   accidentally write the wrong level to Odoo.
3. **Inline select in every cell.** Straightforward technically, but visually noisy
   across a wide matrix and worse for scanning.

## Architecture

### UI

The rendered table remains mostly unchanged: each skill cell still displays the
colored badge. Skill cells become button-like controls with:

- `data-person-odoo-id` from the person's Odoo employee id.
- `data-skill-odoo-id` from the skill row synced from Odoo.
- `data-skill-name` and current `data-level`.
- keyboard activation with Enter or Space.

On activation, `skills-page.js` opens one floating picker anchored to the cell.
The picker contains four choices with the existing legend labels and colors. When
the user selects a level, that cell enters a saving state, the picker closes, and
the client posts JSON to a new endpoint:

```http
POST /staffing/skills/cell
Content-Type: application/json

{
  "person_odoo_id": 123,
  "skill_odoo_id": 456,
  "level": 3
}
```

Successful responses update the badge text, `lvl-N` class, `data-level`, and any
sort value used by the table. Failed responses leave the old badge in place and
show a toast with a short error.

### Backend Route

Add `POST /staffing/skills/cell` to `routes/skills.py`. It validates:

- `person_odoo_id` is present and belongs to a non-excluded local person.
- `skill_odoo_id` is present and belongs to a visible Production/Supervisor skill.
- `level` is an integer from 0 through 3.

The route runs in a thread, calls the Odoo write helper first, then mirrors the
accepted level locally in `person_skills`:

- `level == 0`: delete the local `person_skills` row.
- `level > 0`: upsert the row with `level`, `last_pushed_at = now()`, and
  `local_dirty = FALSE`.

It then invalidates the roster cache and both HTTP response cache buckets used by
the matrix, and returns the canonical level:

```json
{"ok": true, "level": 3}
```

### Odoo Client

Add an Odoo client write helper:

```python
set_employee_skill_level(employee_odoo_id: int, skill_odoo_id: int, bucket: int) -> None
```

Behavior:

- Resolve the skill's `skill_type_id` from `hr.skill`.
- For bucket 1-3, choose the matching `hr.skill.level` within that type using the
  same rank-to-bucket logic as the current sync.
- Search `hr.employee.skill` for `(employee_id, skill_id)`.
- If bucket is 0, unlink existing matching rows.
- If bucket is 1-3 and a row exists, write `skill_level_id`.
- If bucket is 1-3 and no row exists, create a row with `employee_id`, `skill_id`,
  `skill_type_id`, and `skill_level_id`.

The helper raises the existing Odoo exceptions or XML-RPC errors; the route catches
them and returns a 502-style JSON error to the browser.

### Sync Metadata

The current `skills` table already has `odoo_id`, but the sync does not populate it.
Extend `fetch_skill_columns_with_types()` and `odoo_sync.sync()` so Production and
Supervisor skill rows store `skills.odoo_id`. The matrix emits
`data-skill-odoo-id` from this column. If a skill is missing an Odoo id, render the
cell as non-editable and keep the existing read-only badge.

The existing full sync remains authoritative. If someone changes the same skill
directly in Odoo, the next forced or TTL sync updates the local row.

## Error Handling

- Missing Odoo config or auth failure: return JSON `{ok: false, error: "..."}`
  and keep the cell unchanged.
- Odoo write failure: keep the cell unchanged, clear the saving state, and show
  "Odoo save failed" plus the server message.
- Stale local row: if the employee or skill no longer exists locally, return 404
  and ask the user to refresh from Odoo.
- Missing skill level mapping: return a clear server error so setup can be fixed
  in Odoo rather than silently writing the wrong level.
- Duplicate `hr.employee.skill` rows: unlink all matches for bucket 0; for
  bucket 1-3, update the first match and unlink the remaining duplicates so the
  write leaves Odoo with one row for that employee/skill pair.

## Accessibility

- Skill cells are focusable buttons with labels like
  "Edit Maria Garcia Repair skill, current level 2 competent".
- The picker is a small dialog/menu with Escape-to-close and focus returned to the
  cell.
- Saving and error messages use the existing toast/status pattern.
- Keyboard users can open a cell with Enter/Space and choose a level with standard
  button activation.

## Testing

Unit tests:

- Odoo client maps bucket levels to the correct `hr.skill.level` for a skill type.
- Odoo client creates, updates, and unlinks `hr.employee.skill` rows.
- Sync stores `skills.odoo_id`.
- Route rejects invalid levels, missing employee ids, missing skill ids, and
  non-production/supervisor skills.
- Route writes Odoo before local DB mutation and leaves local state unchanged when
  Odoo raises.
- Successful route calls update `person_skills`, clear `local_dirty`, set
  `last_pushed_at`, and invalidate matrix caches.

Static/template tests:

- Skill cells render as accessible edit controls when both person and skill Odoo
  ids exist.
- Cells without Odoo ids render read-only.
- `skills-page.js` contains picker open/close, Escape handling, and live POST logic.

Manual smoke:

1. Open the People Matrix.
2. Click a person's skill cell.
3. Pick a different level.
4. Confirm the badge changes and the toast says saved.
5. Open that employee in Odoo and confirm the skill level changed.
6. Click "Refresh from Odoo" and confirm the edited level remains.

## Files Expected To Change

- `src/zira_dashboard/odoo_client.py` - Odoo skill write helper and level mapping.
- `src/zira_dashboard/odoo_sync.py` - store `skills.odoo_id` during sync.
- `src/zira_dashboard/routes/skills.py` - new live cell update endpoint.
- `src/zira_dashboard/templates/skills.html` - editable skill cell attributes.
- `src/zira_dashboard/static/skills-page.js` - picker, POST, saving/error states.
- `src/zira_dashboard/static/skills.css` - picker and editable cell states.
- `tests/test_odoo_client.py` - Odoo write helper coverage.
- `tests/test_odoo_sync.py` - skill Odoo id sync coverage.
- `tests/test_skills_template_render.py` - editable/read-only cell rendering.
- `tests/test_skills_static.py` - picker behavior checks.
- A route test file for `/staffing/skills/cell`.
