# Leaderboards Follow-up Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Four polish + extension items on top of the just-shipped leaderboards redesign:
1. Drop the "Expected" column.
2. Rename `%` header → `% of Goal`.
3. Multi-column grid layout (auto-fit minmax 420px).
4. Add **group leaderboards** — one section per user-defined Group from Settings → Work Centers, showing top-5 single-day records across all WCs in the group, with a new WC column. Same drag/inactive/order semantics as WC sections.

**Architecture:** Extend `leaderboard_wc_settings` with a `kind` column (`'wc'` or `'group'`) so the same table handles both. Route loops over groups using `work_centers_store.registered_groups()` + `members("group", name)` to fetch WCs in each group; reuses `daily_records` (no new fetch). Template adds a "WC" column when the section is a group. CSS switches the active and inactive containers from a vertical stack to a CSS grid.

**Dependencies:** The four leaderboards-redesign commits (2a077de … 9ca1309) are in. `leaderboard_wc_settings` table exists.

---

## File Structure

- Modified: `src/zira_dashboard/db.py` — add `kind` column DDL (idempotent ALTER + DEFAULT)
- Modified: `src/zira_dashboard/leaderboard_settings_store.py` — `kind` parameter on every fn; PK becomes `(kind, name)`
- Modified: `src/zira_dashboard/routes/leaderboards.py` — build group sections; pass to template
- Modified: `src/zira_dashboard/templates/leaderboards.html` — drop Expected; rename %; multi-column grid; render group sections with WC column
- Modified: `tests/test_leaderboard_settings_store.py` — extend tests for `kind`

---

### Task 1: Schema + store extended for `kind`

**Files:**
- Modify: `src/zira_dashboard/db.py`
- Modify: `src/zira_dashboard/leaderboard_settings_store.py`
- Modify: `tests/test_leaderboard_settings_store.py`

- [ ] **Step 1: Add `kind` to the DDL block in `db.py`**

Replace the existing `leaderboard_wc_settings` DDL with:

```sql
CREATE TABLE IF NOT EXISTS leaderboard_wc_settings (
  kind         TEXT NOT NULL DEFAULT 'wc',
  wc_name      TEXT NOT NULL,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  is_inactive  BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (kind, wc_name)
);
-- Idempotent: add `kind` column to a pre-existing table.
ALTER TABLE leaderboard_wc_settings ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'wc';
```

The bootstrap is idempotent: fresh deploys hit `CREATE TABLE`, existing deploys hit `ALTER TABLE ADD COLUMN IF NOT EXISTS`. The first creates the composite PK directly; the second adds the column with a default and the old single-column PK on `wc_name` stays — that's fine because `kind` always defaults to `'wc'` for legacy rows.

Note: Postgres can't `ALTER TABLE ... DROP CONSTRAINT` and add a composite PK in a way that's both idempotent and safe in mixed states. The existing PK on `wc_name` is acceptable for now since group rows will always have `kind='group'` and never collide with WC rows (WC names and group names are distinct namespaces in practice). Document in a code comment.

Actually — to avoid any name collision risk and be clean, drop the old single-PK and add a composite. Make it idempotent:

```sql
-- Composite PK migration — only run if the old PK exists.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'leaderboard_wc_settings_pkey'
      AND conrelid = 'leaderboard_wc_settings'::regclass
  ) AND NOT EXISTS (
    SELECT 1 FROM pg_index i
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
    WHERE i.indrelid = 'leaderboard_wc_settings'::regclass
      AND i.indisprimary
      AND a.attname = 'kind'
  ) THEN
    ALTER TABLE leaderboard_wc_settings DROP CONSTRAINT leaderboard_wc_settings_pkey;
    ALTER TABLE leaderboard_wc_settings ADD PRIMARY KEY (kind, wc_name);
  END IF;
END $$;
```

This block runs only when the old PK is present and the new one isn't.

- [ ] **Step 2: Update `leaderboard_settings_store.py`**

