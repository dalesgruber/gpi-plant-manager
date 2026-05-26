# Hourly-only Late/Absence Report

**Problem:** The Late/Absence Report flags Dale, Wendy, Ian, and other
salaried/management folks every morning because they're active,
non-reserve people who aren't on a scheduled station. These people
have flexible start times and shouldn't trigger the late workflow.

**Fix:** Filter the report to people whose Odoo `hr.employee.wage_type`
is `'hourly'`. Salaried (`'monthly'` in Odoo parlance) and unknown wage
types are excluded from all three late-report sections.

## Data source

- Field: `hr.employee.wage_type` (selection: `monthly`, `hourly`)
- Lives directly on `hr.employee`; no `hr.contract` fallback needed.

## Implementation

1. **Schema** — add nullable column to `people`:
   ```sql
   ALTER TABLE people ADD COLUMN IF NOT EXISTS wage_type TEXT;
   ```
2. **Odoo fetch** — add `wage_type` to the `fields` list in
   `odoo_client.fetch_employees`.
3. **Sync** — write `wage_type` through the Odoo→people sync alongside
   name/active/etc. (the existing sync_people path).
4. **Dataclass** — add `wage_type: str | None = None` to `staffing.Person`.
   Load it in `load_roster`'s SELECT and Person construction.
5. **Filter** — at the `late_people_for_day_v2` call site in
   `routes/staffing.py` (around line 1008), compute the set of hourly
   emp_ids from the roster and filter both `scheduled_emp_ids` and
   `unscheduled_emp_ids` before calling the builder. Filtering here
   (not in `_safe_attendance`) keeps the rest of the staffing page —
   which also uses `_safe_attendance` for its attendance display —
   unaffected.

## Behavior

- `wage_type == 'hourly'` → included in late report (same as today).
- `wage_type == 'monthly'` → excluded from all three sections.
- `wage_type` NULL/unknown → excluded (safer default; if Odoo sync
  hasn't populated it yet for someone, better to skip than nag).

## Out of scope

- No UI for overriding wage_type per-person. If Odoo wage_type is
  wrong, fix it in Odoo.
- No changes to the staffing page's attendance display — salaried
  folks who happen to be scheduled (rare) still appear there with
  their attendance status.
