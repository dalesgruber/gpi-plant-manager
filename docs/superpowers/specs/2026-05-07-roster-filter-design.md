# Roster Filter — Per-Person Exclusion from Current Views

**Date:** 2026-05-07
**Status:** Approved (brainstorming → implementation planning)

## Context

The Odoo sync (`odoo_sync.sync()`) runs every hour and upserts every
`hr.employee` from Odoo into the local `people` table. Local-only
columns (currently just `reserve`) are preserved across syncs because
the upsert clauses only touch the columns we want Odoo to be the
source of truth for (`name`, `active`, `last_pulled_at`).

Dale wants a way to exclude specific people from current roster views
without affecting historical data (past schedules, leaderboards
rankings, attribution reports). Use cases:

- Office staff or managers that show up in the Odoo employee list but
  shouldn't appear in scheduler / matrix dropdowns.
- People who left and need to be hidden from current pickers but
  whose historical assignments and attendance records must stay
  intact for retrospective reporting.

## Goals

1. New `excluded BOOLEAN DEFAULT FALSE` column on `people`,
   preserved across Odoo syncs (same pattern as `reserve`).
2. New Settings sub-tab **Roster Filter** showing every person in
   the local `people` table with a checkbox; click to toggle the
   `excluded` flag for that `odoo_id`.
3. Every "current roster" surface filters out excluded people
   automatically. The single funnel point is `staffing.load_roster()`
   — apply the filter there and downstream consumers (People Matrix,
   scheduler pickers, player-card picklist, late/absence report's
   unscheduled detection, etc.) inherit the change.
4. Historical data (past `schedule_assignments` rows, leaderboards
   queries, attendance history, attribution rollups) is **not**
   filtered. Excluded people who appear in old data continue to show
   up in views of that old data.

## Non-goals

- No bulk-select / select-all UI. ~30 active employees max; click
  each checkbox individually.
- No per-person "exclusion reason" notes.
- No audit trail of who toggled what when.
- No write-back to Odoo. Exclusion is a local-only concept.
- Sync continues to refresh `name`, `active`, and skill data for
  excluded people. The `excluded` flag never gates the sync — it
  only gates view-time queries.
- The player-card URL `/staffing/people/{name}` continues to render
  for excluded people if you navigate there directly (historical
  reference). The new picklist doesn't list them.

## Design

### 1. Schema change

Add a single column. Idempotent so re-running `bootstrap_schema()`
is safe.

```sql
ALTER TABLE people ADD COLUMN IF NOT EXISTS excluded BOOLEAN NOT NULL DEFAULT FALSE;
```

No index — the column has low cardinality and the active-roster
queries are already small.

### 2. Sync preserves the flag

`odoo_sync.sync()` upserts `people` with `(odoo_id, name, active,
last_pulled_at)`. The `ON CONFLICT (odoo_id) DO UPDATE` clause names
those four columns explicitly, so `reserve` and the new `excluded`
are never touched. **No code change required in `odoo_sync.py`** —
the existing pattern handles new local-only columns automatically.

### 3. `staffing.load_roster()` filters out excluded

The funnel for current-roster views. Today it returns all rows from
`people` (active and inactive both, sorted to put inactives at the
bottom). Add a `WHERE NOT excluded` clause to the SELECT:

```python
def load_roster() -> list[Person]:
    """Load all NON-EXCLUDED people from Postgres. Cached 60s."""
    ...
    rows = db.query(
        "SELECT p.id, p.name, p.active, p.reserve, p.odoo_id, "
        "  COALESCE(json_object_agg(s.name, ps.level) "
        "           FILTER (WHERE s.name IS NOT NULL), '{}'::json)::text AS skills_json "
        "FROM people p "
        "LEFT JOIN person_skills ps ON ps.person_id = p.id "
        "LEFT JOIN skills s ON s.id = ps.skill_id "
        "WHERE NOT p.excluded "                  # ← new
        "GROUP BY p.id "
        "ORDER BY (NOT p.active), lower(p.name)"
    )
    ...
```

Cache invalidation: when an exclusion toggles, call
`staffing._invalidate_roster_cache()` so the next read sees the
updated set.

Downstream surfaces that use `load_roster()`:
- `routes/skills.py` — People Matrix
- `routes/staffing.py` — Plant Scheduler picker, Unscheduled list,
  Reserves list, late/absence report's unscheduled detection
  (via `_safe_attendance`)
- `routes/people.py` — player-card picklist (`roster_names`)
- `routes/leaderboards.py` — referenced for active-roster context

All of these inherit the filter automatically. **No per-route
changes required.**

### 4. Settings sub-tab + sidebar entry

In `templates/settings.html` add a new sidebar item between **Work
Centers & Goals** and **Company Schedule**:

```jinja
<a href="?section=roster_filter"
   class="settings-nav-item {% if active_section == 'roster_filter' %}active{% endif %}">
  Roster Filter
</a>
```

Add a new section panel in the same template:

```jinja
<section class="panel" id="roster-filter-panel"
         {% if active_section != 'roster_filter' %}style="display:none"{% endif %}>
  <h2>Roster Filter</h2>
  <p class="note">
    Uncheck to hide a person from active roster views (People Matrix,
    scheduler dropdowns, late/absence report, etc.). Historical data
    stays intact — past schedules, leaderboards, and attendance
    records still show them.
  </p>
  <ul class="roster-filter-list">
    {% for p in roster_filter_rows %}
    <li class="roster-filter-row" data-odoo-id="{{ p.odoo_id }}">
      <label>
        <input type="checkbox"
               class="roster-filter-toggle"
               {% if not p.excluded %}checked{% endif %}>
        <span class="roster-filter-name">{{ p.name }}</span>
        <span class="roster-filter-meta">(Odoo #{{ p.odoo_id }})</span>
      </label>
    </li>
    {% endfor %}
  </ul>
</section>
```