Every function now takes/returns a `kind` argument. Snapshot returns `{(kind, name): {...}}` keyed by tuple, OR returns a nested dict `{kind: {name: ...}}`. Pick the latter — easier to use from the route:

```python
def snapshot() -> dict[str, dict[str, dict]]:
    """Return {kind: {name: {sort_order, is_inactive}}}.
    kinds: 'wc', 'group'."""
    from . import db
    rows = db.query(
        "SELECT kind, wc_name, sort_order, is_inactive FROM leaderboard_wc_settings"
    )
    out: dict[str, dict[str, dict]] = {"wc": {}, "group": {}}
    for r in rows:
        k = r["kind"] or "wc"
        out.setdefault(k, {})[r["wc_name"]] = {
            "sort_order": r["sort_order"],
            "is_inactive": r["is_inactive"],
        }
    return out


def set_order(kind: str, names: list[str]) -> None:
    """Upsert sort_order for each name within the given kind."""
    from . import db
    if kind not in ("wc", "group"):
        return
    with db.cursor() as cur:
        for i, name in enumerate(names):
            if not isinstance(name, str) or not name.strip():
                continue
            cur.execute(
                "INSERT INTO leaderboard_wc_settings (kind, wc_name, sort_order) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (kind, wc_name) DO UPDATE SET "
                "sort_order = EXCLUDED.sort_order, updated_at = now()",
                (kind, name.strip(), i),
            )


def set_inactive(kind: str, name: str, value: bool) -> None:
    from . import db
    if kind not in ("wc", "group"):
        return
    db.execute(
        "INSERT INTO leaderboard_wc_settings (kind, wc_name, is_inactive) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (kind, wc_name) DO UPDATE SET "
        "is_inactive = EXCLUDED.is_inactive, updated_at = now()",
        (kind, name.strip(), bool(value)),
    )
```

- [ ] **Step 3: Update tests**

Existing tests pass `set_order(["TestWC1", ...])` (no kind). Update them to pass `set_order("wc", ["TestWC1", ...])` and `set_inactive("wc", "TestWC1", True)`. Add a new test that exercises both kinds:

```python
def test_kinds_are_isolated():
    store.set_order("wc", ["TestWC1"])
    store.set_order("group", ["TestWC1"])  # same name, different kind
    snap = store.snapshot()
    assert snap["wc"]["TestWC1"]["sort_order"] == 0
    assert snap["group"]["TestWC1"]["sort_order"] == 0
    store.set_inactive("group", "TestWC1", True)
    snap = store.snapshot()
    assert snap["wc"]["TestWC1"]["is_inactive"] is False
    assert snap["group"]["TestWC1"]["is_inactive"] is True
```

- [ ] **Step 4: Bootstrap on live Postgres + run tests**

```bash
DATABASE_URL=$("C:/Users/dale.gruber/AppData/Roaming/npm/railway.cmd" variables --service Postgres --kv 2>/dev/null | grep "^DATABASE_PUBLIC_URL=" | cut -d= -f2-) && export DATABASE_URL && .venv/Scripts/python.exe -c "from zira_dashboard import db; db.init_pool(); db.bootstrap_schema(); print('schema OK')"
DATABASE_URL=... .venv/Scripts/python.exe -m pytest tests/test_leaderboard_settings_store.py -v
```

