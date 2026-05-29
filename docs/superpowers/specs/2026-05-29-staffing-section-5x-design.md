# Staffing Section: 5× Faster via Proactive Cache Warming

**Date:** 2026-05-29
**Status:** Draft — pending user review
**Goal:** Make every page under the staffing section (`/staffing` day-view, `/staffing/leaderboards`, `/staffing/skills`, `/staffing/people/<name>`) feel ~5× faster, targeting the specific symptom the user reports: **slow on first load, snappy afterward.**

## Problem

The staffing section has already had two performance passes (the "3× pass" of 2026-05-01 and the precompute/warm-cache effort of 2026-05-11). External-API calls are out of the request path, DB reads are parallelized, historical pages read from the `production_daily` fact table, and the day-view has a full HTTP response cache plus a `Server-Timing` header. A changelog entry put GET `/staffing` at **~1.9s median on a cache miss, `<1ms` on a cache hit.**

Yet the user reports the **entire** section is slow, with the symptom **"slow first load, then snappy."** That is the textbook signature of a **cold-cache problem**, not a render-bound or browser problem:

- **Cache hit** → `<1ms` (serves stored bytes)
- **Cache miss / cold** → ~1.9s (full data + 68 KB template render)

The first load eats the cold render; everything after is the warm path — until the cache expires (15s for today) or a Railway deploy wipes the process and the next "first load" is cold again. Because `main` is deployed frequently, cold starts are common.

Two structural gaps make it worse. Response-cache coverage across the section is uneven:

| Page | Route | Response cache? | Template size |
|---|---|---|---|
| Day-view | `routes/staffing.py` | ✅ yes (15s today / 5min past) | 68 KB |
| Leaderboards | `routes/leaderboards.py` | ✅ yes | 27 KB |
| **Skills matrix** | `routes/skills.py` | ❌ **none** | **40 KB** |
| **Player cards** | `routes/people.py` | ❌ **none** | 12 KB |

1. **Skills matrix and player cards have no response cache at all** — they re-render from scratch on *every* load, not just cold ones.
2. **The day-view's cache is lazy** — it's populated by the first unlucky human who loads the page, rather than proactively. With a 15s TTL, most navigations miss it.

The shared base template (`_staffing_base.html`, 3.8 KB) is *not* the bottleneck — each page's own render is.

## Strategy: warm-first, tiered, measured

Make the first load never cold by converting the response cache from **lazy** (populated by the first human) to **proactive** (kept warm by a background loop). The infrastructure already exists:

- `_http_cache.py` exposes `get_cached_response` / `store_cached_response` / `invalidate_today_cache`, backed by two `TTLCache`s (`_RESPONSE_CACHE_TODAY`, 15s; `_RESPONSE_CACHE_PAST`, 300s).
- The day-view and leaderboards handlers **already call `store_cached_response()` themselves**, so a background task that simply invokes the handler populates the cache as a side effect — no change to render logic.
- `app.py`'s lifespan already runs **seven** background warmer loops in a uniform shape (`while True` / `try` / `asyncio.to_thread(...)` / swallow exceptions / `sleep`). The data caches these pages depend on (`load_roster`, `live_cache` attendance/time-off, `production_daily`) are already kept warm by those loops.
- Precedent exists for calling a page handler as a plain function (`share.py` calls the `/staffing` handler directly).

The unifying principle: **a human never eats a cold render.** A background loop renders today's hot pages against already-warm data and stores them in the response cache, on a cadence faster than the cache TTL — including one render at startup, so the first load after every deploy is warm.

Each tier is independently shippable and revertable, mirroring how the prior two passes were structured. We take a cold-load `Server-Timing` reading before starting and after each tier, and **stop as soon as we hit 5×.**

## Tiers

### Tier 1 — Warm the pages that already cache (the big win)

