# Leaderboards Drill-Down Popup + Player Card Per-Day Rows — Design

**Date:** 2026-05-05
**Status:** Approved (brainstorming → implementation planning)

## Context

Two related drill-down gaps on the production-history side of the app:

1. **Player card** at `/staffing/people/{name}` shows a per-WC summary
   (Days / Units / Downtime per WC) for the selected timeframe. Dale
   wants to see the *individual days* that contribute — which day a
   person ran which WC, with that day's units and downtime.
2. **Leaderboards** at `/staffing/leaderboards` shows averages widgets
   (per-WC and per-group: Repair, Dismantler, Junior, etc.) but the
   operator names aren't clickable. Dale wants to click a name and
   see the day-by-day breakdown that produced that average — and from
   there, jump to either (a) the dashboard on a specific day or
   (b) the full player card.

These are one feature in two surfaces. Both reuse the existing
`production_history.attribution_for(day, client)` per-day attribution
data; today that data gets summed inside `attribution_range()` and the
day axis is discarded.

## Goals

1. **Player card per-day breakdown.** Below the existing per-WC summary
   table, render a per-day-per-WC table sorted newest-first. Each Date
   cell is a hyperlink to `/recycling?start=DATE&end=DATE`.
2. **Clickable averages-widget names.** Every operator name on every
   averages widget (per-WC *and* per-group) opens a detail popup
   showing that person's days contributing to the widget's average,
   filtered to the widget's scope and the page's current timeframe.
3. **Popup → dashboard.** The Date column in the popup is hyperlinked
   to `/recycling?start=DATE&end=DATE` so users can drill into a
   specific production day.
4. **Popup → player card.** The popup has an "Open full player card"
   button that navigates to `/staffing/people/{name}?start=A&end=B`,
   carrying the page's selected timeframe.

## Performance constraints (load-bearing)

This feature is occasional-use; whole-site speed matters more than
popup latency. Design rules that follow from that:

1. **Zero cost on the leaderboards page render.** The route handler
   adds nothing — no extra DB calls, no extra Zira fetches, no extra
   per-person aggregation. The only template change is wrapping
   operator names in a `<button>` with `data-*` attributes, which is
   bytes, not work. Pre-rendering popup HTML inline is rejected for
   exactly this reason.
2. **Lazy fetch on click.** The popup data only loads when the user
   actually clicks an operator name. AJAX endpoint, `<button>` →
   `fetch()` → render — no eager work.
3. **Endpoint-side caching.** The JSON endpoint caches responses per
   `(name, scope, start, end)` tuple. Past-only ranges cache
   indefinitely; ranges that include today expire on a short TTL
   (60 s) so a redeploy or new production lands the next click. The
   existing `cached_leaderboard` already handles per-day caching;
   the new endpoint cache sits on top so repeated clicks on the same
   person don't re-aggregate.
4. **No new heavy paths in `production_history`.** The new
   `attribution_per_day` helper fans out per-day exactly the way
   `attribution_range` already does. It does not double-fetch — the
   underlying `cached_leaderboard` cache is shared, so calling both
   `attribution_range` and `attribution_per_day` for the same range
   in the same request hits the cache for round 2.
5. **Player-card route stays cheap on cache hit.** The per-day rows
   are computed from data the route already fetches. The added work
   is one extra Python loop over the same data — negligible.
6. **No background pre-warming for this feature.** It's
   occasional-use; warming the cache for popups that may never open
   would burn connection-pool capacity for no gain.

## Non-goals

- "Best days" top-5 widgets are not clickable. Scope is averages
  widgets only.
- Editing rows from the popup. Read-only.
- Cross-link from the player card back to a leaderboards widget.
  One-way drill-down only.
- New aggregation math. Reuses existing per-day attribution exactly.
- Inline-expanding rows (no `<details>`). Click-to-modal is the only
  interaction.

## Design

### 1. Player-card per-day breakdown

#### Data flow

Add a sibling helper to `production_history.py`:

```python
def attribution_per_day(
    start: date,
    end: date,
    client,
) -> list[tuple[date, dict[str, dict[str, dict[str, float]]]]]:
    """Concurrent fan-out over [start, end]. Returns a list of
    (day, attribution_for_that_day_dict) tuples sorted by date asc.
    The dict shape is identical to attribution_for(day, client).
    """
```

This mirrors the per-day fan-out already inside `attribution_range`
but preserves the day axis instead of summing.

#### Route change