Both must pass.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/db.py src/zira_dashboard/leaderboard_settings_store.py tests/test_leaderboard_settings_store.py
git commit -m "feat(leaderboards): extend settings store with `kind` for groups + WCs"
```

---

### Task 2: Route + template — group sections, multi-column, drop Expected, rename %

**Files:**
- Modify: `src/zira_dashboard/routes/leaderboards.py`
- Modify: `src/zira_dashboard/templates/leaderboards.html`

This is the meat of the feature. Structured as one task because the route + template changes are tightly coupled.

#### Step 1: Update the route

In `routes/leaderboards.py`'s GET handler, after the existing per-WC `sections` computation, add a parallel pass for groups.

Replace the existing settings load:
```python
settings = lstore.snapshot()  # was a flat dict
```
with:
```python
settings = lstore.snapshot()  # {kind: {name: {sort_order, is_inactive}}}
wc_settings = settings.get("wc", {})
group_settings = settings.get("group", {})
```

Update the existing per-WC loop to use `wc_settings` instead of `settings`.

Add the per-group loop (after the per-WC loop, before the active/inactive sort step):

```python
from .. import work_centers_store
group_sections = []
for group_name in work_centers_store.registered_groups():
    member_locs = work_centers_store.members("group", group_name)
    member_names = {loc.name for loc in member_locs}
    if not member_names:
        continue
    group_records = [r for r in records if r["wc"] in member_names]

    # daily target per WC in this group (used for % of goal per row).
    target_by_wc = {
        loc.name: settings_store.station_target_per_day(
            Station(meter_id=loc.meter_id, name=loc.name, category=loc.skill, cell=loc.bay)
        )
        for loc in member_locs
    }

    def metric_value_g(r):
        if metric == "units":
            return r["units"]
        t = target_by_wc.get(r["wc"], 0)
        return (r["units"] / t) if t > 0 else 0.0

    group_records.sort(key=lambda r: (-metric_value_g(r), r["day"]))
    top = group_records[:5]

    # name_count = total days this operator worked at ANY WC in the group across the range.
    name_counts: dict[str, int] = {}
    for r in group_records:
        name_counts[r["person"]] = name_counts.get(r["person"], 0) + 1

    rows = []
    for i, r in enumerate(top, start=1):
        day = r["day"]
        target = target_by_wc.get(r["wc"], 0)
        rows.append({
            "rank": i,
            "name": r["person"],
            "name_count": name_counts.get(r["person"], 0),
            "day": day.isoformat(),
            "day_label": f"{day.strftime('%a')} {day.month}/{day.day}",
            "wc": r["wc"],                      # NEW for groups
            "units": r["units"],
            "pct": (r["units"] / target) if target > 0 else 0.0,
        })

    g_set = group_settings.get(group_name, {"sort_order": 0, "is_inactive": False})
    auto_inactive = not rows
    group_sections.append({
        "loc_name": group_name,
        "rows": rows,
        "is_inactive": g_set["is_inactive"] or auto_inactive,
        "is_manually_inactive": g_set["is_inactive"],
        "is_auto_empty": auto_inactive and not g_set["is_inactive"],
        "sort_order": g_set["sort_order"],
    })

# Sort + split into active/inactive (same pattern as WC sections).
sort_key_g = lambda s: s["sort_order"]
active_groups = sorted([s for s in group_sections if not s["is_inactive"]], key=sort_key_g)
inactive_groups = sorted([s for s in group_sections if s["is_inactive"]], key=sort_key_g)
```

Pass to template:
```python
"active_groups": active_groups,
"inactive_groups": inactive_groups,
```

(In addition to the existing `active_sections` and `inactive_sections` for WCs.)

Update the 3 endpoints (`/order`, `/wc/{name}/inactive`, `/wc/{name}/active`) to take a `kind` parameter. Easiest: add a query param `?kind=wc|group` (default `wc`) on each endpoint. The JS sends the right kind based on which list the section came from.

```python
@router.post("/staffing/leaderboards/order")
async def leaderboards_set_order(request: Request, kind: str = Query(default="wc")):
    from .. import leaderboard_settings_store as lstore
    body = await request.json()
    order = body.get("order") or []
    if not isinstance(order, list):
        return JSONResponse({"ok": False, "error": "order must be a list"}, status_code=400)
    if kind not in ("wc", "group"):
        return JSONResponse({"ok": False, "error": "invalid kind"}, status_code=400)
    lstore.set_order(kind, [str(x) for x in order if isinstance(x, str)])
    return JSONResponse({"ok": True})


