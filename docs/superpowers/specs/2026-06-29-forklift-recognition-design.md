# Forklift Recognition — Leaderboards, Trophies & GOAT Score (Stage 2)

- **Date:** 2026-06-29
- **Status:** Approved (brainstorm) — pending spec review
- **Builds on:** the forklift integration Stage 1 (`docs/superpowers/specs/2026-06-26-forklift-demand-staffing-design.md`, `2026-06-27-forklift-settings-redesign-design.md`) and the production recognition system (`2026-05-07-trophy-system-design.md`, `2026-04-30-best-averages-leaderboard-design.md`, `2026-05-11-goat-badges-design.md`).

## 1. Background & Goal

Stage 1 pulls forklift demand into the scheduler. Stage 2 (from Dale, 2026-06-26): *"performance metrics in the trophy case and leaderboards for forklift behavior."* We already store 89 days of per-day, per-driver forklift data in `forklift_driver_daily` (calls + response/handling time). This feature surfaces that as **driver recognition**, reusing the production trophy/leaderboard/GOAT machinery so it looks and behaves native — and crowns the GOAT with a **weighted composite "GOAT score"** (Dale, 2026-06-29) that blends calls, on-time %, speed, and utilization into one tunable 0–100 number.

## 2. Decisions (locked in brainstorm)