In `routes/people.py:staffing_player_card`, after computing the
existing `range_out` summary, also call `attribution_per_day` and
build a flat list of per-day-per-WC rows for the requested person:

```python
day_rows: list[dict] = []
for day, daily in attribution_per_day(start_d, end_d, client):
    person_data = daily.get(name, {})
    for wc_name, totals in person_data.items():
        day_rows.append({
            "date": day,
            "wc": wc_name,
            "units": totals["units"],
            "downtime": totals["downtime"],
        })
day_rows.sort(key=lambda r: (r["date"], r["wc"]), reverse=True)
```

Pass `day_rows` to the template alongside the existing context.

#### Template change

Append a new table to `templates/player_card.html` after the existing
per-WC summary:

```jinja
{% if day_rows %}
<h3 style="margin-top:1rem">Per-day breakdown</h3>
<table class="pc">
  <thead>
    <tr><th>Date</th><th>Work Center</th><th class="num">Units</th><th class="num">Downtime (min)</th></tr>
  </thead>
  <tbody>
    {% for r in day_rows %}
    <tr>
      <td><a href="/recycling?start={{ r.date }}&end={{ r.date }}">{{ r.date }}</a></td>
      <td>{{ r.wc }}</td>
      <td class="num">{{ '{:,.0f}'.format(r.units) }}</td>
      <td class="num">{{ '{:,.0f}'.format(r.downtime) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
```

The existing per-WC summary table stays unchanged.

### 2. Leaderboards averages-widget popup

#### JSON endpoint

`GET /api/staffing/leaderboards/person-days`

**Query params:**
- `name=Carlos` (required)
- `wc=Repair-1` (mutually exclusive with `group`)
- `group=Repair` (mutually exclusive with `wc`)
- `start=YYYY-MM-DD` (required)
- `end=YYYY-MM-DD` (required)

**Behavior:**
- 400 if `name` missing, both/neither of `wc`/`group` set, or dates
  unparseable.
- Cache the response per `(name, wc-or-group, start, end)`. Range
  fully in the past → cache for 1 h. Range includes today → cache for
  60 s. The cache is in-process (same `_HTTP_CACHE` style as the
  existing dashboard response cache, or a small TTLCache local to
  this endpoint).
- On cache miss: for each day in `[start, end]` (ascending), pull the
  per-day attribution via `attribution_per_day`, filter to the
  requested `name`, and filter the inner `{wc: totals}` dict to
  entries whose WC name matches the scope.
  - `wc=X` scope: keep entries where the key equals `X`.
  - `group=Y` scope: keep entries where the WC's category equals `Y`.
    Category lookup uses `staffing.LOCATIONS` — match `loc.skill ==
    Y` (the existing category column on the leaderboards page).
- Aggregate the kept entries per day: sum units, sum downtime, collect
  WC names sorted alphabetically.
- Drop days where the person has no matching production.
- Sort the output rows by date **descending** so the popup shows
  newest first.

**Response shape:**

```json
{
  "rows": [
    {"date": "2026-04-22", "wcs": ["Repair-1", "Repair-2"], "units": 187, "downtime": 12},
    {"date": "2026-04-21", "wcs": ["Repair-1"], "units": 95, "downtime": 0}
  ]
}
```

`units` and `downtime` are returned as numbers (no rounding) — the
client formats for display.

#### Template change

In `templates/leaderboards.html`, the operator-name cells on every
averages widget become clickable buttons. Add a `data-*` attributes
block so JS knows the scope:

```jinja
{# Inside per-WC averages widget #}
<button type="button"
        class="lb-name-btn"
        data-name="{{ row.name }}"
        data-wc="{{ wc.name }}"
        data-start="{{ start }}" data-end="{{ end }}">
  {{ row.name }}
</button>

{# Inside per-group averages widget #}
<button type="button"
        class="lb-name-btn"
        data-name="{{ row.name }}"
        data-group="{{ group.name }}"
        data-start="{{ start }}" data-end="{{ end }}">
  {{ row.name }}
</button>
```

Style `.lb-name-btn` to look identical to the existing plain text
(transparent background, no border, inherits font and color, but
`cursor: pointer` and a subtle hover underline so the affordance is
clear). The visible cell appearance doesn't change — only the
behavior.

#### Modal HTML + CSS

Reuse the existing `.popover-backdrop` + `.popover` style block from
`past_schedules.html`. Inline a single hidden modal at the bottom of
`leaderboards.html`:

```html
<div id="lb-popup-bd" class="popover-backdrop">
  <div class="popover" id="lb-popup" style="max-width:34rem">
    <h4 id="lb-popup-title">…</h4>
    <table class="pc" id="lb-popup-table">
      <thead>
        <tr><th>Date</th><th>Work Centers</th><th class="num">Units</th><th class="num">Downtime</th></tr>
      </thead>
      <tbody></tbody>
    </table>
    <p id="lb-popup-empty" style="color:var(--muted);font-style:italic;display:none">No production days for this person in the selected range.</p>
    <div class="actions">
      <a id="lb-popup-card-link" href="#" class="ghost"
         style="text-decoration:none;display:inline-flex;align-items:center;padding:0.4rem 0.85rem">
        Open full player card →
      </a>
      <button type="button" class="ghost" onclick="closeLbPopup()">Close</button>
    </div>
  </div>
</div>
```

#### JS

Inline at the bottom of `leaderboards.html`. Handles:

```javascript
async function openLbPopup(btn) {
  const name = btn.dataset.name;
  const wc = btn.dataset.wc || '';
  const group = btn.dataset.group || '';
  const start = btn.dataset.start;
  const end = btn.dataset.end;

  // Header
  const scopeLabel = wc ? wc : (group + ' group');
  document.getElementById('lb-popup-title').textContent =
    `${name} — ${scopeLabel} · ${start} → ${end}`;

  // Player card link, carrying the timeframe
  const cardUrl = `/staffing/people/${encodeURIComponent(name)}?start=${start}&end=${end}`;
  document.getElementById('lb-popup-card-link').href = cardUrl;

  // Show backdrop, fetch
  const bd = document.getElementById('lb-popup-bd');
  bd.classList.add('show');
  const tbody = document.querySelector('#lb-popup-table tbody');
  tbody.innerHTML = '<tr><td colspan="4" style="color:var(--muted)">Loading…</td></tr>';
  document.getElementById('lb-popup-empty').style.display = 'none';

  const params = new URLSearchParams({ name, start, end });
  if (wc) params.set('wc', wc); else params.set('group', group);

  try {
    const r = await fetch('/api/staffing/leaderboards/person-days?' + params);
    const data = await r.json();
    renderLbPopupRows(data.rows || []);
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="4" style="color:var(--bad)">Failed to load.</td></tr>';
  }
}

function renderLbPopupRows(rows) {
  const tbody = document.querySelector('#lb-popup-table tbody');
  const empty = document.getElementById('lb-popup-empty');
  if (!rows.length) {
    tbody.innerHTML = '';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td><a href="/recycling?start=${r.date}&end=${r.date}">${r.date}</a></td>
      <td>${r.wcs.join(', ')}</td>
      <td class="num">${Math.round(r.units).toLocaleString()}</td>
      <td class="num">${Math.round(r.downtime).toLocaleString()}</td>
    </tr>
  `).join('');
}

function closeLbPopup() {
  document.getElementById('lb-popup-bd').classList.remove('show');
}

// Wire everything up
document.querySelectorAll('.lb-name-btn').forEach(btn => {
  btn.addEventListener('click', () => openLbPopup(btn));
});
document.getElementById('lb-popup-bd').addEventListener('click', e => {
  if (e.target.id === 'lb-popup-bd') closeLbPopup();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeLbPopup();
});
```

## Components and data flow

```
[user clicks operator name on averages widget]
        |
        v
  openLbPopup(btn)  ── reads data-name / data-wc|group / data-start/end
        |
        v
  fetch /api/staffing/leaderboards/person-days?name=...&wc=...&start=...&end=...
        |
        v
  routes/leaderboards.py: person_days_json()
        |
        v
  production_history.attribution_per_day(start, end, client)
        |   list[(day, {person: {wc: {units, downtime, hours, days_worked}}})]
        v
  filter to person + scope, aggregate per day, sort desc
        |
        v
  JSON response
        |
        v
  renderLbPopupRows()  ── populates modal table
        |
        v
  user clicks Date         user clicks "Open full player card"
        |                          |
        v                          v
  /recycling?start=D&end=D   /staffing/people/{name}?start=A&end=B
                                       |
                                       v
                              player card with per-WC summary
                              + per-day breakdown table