Sort: alphabetical by `name`. Includes both `active` and inactive
Odoo employees so Dale can also explicitly exclude inactive ones.

Inline JS at the bottom of `settings.html` toggles via fetch:

```javascript
document.querySelectorAll('.roster-filter-toggle').forEach(function (cb) {
  cb.addEventListener('change', function () {
    var li = cb.closest('.roster-filter-row');
    var odoo_id = li.dataset.odooId;
    var excluded = !cb.checked;
    fetch('/api/settings/roster-filter/toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({odoo_id: odoo_id, excluded: excluded}),
    }).then(function (r) {
      if (!r.ok) {
        // Roll back the visual on failure.
        cb.checked = !cb.checked;
      }
    }).catch(function () {
      cb.checked = !cb.checked;
    });
  });
});
```

### 5. New API endpoint

```
POST /api/settings/roster-filter/toggle
  body: {odoo_id: int, excluded: bool}
  → UPDATE people SET excluded = $excluded WHERE odoo_id = $odoo_id
  → invalidate the roster cache + cert lookup cache
  → returns {ok: true}
```

### 6. Settings route — load roster filter rows

In `routes/settings.py`'s settings handler, when `active_section ==
'roster_filter'`, query directly (bypassing `load_roster()` so we
see excluded people too):

```python
roster_filter_rows = db.query(
    "SELECT odoo_id, name, excluded "
    "FROM people "
    "WHERE odoo_id IS NOT NULL "
    "ORDER BY lower(name)"
)
```

Pass `roster_filter_rows` to the template context.

The query bypasses `load_roster()` because the filter UI must show
excluded people (otherwise you can't un-exclude them). Other
sections of the settings page are unaffected.

## Components and data flow

```
[Odoo sync — every hour]
        ↓
  upsert into people (name, active, last_pulled_at)
  — leaves `excluded` and `reserve` untouched
        ↓
[Settings UI: Roster Filter]
        ↓ user toggles checkbox
  POST /api/settings/roster-filter/toggle
        ↓
  UPDATE people SET excluded = ?
        ↓
  invalidate roster + cert caches
        ↓
[Next pageview anywhere]
        ↓
  staffing.load_roster() — now WHERE NOT excluded
        ↓
  People Matrix / scheduler / picklists / late report / etc.
  — excluded people don't appear in current views
        ↓
[Past-data views — past_schedules, leaderboards, attendance]
        ↓
  Query schedule_assignments / production_history / etc. directly
  by name. NOT filtered. Excluded people still appear in old data.
```

## Testing

**Unit tests** (`tests/test_staffing_roster_filter.py` — new):

1. `test_load_roster_skips_excluded` — given a roster with one
   excluded row, `load_roster()` returns only non-excluded entries.
2. `test_invalidate_roster_cache_after_toggle` — toggling exclusion
   followed by `load_roster()` reflects the change immediately
   (cache busted).

**Endpoint test** (`tests/test_roster_filter_route.py` — new):

3. `test_toggle_endpoint_requires_odoo_id` — missing or non-integer
   `odoo_id` returns 400.
4. `test_toggle_endpoint_writes_excluded_flag` — POST with
   `{odoo_id: 123, excluded: true}` runs the right UPDATE; mock the
   db.execute and assert the call.

**Sync regression** (`tests/test_odoo_sync.py` — extend):

5. `test_sync_preserves_excluded_flag` — given a person with
   `excluded=TRUE` in `people`, run sync; assert `excluded` is still
   `TRUE` afterward (the sync's UPSERT clause must not touch it).

**Visual / manual:**

- Open `/settings?section=roster_filter`. Confirm every Odoo-synced
  person is listed with a checkbox; uncheck someone, refresh
  `/staffing/skills` — they're gone from the matrix. Re-check, they
  reappear.
- Open `/staffing/past?day=<a date they were assigned>`. Confirm
  they still appear in that historical view.

DB-bound tests skip without `DATABASE_URL` (existing project pattern).

## Files touched

- `src/zira_dashboard/db.py` — DDL: `ALTER TABLE people ADD COLUMN IF
  NOT EXISTS excluded BOOLEAN NOT NULL DEFAULT FALSE`.
- `src/zira_dashboard/staffing.py` — `load_roster()` SELECT adds
  `WHERE NOT p.excluded`.
- `src/zira_dashboard/routes/settings.py` — `roster_filter` section
  loads rows; new `POST /api/settings/roster-filter/toggle` handler.
- `src/zira_dashboard/templates/settings.html` — sidebar entry +
  new panel + inline JS for toggles.
- `tests/test_staffing_roster_filter.py` (new), `tests/test_roster_filter_route.py` (new),
  `tests/test_odoo_sync.py` (extend).
- `CHANGELOG.md` — entry for the deploy.

## Implementation notes

- The DDL change is additive and idempotent. No data migration —
  existing rows default to `excluded = FALSE` (i.e., visible),
  matching the pre-change behavior.
- The `_invalidate_roster_cache()` call after a toggle is the same
  call the Odoo sync uses, so the patterns match.
- No need to touch `routes/skills.py`, `routes/staffing.py`,
  `routes/people.py`, `routes/leaderboards.py` — they all consume
  `load_roster()` and inherit the filter.
- The toggle endpoint goes under `/api/settings/...` (not
  `/api/people/...`) because it's a settings concern and the
  settings page owns the route module.
- `_safe_attendance` (in `routes/staffing.py`) gets active
  non-reserve people via `staffing.load_roster()`, so it inherits
  the filter automatically — excluded people no longer appear in
  the late/absence report's "Unscheduled" section either.