@router.post("/staffing/leaderboards/wc/{name}/inactive")
def leaderboards_set_inactive(name: str, kind: str = Query(default="wc")):
    from .. import leaderboard_settings_store as lstore
    if kind not in ("wc", "group"):
        return JSONResponse({"ok": False, "error": "invalid kind"}, status_code=400)
    lstore.set_inactive(kind, name, True)
    return JSONResponse({"ok": True})


@router.post("/staffing/leaderboards/wc/{name}/active")
def leaderboards_set_active(name: str, kind: str = Query(default="wc")):
    from .. import leaderboard_settings_store as lstore
    if kind not in ("wc", "group"):
        return JSONResponse({"ok": False, "error": "invalid kind"}, status_code=400)
    lstore.set_inactive(kind, name, False)
    return JSONResponse({"ok": True})
```

#### Step 2: Update the template

**Drop the Expected column.** Find the `<th>Expected</th>` in the WC section table and the matching `<td>` and remove both. There may be two copies (active + inactive sections). Same in any group-section template you add.

**Rename `%` → `% of Goal`** in the `<th>%</th>` cell (and copy-paste).

**Multi-column grid** — change the active and inactive container CSS:

```css
.lb-active-list,
.lb-inactive-wrap > details-content,  /* may need .lb-inactive-content wrapper */
.lb-active-list,
.lb-active-list,
.lb-inactive-content {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 0.7rem;
}
```

Actually a `<details>` element's content is implicit — wrap the inactive sections in a `<div class="lb-inactive-content">` so we can apply grid to them. So:

```html
<details class="lb-inactive-wrap" id="lb-inactive">
  <summary>Inactive (...)</summary>
  <div class="lb-inactive-content">
    {% for s in ...inactive lists... %}
      <div class="lb-section ..." ...>...</div>
    {% endfor %}
  </div>
</details>
```

CSS:
```css
.lb-active-list,
.lb-inactive-content {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 0.7rem;
  align-items: start;
}
```

**Group sections** — render them ABOVE the per-WC sections. Each group section uses the same `.lb-section` class so drag/inactive style matches, but adds a "WC" column and a `data-kind="group"` attribute (on the section div) so the JS can route to the right endpoints.

Render order in the active list:
1. Group sections (each: header + table with WC column)
2. WC sections (existing layout, no WC column)

The active container needs to hold both kinds. A single `<div class="lb-active-list">` is fine — the grid arranges them in source order.

**Group section template snippet:**

```html
{% for s in active_groups %}
  <div class="lb-section lb-section-group" data-kind="group" data-wc="{{ s.loc_name }}" draggable="true">
    <div class="lb-section-header">
      <span class="lb-drag-handle" title="Drag to reorder">&#9776;</span>
      <h3>{{ s.loc_name }} <span class="lb-section-tag">group</span></h3>
      <button type="button" class="lb-hide-btn" title="Mark inactive">&#10005;</button>
    </div>
    {% if s.rows %}
      <table class="lb-table">
        <thead>
          <tr>
            <th>#</th><th>Operator</th><th>Date</th><th>WC</th>
            <th class="num">Units</th><th class="num">% of Goal</th>
          </tr>
        </thead>
        <tbody>
          {% for r in s.rows %}
            <tr>
              <td class="rank">{{ r.rank }}</td>
              <td class="op">{{ r.name }} <span class="lb-name-count">({{ r.name_count }})</span></td>
              <td>{{ r.day_label }}</td>
              <td>{{ r.wc }}</td>
              <td class="num">{{ r.units|round|int }}</td>
              <td class="num pct">{{ '%.0f' % (r.pct * 100) }}%</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    {% endif %}
  </div>
{% endfor %}
```

Then the existing WC sections come below (no WC column in their tables — drop those header + cell removals from Step 1's "drop Expected" pass).

The inactive section similarly contains both group and WC inactive sections. Add inactive-groups loop before the inactive-WCs loop.

`data-kind="wc"` attribute on existing WC sections too (for the JS to route correctly).

**Section tag styling:**

```css
.lb-section-tag {
  font-size: 0.65rem; color: var(--accent); background: var(--accent-dim);
  padding: 0.1rem 0.4rem; border-radius: 4px; margin-left: 0.4rem;
  text-transform: uppercase; letter-spacing: 0.5px; font-weight: 700;
}
```

#### Step 3: Update the JS

The drag-save and hide/show button handlers need to send the `kind` query param. Update the JS to read `data-kind` from each section:

```js
function saveOrder() {
  const lists = [
    {kind: 'wc',    sels: ['.lb-active-list .lb-section[data-kind="wc"]',  '.lb-inactive-content .lb-section[data-kind="wc"]']},
    {kind: 'group', sels: ['.lb-active-list .lb-section[data-kind="group"]', '.lb-inactive-content .lb-section[data-kind="group"]']},
  ];
  for (const {kind, sels} of lists) {
    const order = [];
    for (const sel of sels) {
      document.querySelectorAll(sel).forEach(s => order.push(s.dataset.wc));
    }
    fetch('/staffing/leaderboards/order?kind=' + encodeURIComponent(kind), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({order}),
    }).catch(() => {});
  }
}

