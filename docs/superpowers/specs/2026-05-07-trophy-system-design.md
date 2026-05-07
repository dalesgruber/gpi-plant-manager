# Trophy System — Badges, Trophies, Awards

**Date:** 2026-05-07
**Status:** Approved (brainstorming → implementation planning)

## Context

The plant has the data (`daily_records` from
`production_history`) to declare winners, but no recognition layer.
Dale wants per-person achievement icons on the player card and a
public **Trophy Case** page that shows monthly champions, annual top
days, all-time GOATs, and best-average winners per group and per WC.

Three tiers, all derived from the existing data:

- **Badge** — monthly recognition.
- **Trophy** — annual recognition.
- **Award** — all-time GOAT.

## Goals

1. **Monthly badges** — top-3 single-day units in each group's WCs
   during the month → gold / silver / bronze, per group (Repairs,
   Dismantlers, Juniors).
2. **Annual top-day trophies** — top-3 single-day units in each group's
   WCs during the year → gold / silver / bronze, per group.
3. **Annual best-avg trophies (group)** — best avg pph across each
   group's WCs during the year, gated on ≥30 days worked in that
   group → one trophy per group per year.
4. **Annual best-avg trophies (WC)** — best avg pph in each individual
   WC during the year, gated on ≥30 days in that WC → one trophy per
   WC per year. Skipped silently if nobody qualifies.
5. **GOAT awards** — per-group all-time best single-day units, with
   first-to-set on tie. Holder is replaced when someone has a strictly
   better day.
6. **Manual overrides** — Dale can reassign or delete any awarded
   slot, with optional note. Reset-to-computed restores the live
   result.
7. **Player card section** — text-list of awards earned by that
   person, hidden when empty.
8. **Trophy Case page** — new top-nav entry, year/month picker, all
   awards browsable.

## Non-goals