- Add `_warm_staffing_pages_loop` to `app.py`'s lifespan, following the existing warmer shape. On boot and every ~45s thereafter, it renders **today's day-view** and the **default leaderboards** by calling their handlers (in `asyncio.to_thread`, with explicit default params so it doesn't choke on FastAPI `Query` objects — see the `share.py` precedent: pass concrete `publish_blocked=0, view="draft"` etc.). The handlers' existing `store_cached_response()` populates the cache.
- **Align the TTL to the cadence.** `_RESPONSE_CACHE_TODAY`'s 15s TTL would go cold between 45s ticks. Raise it to 60s — the warmer re-renders every 45s, so a 60s TTL keeps a comfortable margin and never goes cold between ticks. Mutations already call `invalidate_today_cache()`, so saves still appear instantly.
- The loop renders on its first iteration *before* the first `sleep`, so the first human load after a Railway deploy is already warm.
- **Outcome:** first load — including post-deploy — hits the warm `<1ms` path instead of ~1.9s. For the reported symptom this is well past 5×.

### Tier 2 — Keep shared data warm for the long tail

- Raise `load_roster()`'s in-process TTL (`staffing._ROSTER_CACHE_TTL_SECONDS`) from 60s to something long (e.g., 1 hour). It already invalidates on `save_roster()` and Odoo sync, so a short TTL buys nothing but cold misses. This makes *cold* long-tail renders — an old day, an unusual leaderboard range, a specific player card — faster even when they aren't pre-rendered.
- Confirm (no code change expected) that `live_cache` attendance/time-off and `production_daily` stay warm via the existing `_warm_live_cache_loop` (45s) and nightly precompute.

### Tier 3 — Close the two gaps

- **Skills matrix** (`routes/skills.py`): add a response cache around the GET `/staffing/skills` render, reusing the `_http_cache` helpers. Its data (roster + skill levels) is stable, so it's an ideal cache; invalidate it from the roster/skill save paths (alongside the existing `_invalidate_roster_cache()` / `invalidate_today_cache()` calls). Warm it in the Tier 1 loop too. This is the page most likely slow on *every* load today.
- **Player cards** (`routes/people.py`): add a response cache keyed per-person. Do **not** pre-render all ~100 cards in the warmer — rely on cache-on-first-view plus the warm roster from Tier 2, so the second view of any person is instant and the first is cheaper. Invalidate on roster/skill/attribution writes that change a card.

### Approach B fallback (only if measurement demands it)

If, after Tiers 1–3, a cold-load `Server-Timing` reading still shows a gap to 5× on some path, attack the render cost directly: shrink the 68 KB / 40 KB templates, precompute view-models, reduce per-cell work. This is deferred and *conditional* — no speculative template surgery.

## Measurement & stop criteria

- Baseline: capture timing on a deliberately cold load of each page before any change. The `Server-Timing` header (`db`, `stratustime`, `render`, `total`) currently exists only on the day-view route (`routes/staffing.py`); for leaderboards/skills/player-cards, use the browser Network tab's total response time, or add the same `_Phase` instrumentation if per-phase numbers are needed there.
- After each tier, re-measure the same way (cold load, then a warm reload to confirm the cache hit).
- **Done = 5× on the reported (first-load) symptom.** Tier 1 alone very likely clears it; Tiers 2–3 extend the win to the long tail and the two uncached pages. Approach B only if a measured gap remains.

## Testing & rollback

- **Warmer safety:** `_warm_staffing_pages_loop` swallows all exceptions like the other seven loops — it can never crash the app, even during a StratusTime/Odoo outage.
- **Correctness preserved:** mutation → `invalidate_today_cache()` (and the new skills/player-card invalidations) are preserved, so nothing is stale after a save.
- **Tests:** assert the warmed handlers still return 200 and that a cache entry is populated after a warm tick; assert the new skills/player-card caches are invalidated by their respective write paths; existing suite must still pass.
- **Rollback:** Tier 1 rolls back by deleting the loop and reverting one TTL constant. Each later tier reverts in isolation.

## Behavior change to accept

Raising today's response-cache TTL from 15s → 60s means on-screen data for *today* can lag reality by up to ~60s. But saves still bust the cache instantly via `invalidate_today_cache()`, and the underlying `live_cache` only refreshes every 45s anyway — so real-world freshness is effectively unchanged. (Approved by the user during brainstorming.)

## File touch map

- **Modify:** `src/zira_dashboard/app.py` — add `_warm_staffing_pages_loop` to lifespan.
- **Modify:** `src/zira_dashboard/_http_cache.py` — raise `_RESPONSE_CACHE_TODAY` TTL to 60s.
- **Modify:** `src/zira_dashboard/staffing.py` — raise `_ROSTER_CACHE_TTL_SECONDS`.
- **Modify:** `src/zira_dashboard/routes/skills.py` — add response cache to GET `/staffing/skills` + invalidation hooks.
- **Modify:** `src/zira_dashboard/routes/people.py` — add per-person response cache + invalidation hooks.
- **Modify:** `CHANGELOG.md` — an entry per deploy.

No DB schema changes. No new dependencies. No env-var changes.