document.querySelectorAll('.lb-hide-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const sec = btn.closest('.lb-section');
    const name = sec.dataset.wc;
    const kind = sec.dataset.kind || 'wc';
    const resp = await fetch(`/staffing/leaderboards/wc/${encodeURIComponent(name)}/inactive?kind=${kind}`,
                             {method: 'POST'});
    if (resp.ok) window.location.reload();
  });
});

document.querySelectorAll('.lb-show-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const sec = btn.closest('.lb-section');
    const name = sec.dataset.wc;
    const kind = sec.dataset.kind || 'wc';
    const resp = await fetch(`/staffing/leaderboards/wc/${encodeURIComponent(name)}/active?kind=${kind}`,
                             {method: 'POST'});
    if (resp.ok) window.location.reload();
  });
});
```

Drag binding stays the same — just makes sure `data-kind` is on every `.lb-section`.

#### Step 4: Smoke render

```bash
DATABASE_URL=... .venv/Scripts/python.exe -c "
from fastapi.testclient import TestClient
from zira_dashboard.app import app
c = TestClient(app)
r = c.get('/staffing/leaderboards?window=week&metric=pct')
print('status:', r.status_code, 'len:', len(r.text))
print('grid:', 'grid-template-columns' in r.text)
print('group section:', 'lb-section-group' in r.text or 'data-kind=\"group\"' in r.text)
print('% of Goal:', '% of Goal' in r.text)
print('WC col header:', '<th>WC</th>' in r.text)
print('Expected gone:', '<th>Expected</th>' not in r.text)
"
```

All should be True.

#### Step 5: Run test suite

```bash
DATABASE_URL=... .venv/Scripts/python.exe -m pytest tests/ --ignore=tests/test_dashboards_polish.py
```

#### Step 6: Commit

```bash
git add src/zira_dashboard/routes/leaderboards.py src/zira_dashboard/templates/leaderboards.html
git commit -m "feat(leaderboards): group leaderboards + multi-column grid + UX polish"
```

---

### Task 3: Push + verify

- [ ] git push origin main
- [ ] Wait for redeploy
- [ ] Visit `/staffing/leaderboards`. Confirm:
  - Grid layout (multiple columns at typical viewport widths)
  - Group sections appear at the top, each tagged "group"
  - Each group section has a "WC" column showing where the record was set
  - Per-WC sections below the group sections
  - "% of Goal" header (no plain "%")
  - Expected column gone
  - Drag works within active and within inactive
  - ✕ button on a group section moves it to inactive (and persists across reload)
  - ↶ on a manually-inactive group brings it back