- No notification system (Slack, email) on award change.
- No "previous holders" history for trophies/badges. Only GOATs track
  prior holders (since they're displaced by new records).
- No filtering on the trophy case beyond year/month selection.
- No public leaderboard of "most badges earned" or career totals.
- No badge/trophy creation UI for new categories — Repairs,
  Dismantlers, Juniors are wired in; new groups get awards
  automatically because the engine iterates `registered_groups()`.
- No award-icon customization (emoji only for v1).

## Design

### 1. Data model

One new table — `award_overrides`. **No `awards` table** — winners
are computed live every render from `daily_records`. The override
table is the only persistent state.

```sql
CREATE TABLE IF NOT EXISTS award_overrides (
  id            SERIAL PRIMARY KEY,
  scope         TEXT NOT NULL,        -- see scope values below
  group_name    TEXT,                  -- 'Repairs' / 'Dismantlers' / 'Juniors' or NULL
  wc_name       TEXT,                  -- WC name for trophy_best_avg_wc; NULL otherwise
  year          INT,                   -- NULL only for award_goat
  month         INT,                   -- 1-12 for badges; NULL for annual / all-time
  position      INT NOT NULL,          -- 1=gold, 2=silver, 3=bronze; 1 for single-winner scopes
  action        TEXT NOT NULL,         -- 'replace' or 'delete'
  name          TEXT,                  -- new winner when action='replace'; NULL when 'delete'
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  note          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS award_overrides_slot ON award_overrides
  (scope, COALESCE(group_name,''), COALESCE(wc_name,''),
   COALESCE(year,0), COALESCE(month,0), position);
```

**Scope values:**

| Scope | Group | WC | Year | Month | Position |
|---|---|---|---|---|---|
| `badge` | required | — | required | required | 1/2/3 |
| `trophy_top_day` | required | — | required | — | 1/2/3 |
| `trophy_best_avg_group` | required | — | required | — | 1 |
| `trophy_best_avg_wc` | — | required | required | — | 1 |
| `award_goat` | required | — | — | — | 1 |

The unique index ensures one override per slot. Reassigning the same
slot replaces the prior override (`ON CONFLICT (...) DO UPDATE`).

### 2. Computation engine — `awards.py`

New module, pure functions over `daily_records`, no caching.

```python
# Helpers
def person_days_in_group(group_name, start, end) -> list[dict]:
    """One row per (person, day) summing units/hours across the
    group's WCs. Filters to days where units > 0."""

def person_days_in_wc(wc_name, start, end) -> list[dict]:
    """Same but for a single WC."""

# Public award computation
def monthly_badges(group_name, year, month) -> list[dict]:
    """Top 3 by units desc; ties broken by pph desc, then name asc.
    Returns up to 3 entries: {position, name, day, units, pph}."""

def annual_top_days(group_name, year) -> list[dict]:
    """Same shape as monthly_badges, scoped to full year."""

def annual_best_avg_group(group_name, year) -> dict | None:
    """Sum units/hours across the group's WCs, group by person,
    keep only people with days ≥ 30. Highest avg pph wins.
    Returns {name, pph, days, units, hours} or None."""

def annual_best_avg_wc(wc_name, year) -> dict | None:
    """Same but at single-WC granularity."""

def goat(group_name) -> dict | None:
    """All-time max single-day units in the group. Earliest day
    wins on tie. Returns {name, day, units, pph} or None."""

# Override layer
def apply_overrides(slot_list, scope, **slot_keys) -> slot_list:
    """Layer override rows on the computed result. Replaces names,
    drops deleted slots."""

# Player-card lookup
def awards_earned_by(name, today) -> list[dict]:
    """Reverse lookup. Walks all award functions for visible periods
    and returns ones this person earned. Used by player card."""
```

`apply_overrides` is called as the last step of every public function.
Callers never see raw computed results — they always see the
corrected list.

**Performance:** A full year of `daily_records` for ~30 people × ~10
WCs × ~250 days ≈ 75k rows. Postgres aggregations finish in
milliseconds. No materialization needed for v1.

### 3. Player card section

New section in `templates/player_card.html`, between the
`.pc-group-avgs` row and the per-WC table:

```
╔═══ Trophy case ═════════════════════════════════════════════╗
║ 🐐 GOAT — Repairs (162 units, 2026-04-12)                   ║
║ 🏆 Best Repairer 2026 — 13.4 pph (188 days)                 ║
║ 🏆 Best Repair 1 of 2026 — 14.1 pph (62 days)               ║
║ 🥇 Repairs — 2026-04 (162 units)                            ║
║ 🥈 Dismantlers — 2026-03 (138 units)                        ║
║ 🥉 Repairs — 2026-02 (121 units)                            ║
╚════════════════════════════════════════════════════════════╝
```

- Hidden entirely when `awards_earned_by(name, today) == []`.
- Order: GOATs first → annual trophies (newest year first) → monthly
  badges (newest month first). Inside the same period, gold/silver/
  bronze order.
- Each line is a hyperlink to the trophy case anchor for that scope
  (e.g., monthly badge links to `/trophies?year=2026&month=4#repairs`).

### 4. Trophy Case page

New route `/trophies` and a top-nav entry **Trophy Case** between
**Leaderboards** and **Staffing**.

Layout top-to-bottom:

**🐐 GOATs section** — three cards side by side (Repairs, Dismantlers,
Juniors). Each shows current holder + record day + units. No
"previous holders" expand for v1 (out of scope per goals).

**🏆 Annual section** — year picker (defaults to current year).
For each group:
- Top 3 days (gold/silver/bronze) with name, day, units.
- Best avg trophy (one winner) with pph and days.
- A small **Best of each WC** sub-list — one line per WC where a
  winner exists for that year (skipped silently when no qualifier).

**🥇 Monthly section** — month picker (defaults to current month).
For each group:
- Gold/silver/bronze with name, day, units.

**Overrides** — every awarded slot has a small ✏️ icon. Click opens
a modal with:
- *Reassign to…* — name dropdown (active roster).
- *Delete this slot* — drops the slot from the trophy case.
- *Reset to computed* — clears any existing override row.
- *Note* — free-text, optional.

The modal POSTs to `/api/awards/override` with
`{scope, group_name, wc_name, year, month, position, action, name, note}`.
Authorization mirrors the rest of the app (single-operator internal
tool, no auth gating today — the existing `/admin/*` routes are
similarly open). If a future iteration adds auth, the override
endpoint goes behind whatever gate is added.

## Components and data flow

```
daily_records (existing, populated by production_history)
        ↓
awards.py — pure functions over daily_records
        ↓
        ├── apply_overrides(slot_list)  ← reads award_overrides
        ↓
        ├── trophy case page (/trophies)
        └── player card section (awards_earned_by)
                ↑
        award_overrides ← admin override modal posts changes
```

## Tie-breaking and edge cases

1. **Equal units on a single-day metric:** higher pph wins, then
   alphabetical by name.
2. **GOAT tie:** first to achieve holds. Strictly-greater displaces.
3. **Equal avg pph for best-avg trophy:** more days worked wins,
   then total units, then name.
4. **Day with `hours == 0`:** skipped from all averages (defensive).
5. **No qualifying person for best-avg trophy:** trophy is silently
   skipped — neither the player card nor the trophy case shows a
   placeholder.
6. **Override name no longer in roster:** override still applies (the
   name is stored as text; person could be excluded or removed from
   Odoo). The override-modal name dropdown shows current active
   roster, but a typed name would also be accepted.

## Testing

**Unit tests** for `awards.py` (`tests/test_awards.py` — new):

1. `test_monthly_badges_top_3_by_units` — 5 person-days in a group
   month; assert positions 1-3 are the three highest by units.
2. `test_monthly_badges_tiebreak_by_pph` — two days with equal units;
   assert the one with higher pph (lower hours) ranks first.
3. `test_monthly_badges_only_within_month` — days outside the
   target year/month don't appear.
4. `test_annual_best_avg_group_requires_30_days` — person with 29
   days at high pph is excluded; person with 30 days at slightly
   lower pph wins.
5. `test_annual_best_avg_group_returns_none_when_no_qualifier` —
   nobody hits 30 days in the year.
6. `test_annual_best_avg_wc_filters_to_single_wc` — sums only that
   WC's records.
7. `test_goat_first_to_set_on_tie` — two equal records on different
   days; earlier date holds.
8. `test_goat_displaced_by_strictly_better` — later strictly-greater
   record displaces.
9. `test_apply_overrides_replace` — override with action='replace'
   swaps the name in slot 2.
10. `test_apply_overrides_delete` — override with action='delete'
    removes slot 3.
11. `test_apply_overrides_no_match_passthrough` — overrides for
    different year/month don't affect the result.
12. `test_awards_earned_by_aggregates_across_types` — given fixtures
    that produce a GOAT, an annual top-3, and two monthly badges
    for one person, the lookup returns all four.

**Endpoint tests** (`tests/test_trophies_route.py` — new):

13. `test_trophies_page_renders_with_no_data` — empty daily_records;
    page returns 200 with empty sections.
14. `test_override_endpoint_replace` — POST replace; assert
    `award_overrides` row created.
15. `test_override_endpoint_delete` — POST delete; assert row
    created with action='delete'.
16. `test_override_endpoint_reset_clears_row` — POST with an action
    of `reset` deletes the existing override row.
17. `test_override_endpoint_validates_scope` — bad `scope` returns
    400.

DB-backed tests skip without `DATABASE_URL`. Pure-logic tests for
`awards.py` use stub fixtures and run anywhere.

**Visual / manual:**

- Open `/trophies` with current data, confirm GOAT cards render,
  year/month pickers update sections, ✏️ buttons open the modal.
- Override Repairs gold for current month; reload trophy case and
  player cards to confirm the new name appears.
- Reset the override; original computed winner returns.
- Open a player card with several awards; confirm trophy case
  section shows them in the documented order.

## Files touched

- `src/zira_dashboard/db.py` — add `award_overrides` DDL.
- `src/zira_dashboard/awards.py` (new) — computation engine + override
  layer.
- `src/zira_dashboard/routes/trophies.py` (new) — `/trophies` page,
  `POST /api/awards/override` endpoint.
- `src/zira_dashboard/routes/people.py` — call `awards.awards_earned_by`
  in the player-card handler, pass to template.
- `src/zira_dashboard/templates/trophy_case.html` (new) — page layout.
- `src/zira_dashboard/templates/player_card.html` — new section
  between `.pc-group-avgs` and per-WC table.
- Top-nav (duplicated across templates today — implementer adds the
  **Trophy Case** entry in each):
  - `src/zira_dashboard/templates/_staffing_base.html` (line ~68-70)
  - `src/zira_dashboard/templates/staffing.html`
  - `src/zira_dashboard/templates/new_vs.html`
  - `src/zira_dashboard/templates/recycling.html`
  - `src/zira_dashboard/templates/settings.html`
  - `src/zira_dashboard/templates/index.html`
- `src/zira_dashboard/app.py` — register the new router.
- `tests/test_awards.py` (new), `tests/test_trophies_route.py` (new).
- `CHANGELOG.md` — entry for the deploy.

## Implementation notes

- Emoji choice: 🥇🥈🥉 for badges/top-day trophies, 🏆 for best-avg
  trophies, 🐐 for GOATs. No CSS font tweaks — emoji renders fine in
  the existing dark theme.
- Trophy-case layout reuses the existing `.panel` / `.stat` styling
  conventions from settings.html and player_card.html. No new color
  tokens.
- The override modal uses an inline div + backdrop matching the
  existing leaderboard drill-down popup in `templates/_footer.html`.
- The player-card "trophy case" section header uses a small label
  `Trophy case` matching the size of `Per-day breakdown` and
  `Attendance` headers below.
- `awards.py` should expose computation functions independently of
  the override layer — that way unit tests can exercise raw winners
  before testing override application.
