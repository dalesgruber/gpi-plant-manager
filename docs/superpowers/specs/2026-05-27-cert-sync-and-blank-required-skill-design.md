# Cert Sync (Binary) + Blank Required Skill

**Problem:** Two related symptoms on the staffing page:

1. People scheduled to Truck Driver render RED ("not trained") even
   though they're CDL-certified in Odoo.
2. DOT-certified mechanics don't get the wrench icon next to their
   names, even though the cert is mapped in `cert_icons.py`.

Root cause: `odoo_sync.py` skips inserting `person_skills` rows whose
Odoo-level bucket resolves to 0. Single-level skill types (typical for
certifications — you either have "DOT Certified" or you don't) get
bucket 0 from `fetch_skill_level_buckets()` (see `odoo_client.py:137`,
which forces `bucket=0` when a skill type has only one level), so no
cert rows ever land in the local DB. `cert_lookup` and the staffing
color logic both find nothing.

Secondary problem: there is no way to make a work center's required
skill list blank. The Settings save handler silently ignores empty
submissions (`if picked_skills:` at `routes/settings.py:317`), and
`work_centers_store._effective_uncached` falls back to the hardcoded
`LOCATIONS.skill` when the DB has no required-skill rows. Even if a
user wanted a skill-agnostic WC, they can't have one.

**Fix:** Two independent changes.

1. **Cert sync as binary** — in `odoo_sync.py`, when a synced skill's
   `skill_type == 'Certifications'`, insert with `level = 3`
   ("proficient"/green), regardless of the bucket from
   `fetch_skill_level_buckets()`. Other skill types keep the existing
   bucket logic and the `level <= 0: continue` guard.

2. **Blank required skill** — persist an empty required-skill list,
   stop the LOCATIONS fallback when a WC row exists, and render
   people without skill-based color when required is empty.

## Half A: Cert sync as binary

**File:** `src/zira_dashboard/odoo_sync.py` (sync block, ~lines 139-159)

Build a `skill_name → skill_type` map from `columns_meta` once. In the
inner loop:

```python
type_by_skill = {c["name"]: c.get("type", "") for c in columns_meta}
# ... inside the per-employee loop:
for s in emp_skills.get(emp["id"], []):
    if s["skill_name"] not in columns:
        continue
    if type_by_skill.get(s["skill_name"]) == "Certifications":
        level = 3
    else:
        level = buckets.get(s["level_id"], 0)
        if level <= 0:
            continue
    cur.execute("INSERT INTO person_skills ...", (level, ...))
```

Effects:
- `cert_lookup.load_person_certs()` returns DOT-certified people →
  wrench badges appear on staffing, past_schedules, skills,
  leaderboards (all pages already wired through `_cert_badges.html`).
- Truck Driver WC's color check finds level 3 for CDL drivers → green.
- All other certs (Forklift, Spotter, CDL Manuals) start surfacing
  too. No per-cert changes needed.
- Production/Supervisor skills behavior unchanged.

## Half B: Blank required skill

### B1. Persistence — save empty list

**File:** `src/zira_dashboard/templates/settings.html` (~line 169-185)

Add a hidden marker input inside the required-skills cell, mirroring
the `default_people_present` pattern at line 187:

```html
<input type="hidden" name="{{ p }}required_skills_present" value="1">
```

**File:** `src/zira_dashboard/routes/settings.py` (~line 315-318)

Replace:
```python
picked_skills = form.getlist(prefix + "required_skills")
if picked_skills:
    updates["required_skills"] = picked_skills
```

With:
```python
if (prefix + "required_skills_present") in form:
    updates["required_skills"] = form.getlist(prefix + "required_skills")
```

Now an unchecked-all submission saves an empty list. The marker
guards against accidentally clearing on forms that don't include the
required-skills section (defensive — matches the default-people
pattern).

### B2. Effective config — drop fallback when row exists

**File:** `src/zira_dashboard/work_centers_store.py` (`_effective_uncached`, ~line 64-102)

Today the SELECT pulls `goal_per_day_override`, `min_ops`, `max_ops`,
`department`, `group_name`, `note` from `work_centers`. We need to
know whether the row exists. The existing `rec = rows[0] if rows else {}`
already distinguishes — when `rows` is empty, no row exists.

Change the required-skills handling:

```python
if req_rows:
    req = [r["name"] for r in req_rows]
elif not rec:
    # No work_centers row at all → true bootstrap. Use hardcoded default.
    req = list(required_skills_for(loc))
else:
    # Row exists but no required-skill rows → user explicitly cleared.
    req = []
```

### B3. Neutral color when required is empty

Three call sites that compute color from level today:

1. **`routes/staffing.py:487-493`** (`options_for` — the dropdown
   options). When `required` is empty, return rows with `color=None`,
   `level=None`, `trained=True`. The template treats `color=None` as
   "no inline background" — the chip renders as default foreground
   text.

2. **`routes/staffing.py:511`** (per-assigned-person color in the
   render-model loop). Same: `color=None` when `required` is empty.

3. **`routes/settings.py:131-136`** (default-people picker pool).
   Same: produce `level=None` rows when required is empty.

**Template changes:** `templates/staffing.html` and
`templates/settings.html` use the per-row color in `style="background:
{{ color }}"` or similar. Wrap the inline-style with `{% if color %}`
so a `None` color emits no `style` attribute and the chip inherits
the default foreground.

**Filter behavior:** the "WC Training" / "Show Untrained" toggle that
filters by `trained==False` becomes a no-op when required is empty,
because every row has `trained=True`. Acceptable.

## Migration / data

- No schema changes. `work_centers_required_skills` already supports
  zero rows per WC.
- No data migration. After deploy, existing WCs keep their current
  required skills until someone re-saves Settings with an empty list.

## Out of scope

- Fixing `fetch_skill_level_buckets()`'s single-level bug at
  `odoo_client.py:137-141`. Production/Supervisor skill types always
  have ≥3 levels in practice; routing certs around it is enough.
- Reworking `roster.json` legacy storage. `Person.skills` already
  loads from `person_skills`, which is the right path.
- Cert-icon catalog changes. DOT → wrench is already mapped at
  `cert_icons.py:69`. New cert types need an entry there but that's
  not this spec.

## Testing

**Unit:**
- `odoo_sync.sync()` — given a fixture with a Certifications-type
  skill whose `level_id` buckets to 0, verify a `person_skills` row
  is inserted with `level=3`. Given a Production-type skill, verify
  the existing bucket logic still runs and `level<=0` skips the insert.
- `work_centers_store._effective_uncached` — three cases:
  (a) WC row absent → falls back to `LOCATIONS.skill`.
  (b) WC row present, no req-skill rows → returns `[]`.
  (c) WC row present, with req-skill rows → returns the DB list.
- `routes/staffing.options_for(())` — returns all active people with
  `color=None`, `level=None`, `trained=True`.

**Manual:**
1. Run Odoo sync. Verify `person_skills` now has rows for everyone's
   certs (`SELECT s.name, count(*) FROM person_skills ps JOIN skills s
   ON s.id=ps.skill_id WHERE s.skill_type='Certifications' GROUP BY s.name`).
2. Open staffing page. Mechanics with DOT cert show the wrench
   badge. CDL drivers scheduled to Truck Driver render green.
3. Settings → pick a WC → uncheck all required skills → save →
   reload. Required-skills cell shows "—". Dropdown for that WC
   shows all active people with no color. Saved person renders
   without skill color.
4. Re-check the cert on the same WC and save. Coloring returns.
