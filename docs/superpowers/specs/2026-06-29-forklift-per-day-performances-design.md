# Forklift per-day performances on the People page

**Date:** 2026-06-29
**Status:** Approved

## Goal

For anyone who maps to a forklift driver, show a per-day list of their forklift
performances on their player card (`/staffing/people/{name}`) — newest first,
following the page's From/To date picker — where clicking a day expands a drawer
with that day's full breakdown. Mirrors the existing repair/dismantling
"Per-day breakdown" so a forklift driver's days can be browsed and clicked
through the same way.

Today a forklift driver's card shows only the 90-day **summary** (Calls /
On-time % / Avg response / Utilization / Best-day score) and the Trophy case,
plus the "No published-day production" message — there is no per-day forklift
list.

## Decisions (from brainstorming)

- **Click action:** expand the day **inline** (a drawer under the row), no reload.
- **Date range:** the per-day list follows the page's **From/To picker** (not the
  fixed 90-day summary window).
- **Days shown:** **every day with forklift activity** (calls > 0) in range,
  newest first; days below the scoring threshold still appear with score "—".

## 1. Data source — compute on the fly, no new table

Daily metrics already live in `forklift_driver_daily` (calls, on_time, late,
avg_ms, max_ms, utilization_pct). `forklift_score.daily_score(row, cfg)` already
turns one row into a composite score + four subscores (calls / ontime / speed /
util), returning `None` below the min-calls gate. So no new table and no
"publish" step: read the existing rows for the page's date range and score each.

Alternative considered — persist a daily-score table like `production_daily`.
Rejected as YAGNI: the metrics are already persisted and `daily_score` is a cheap
pure function.

## 2. Route helper — `routes/people.py`

- Extract the name→driver resolution currently inline in `_forklift_for_person`
  into `_resolve_forklift_name(name) -> str | None` and reuse it (no duplicated
  reverse name-map logic).
- Add `_forklift_days_for_person(forklift_name, start_d, end_d, cfg) -> list[dict]`:
  - calls `forklift_store.driver_days_between(start_d, end_d)`,
  - keeps rows for this driver **with calls > 0**,
  - sorts newest-first,
  - builds one dict per day:
    `date, calls, on_time, late, ontime_pct, avg_ms, max_ms, utilization_pct,
    score (float | None), components ({calls, ontime, speed, util} sub-values | None)`.
  - `score` / `components` come straight from `daily_score` → `None` for sub-gate
    days (still listed).
  - Defensive: any store/compute failure → empty list, never 500 (same posture as
    `_forklift_for_person`).
- In `staffing_player_card`, after the existing `forklift` block, compute
  `forklift_days` over the page's `start_d`/`end_d` (the picker range, **not** the
  90-day summary window) and pass it to the template. Only computed when the
  person maps to a forklift driver. Rides the existing per-person+range response
  cache — no cache changes.

## 3. Template — `player_card.html`

New section **"🚜 Forklift — per-day performances"**, placed right after the
existing Forklift stats block (keeps forklift content together). Table columns:
**Date · Calls · On-time % · Avg response · Score**.

Each row is clickable (pointer cursor) and toggles a hidden detail row directly
beneath it. All detail is server-rendered in the initial HTML, so a tiny
vanilla-JS toggle handles expand/collapse — **no fetch, no new endpoint**.

Expanded drawer shows:
- **Raw metrics:** Calls, On-time / Late counts, On-time %, Avg response (s),
  Max response (s), Utilization %.
- **Score breakdown:** composite score + each subscore (calls / on-time / speed /
  util) on the 0–100 scale already shown on the summary card. Sub-gate days show
  the raw metrics plus "Below scoring threshold (min N calls)" instead of a score.

If the driver has no forklift days in the selected range, show a muted
"No forklift days in this range." line (parallel to "No published-day production").

## 4. Testing

- **Pure-function** test of `_forklift_days_for_person` (monkeypatch
  `driver_days_between`): correct sort order, `ontime_pct`/`avg_ms` passthrough,
  `score`/`components` populated for a scoring day and `None` for a sub-gate day,
  calls=0 rows excluded.
- **Context** test via the existing `_capture` pattern (monkeypatch
  `templates.TemplateResponse`): a mapped driver gets a populated `forklift_days`;
  a non-forklift person gets `None`/empty.
- **Template** test rendering the new block in isolation through the Jinja env
  (the `_extract_block` pattern in `test_staffing_forklift_card.py`): rows render,
  the detail drawer markup is present, a sub-gate day shows the threshold note.

## Out of scope

- No new ingestion, table, or manual "publish" action.
- No changes to the forklift leaderboard / trophy pages.
- No persisted daily-score history.