- **Drivers compete on three metrics** plus one composite: **calls** (volume), **on-time %** (quality), **response speed** (efficiency), and an **Overall GOAT score** (the weighted blend). Unclaimed calls and late reasons are **context only** — shown on the driver's player card, never ranked.
- **Placement = hybrid:** forklift **trophies** join the existing shared trophy case (`/trophies`) and drivers' player cards; a **dedicated forklift leaderboard page** holds the per-driver rankings.
- **GOAT = highest single-day GOAT score, all-time.** The composite score also drives an **"Overall score" card** on the leaderboard (alongside the three metric cards) and the "best day" trophies (annual top-3, monthly ribbons). Per-metric awards (best on-time, fastest) remain to honor specialists.
- **On-time % history:** reconstruct full per-day history (differencing the dashboard's cumulative counts) **and** capture it forward, so on-time data — and therefore the score's on-time and utilization components — are correct from day one.
- **Speed metric = time-to-claim** (`avg_ms`, the response time already stored). Handling/complete time stays available as context.
- **Volume gate everywhere:** rate metrics (on-time %, fastest) and the GOAT score require a minimum volume to qualify — this is what lets us safely include utilization (which is otherwise "98% off 2 minutes"). Per-window metric lists use ≥ `FORKLIFT_MIN_CALLS` (default 50) in the window; the per-day GOAT score uses ≥ `score_min_calls` (default 8) calls that day.

## 3. Composite GOAT score — `forklift_score.py` (new, pure)

A per-driver, per-day score in **0–100**, a weighted blend of four components, each normalized against an **absolute target** so a score means the same thing every day (essential for a comparable all-time GOAT — a "curve vs. today's field" would let a weak day's 100 outrank a strong day's 90).

Sub-scores (each clamped to 0–100):
- **Calls:** `s_calls = min(100, calls / target_calls × 100)` — default `target_calls = 25`.
- **On-time %:** `pct = on_time / (on_time + late) × 100` (→ 0 when no calls); `s_ontime = clamp((pct − floor) / (100 − floor) × 100)` — default `floor = 80` (spreads the 80–100 % band that actually varies).
- **Speed:** `secs = avg_ms / 1000`; `s_speed = clamp((slow − secs) / (slow − fast) × 100)` — defaults `fast = 30 s` (→100), `slow = 180 s` (→0).
- **Utilization:** `s_util = clamp(utilization_pct)` — already 0–100.

Weights (defaults **calls 40 / on-time 30 / speed 20 / utilization 10**), normalized to sum 1:
`score = Σ (wᵢ / Σw) · sᵢ`.

**Eligibility gate:** if `calls < score_min_calls` (default 8) → `daily_score = None` (the day earns no score and is ineligible for GOAT/score awards).

Module surface (pure, no DB/template):
- `ScoreConfig` dataclass: `weights: dict[str,float]`, `target_calls`, `ontime_floor`, `fast_secs`, `slow_secs`, `min_calls`. `DEFAULT_SCORE_CONFIG` holds the defaults above.
- `daily_score(row, cfg) -> ScoreBreakdown | None` where `ScoreBreakdown = {score: float, components: {key: {sub: float, points: float}}}`; `None` below the gate.
- All numbers float; callers round for display (`round`, banker's, matching the existing JS-preview convention).

## 4. Award taxonomy (mirrors production)

Production awards derive live from `production_daily` + an `award_overrides` layer (`awards.py`). Forklift mirrors this with the work-center/group dimension dropped — all drivers compete in one pool — and the "best day" ranked by the **GOAT score** rather than raw units.

| Forklift award | Production analogue | Definition | Icon |
|---|---|---|---|
| **Forklift GOAT** | `award_goat` | Highest single-day **GOAT score**, all-time (earliest day wins ties, then name) | 🐐 |
| **Annual — top days** | `trophy_top_day` | Top-3 single-day **GOAT scores** in the year | 🏆 |
| **Annual — best on-time** | `trophy_best_avg_*` | Highest on-time % in the year, ≥ min calls | 🏆 |
| **Annual — fastest** | (new sibling) | Lowest avg response in the year, ≥ min calls | 🏆 |
| **Monthly ribbons** | `badge` | Top-3 single-day **GOAT scores** in the month | 🥇🥈🥉 |

New `award_overrides` scopes: `forklift_goat`, `forklift_top_day`, `forklift_best_ontime`, `forklift_fastest`, `forklift_badge`. Same replace/delete/reset actions and the same `POST /api/awards/override` handler (extended to accept these scopes). Derived at render with a 5-minute in-process TTL cache keyed on `(scope, year, month, ScoreConfig fingerprint)` so a settings change naturally uses a fresh entry.

## 5. Data model — enrich `forklift_driver_daily`

The table already has the needed columns (`on_time`, `late`, `utilization_pct`, `on_call_ms`, `available_ms`); Stage 1 writes them as 0 because the per-call completions feed lacks them. **No schema migration for the fact table.** Populate them from the dashboard endpoint, which exposes per-driver `onTime / late / totalOnCallMs / availableMs / utilizationPct`.

Source of truth: `GET /api/dashboard?since=<ms>` returns per-driver **cumulative** counts from `since` to now (verified: `since=0` → 37,540 calls / 10 drivers vs. default "today" → 220). Two write paths:

- **Forward capture** (steady state): the existing `_tick_forklift` warmer, after its `snapshot_today`, also calls `fetch_dashboard(since=startOfToday)` and upserts each driver's `on_time/late/on_call_ms/available_ms/utilization_pct` into today's `forklift_driver_daily` row. `calls/avg_ms/max_ms` stay from the completions snapshot (don't overwrite). Join dashboard rows → `driver_id` via the `/api/drivers` list (name→id), falling back to name.
- **Historical reconstruction** (one-time): for each day D, `onTime(D) = cum(since=startOfD) − cum(since=startOfD+1)`; same for `late`, `totalOnCallMs`, `availableMs`. `utilization_pct(D)` recomputed from the differenced on-call/available. Fill only those columns; leave `calls/avg_ms/max_ms`. ~90 dashboard calls, idempotent, clamps negatives at 0.

`avg_ms` (speed) and `calls` are unchanged — calls + speed leaderboards/trophies work the moment this ships; on-time, utilization, and therefore the full GOAT score fill in via reconstruction.

## 6. Compute module: `forklift_awards.py` (mirrors `awards.py`)

Pure-ish computation over `forklift_driver_daily` + `forklift_score`; no template concerns; defensive (never raises into a request path; mirrors `forklift_advisor`'s posture); 5-min TTL cache.

- `driver_days(start, end) -> list[row]` — per-driver per-day rows (calls, on_time, late, avg_ms, utilization_pct).
- `goat(cfg) -> {name, driver_id, score, day, breakdown} | None` — max `daily_score` all-time (floor 2024-01-01), ties by earliest day then name.
- `annual_top_days(year, cfg) -> [top3 by score]`; `monthly_badges(year, month, cfg) -> [top3 by score]`.
- `annual_best_ontime(year, min_calls)`; `annual_fastest(year, min_calls)`.
- `leaderboard(start, end, cfg, min_calls) -> {most_calls, on_time, fastest, overall}`:
  - `most_calls/on_time/fastest` — ranked metric lists; rate lists filtered by `min_calls`; rows `{name, driver_id, value, calls, on_time, late}`.
  - `overall` — per driver, the **average of their eligible daily scores** in the window (driver needs ≥1 eligible day), ranked desc; rows `{name, driver_id, score, days, calls}`. (GOAT is the all-time *max* daily score; the leaderboard card is the windowed *average* — consistency rewards a sustained, not one-off, performer.)
- `awards_earned_by_driver(name, today, cfg) -> [...]` — reverse lookup for the player card (forklift scopes), parallel to `awards.awards_earned_by`.
- Override application reuses `awards.apply_overrides*` (extended to know forklift scopes).

`cfg` comes from `forklift_settings` (§7); routes pass the resolved `ScoreConfig`.

**Identity join:** rankings use the forklift `name` directly. Player-card block and trophy→player-card links resolve a forklift driver to a plant person via `forklift_name_map` (kind=`'driver'`) when names differ, else a direct name match. Forklift-only drivers still appear on the leaderboard/trophy case, just without a player-card link.

## 7. Settings — extend `forklift_settings` (GOAT Score panel)

Reuse the redesign's nullable-override + `Resolved` pattern (`docs/.../2026-06-27-forklift-settings-redesign-design.md`). Add nullable override columns to the `forklift_settings` singleton (NULL = auto / follow the algorithm default):
- `score_w_calls`, `score_w_ontime`, `score_w_speed`, `score_w_util` (NUMERIC NULL)
- `score_target_calls` (NUMERIC NULL), `score_ontime_floor` (NUMERIC NULL)
- `score_fast_secs` (NUMERIC NULL), `score_slow_secs` (NUMERIC NULL)
- `score_min_calls` (INTEGER NULL)

Migration: guarded idempotent `ALTER TABLE forklift_settings ADD COLUMN IF NOT EXISTS …` for each, same as the redesign.

`forklift_settings.py`: `Resolved` gains `score_config() -> forklift_score.ScoreConfig` (override-or-default per field; weights default to 40/30/20/10). `algorithm_values()` already exposes the defaults for the grey ticks.

**Settings page (`/settings` → Forklift → "GOAT Score" subsection):** mirrors the existing slider-per-factor UI:
- **Four weight sliders** (Calls / On-time / Speed / Utilization), displayed as normalized %, each with the grey algorithm-default tick + ↺ reset.
- **Targets** (collapsible "advanced"): target calls/day, fast secs, slow secs, on-time floor, eligibility gate (min calls/day) — sliders/number inputs with default ticks + reset.
- **Live worked example:** a sample recent top day rendered as the score breakdown (the brainstorm tuner widget), recomputing client-side as sliders move — JS holds the same normalization formula as `forklift_score`, rounded identically.
- **Save** + **↺ reset all to algorithm**. `POST /settings/forklift` parser extended: blank/"auto" → NULL; clamp ranges; weights stored raw (normalized at compute time).

## 8. UI

### Dedicated forklift leaderboard — `GET /staffing/forklift` (new `routes/forklift_leaderboards.py`, `templates/forklift_leaderboards.html`)
- Extends `_staffing_base.html`; reuses `.lb-section` / `.lb-table` styling and the GOAT-badge macro.
- **Window selector** identical to `/staffing/leaderboards` (today / week / month / quarter / year / all-time + custom).
- **Four ranked cards:** 🏅 Overall score, 📞 Most calls, ⏱️ On-time %, ⚡ Fastest response. Top-3 medal-tinted; rate + overall cards show the "min N calls to qualify" note. Most-calls rows show the on-time/late split (green/red).
- Registered in `app.py` (`include_router`); nav link added to `_staffing_subnav.html` ("Forklift").

### Trophy case — `/trophies` (`routes/trophies.py`, `templates/trophy_case.html`)
- New **🚜 Forklift** section after the production sections: Forklift GOAT card (shows the winning day's **score** + a compact component breakdown), Annual block (top-3 score days + best on-time + fastest, existing year picker), Monthly ribbons (existing month picker). Same `.tc-card`/`.tc-group-block`/`.tc-row` structure + tier filters.
- Edit modal + `POST /api/awards/override` extended for the forklift scopes. Cache invalidation on save covers forklift caches.

### Player card — `/staffing/people/{name}` (`routes/people.py`, `templates/player_card.html`)
- A **Forklift** stat block (mirrors `pc-group-avgs`/`.stat`): calls (window), on-time %, avg response, utilization (muted), and **best-day GOAT score** with its component breakdown.
- Forklift trophies earned appear in the existing trophy-case subsection via `awards_earned_by_driver`, with 🐐/🏆/🥇 icons.
- Context (utilization, unclaimed, late reasons) below the podium metrics — numbers/lists for v1; richer charts deferred.
- Rendered only for people who map to a forklift driver; absent otherwise.

## 9. Components / boundaries
- `forklift_score.py` — pure scoring (normalization + weighted blend + gate). The only new pure module; fully unit-testable.
- `forklift_awards.py` — award + leaderboard computation over `forklift_driver_daily` + `forklift_score`.
- `forklift_store.py` — add `upsert_driver_metrics(rows)` (on-time/late/util only) for both write paths.
- `forklift_settings.py` — `Resolved.score_config()` + override fields.
- `forklift_client.fetch_dashboard(since=None)` — exists with no args today; add an optional `since` param plus query-string support to `_get` (or a small dedicated fetch).
- `forklift_backfill.py` / new `scripts/backfill_forklift_ontime.py` — one-time reconstruction; idempotent; logs outcome at WARNING.
- `app.py::_tick_forklift` — forward capture appended after `snapshot_today`.
- Routes/templates: new forklift leaderboard; trophy-case + player-card extensions; settings GOAT-Score subsection; `awards.py` override-scope awareness.

## 10. Error handling
- All forklift API reads stay best-effort: warmer/reconstruction log and swallow (Stage 1 pattern); a transient dashboard failure leaves that day's columns unchanged, never crashes the warmer.
- Render-time award/score functions are defensive: any DB/data problem yields an empty/None award (the section just doesn't render), never a 500.
- `forklift_score.daily_score` guards division (no calls → on-time 0), clamps every sub-score, and returns None below the gate; weights summing to 0 fall back to equal weights.
- Reconstruction is idempotent, re-runnable, clamps negative differences at 0.

## 11. Testing (mirrors existing award/leaderboard tests; pure tests need no DB)
- `forklift_score`: each sub-score's normalization (target/floor/fast/slow boundaries, clamping), weighted blend with default + custom weights, weight renormalization, zero-weight fallback, gate (below → None), division guards. Hand-computed expected values.
- `forklift_awards`: goat picks max score (tie → earliest/name), annual_top_days / monthly_badges by score, annual_best_ontime / fastest with min-calls gate, leaderboard four-list shape (overall = avg of eligible daily scores; gated), awards_earned_by_driver reverse lookup, override application per scope. Fixture rows.
- Reconstruction differencing: `cum(D) − cum(D+1)`, clamp-at-0, name→id join, idempotent re-run (DB-gated).
- Forward capture: dashboard rows upsert on-time/late/util without clobbering calls/avg_ms (DB-gated).
- `forklift_settings`: `score_config()` override resolution (auto→default; set→override), nullable round-trip (DB-gated).
- Routes: `/staffing/forklift` renders four cards + window selector (Jinja render, no DB); trophy-case forklift section renders; settings GOAT-Score subsection renders sliders with ticks; `POST /settings/forklift` sets/resets score overrides (303); `POST /api/awards/override` accepts a forklift scope (303). Player-card forklift block renders when mapped, absent otherwise.

## 12. Out of scope
- Increment B (people-level "dedicate X" staffing suggestions) — still parked.
- Rich context charts (utilization/unclaimed/late-reason visualizations) on the player card — numbers/lists for v1.
- Any change to the forklift app's API/auth (separate, other-app concern).