```

## Testing

**Unit tests** (`tests/test_production_history.py` — extend existing or new file):

1. `test_attribution_per_day_returns_one_entry_per_day_in_order` —
   given a 3-day range with mocked single-day attribution data,
   returns 3 tuples sorted ascending by date.
2. `test_attribution_per_day_skips_days_with_no_data` — days with
   empty attribution still appear in the list (with an empty dict)
   to keep date alignment predictable; the route filters empties.

**Route / endpoint tests** (`tests/test_leaderboards_person_days.py` — new):

3. `test_person_days_filters_to_single_wc` — `?wc=Repair-1` returns
   only rows where the WC matches; multiple-WC days are filtered
   to just the matching one.
4. `test_person_days_aggregates_group_scope` — `?group=Repair` for a
   day where the person ran Repair-1 + Repair-2 returns one row
   with both WCs in `wcs`, summed units/downtime.
5. `test_person_days_400_when_neither_wc_nor_group` — both missing
   returns 400.
6. `test_person_days_400_when_both_wc_and_group` — both set returns
   400.
7. `test_person_days_400_on_unparseable_dates` — bad ISO date returns
   400.
8. `test_person_days_returns_empty_rows_when_no_match` — person who
   didn't work in the scope/range returns `{"rows": []}` and 200.

**Player card tests** (extend `tests/test_player_card.py` if it exists,
or new file):

9. `test_player_card_renders_per_day_breakdown` — given mocked
   attribution data, the rendered HTML contains one row per
   (day, WC) for the requested person, with date hyperlinks.

**Visual / manual:**

- Open `/staffing/leaderboards`, pick any range, click any operator
  name on any averages widget. Verify popup opens, table populates,
  date links navigate to `/recycling?start=DATE&end=DATE`, "Open
  full player card" navigates to the card with the same timeframe.
- Verify Esc / backdrop click / × button all close the popup.
- Open `/staffing/people/{name}` directly, verify the per-day
  breakdown table renders below the per-WC summary, dates link to
  the recycling dashboard.

DB-bound tests will follow the existing pattern in
`test_dashboards_polish.py` (require `DATABASE_URL`); pure-helper
tests (1, 2) run without it.

## Files touched

- `src/zira_dashboard/production_history.py` — add
  `attribution_per_day()`. No changes to existing functions.
- `src/zira_dashboard/routes/leaderboards.py` — add
  `person_days_json()` JSON endpoint at
  `/api/staffing/leaderboards/person-days`.
- `src/zira_dashboard/routes/people.py` — extend
  `staffing_player_card` to compute and pass `day_rows`.
- `src/zira_dashboard/templates/leaderboards.html` — wrap operator
  names in `<button class="lb-name-btn" data-...>`; inline modal
  HTML and JS at the bottom; `<style>` rules for `.lb-name-btn` and
  `.popover-backdrop` / `.popover` (or import from a shared block —
  see below).
- `src/zira_dashboard/templates/player_card.html` — add per-day
  breakdown table.
- `tests/test_production_history.py` (extend or new) — unit tests
  for `attribution_per_day`.
- `tests/test_leaderboards_person_days.py` (new) — endpoint tests.
- `tests/test_player_card.py` (extend or new) — render test.
- `CHANGELOG.md` — entry for the deploy.

## Implementation notes

- The `.popover-backdrop` + `.popover` CSS currently lives only in
  `past_schedules.html`. Copy the necessary subset inline into
  `leaderboards.html` rather than extracting a partial — extracting
  is its own refactor and the duplication is small (~15 lines).
- `attribution_per_day` and `attribution_range` both fan out per-day
  with a `ThreadPoolExecutor`. Keep the same pool sizing (default
  matches existing `attribution_range`) so a multi-month range
  doesn't suddenly explode connection-pool usage.
- The `wc=` vs `group=` mutual-exclusivity check in the endpoint
  keeps the contract narrow. Don't accept "neither" as a default to
  "all" — that's a different feature.
- For per-group scope, use `loc.skill` from `staffing.LOCATIONS` as
  the category. This matches what the leaderboards page already
  treats as "group" today (Dismantler / Repair / Junior / etc.).
- The popup's hyperlinked Date column generates absolute paths
  (`/recycling?start=...&end=...`) so it works regardless of where
  the leaderboards page is mounted.
- Endpoint cache invalidation: don't bother. The 60s TTL for
  today-including ranges is short enough that staleness is
  imperceptible on the rare popup open. Past-only ranges return
  immutable data; the 1 h TTL is just to bound memory.
- Connection pool: the new endpoint shares the same Postgres pool +
  Zira client as the rest of the app. A multi-month range in the
  popup would fan out into many parallel `attribution_for(d, client)`
  calls. The existing `attribution_range` thread pool sizes are the
  same shape — no new tuning needed, but worth confirming on first
  use (a year-long range will fire 200-ish parallel calls; cached
  past days return instantly).
