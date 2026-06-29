# Forklift Recognition — Leaderboards, Trophies & GOAT Score Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface forklift-driver performance as native recognition — a dedicated leaderboard, trophies in the shared trophy case, and a player-card block — with the GOAT chosen by a tunable weighted 0–100 composite score.

**Architecture:** A pure scoring module (`forklift_score`) blends four normalized components into a daily 0–100 score. A pure award module (`forklift_awards`) ranks days/drivers over `forklift_driver_daily` (mirroring `awards.py`). The fact table's already-present on-time/utilization columns are filled forward (warmer) and backward (one-time reconstruction differencing the dashboard's cumulative counts). Settings reuse the existing nullable-override + `Resolved` pattern. UI mirrors the production leaderboards/trophy-case/player-card templates.

**Tech Stack:** FastAPI + Jinja2 + htmx, raw psycopg2 (ThreadedConnectionPool), Postgres on Railway. Tests: pytest (`ZIRA_API_KEY=test .venv/bin/python -m pytest`); DB-gated tests skip without `DATABASE_URL`. Lint: ruff (`F` rules).

**Spec:** `docs/superpowers/specs/2026-06-29-forklift-recognition-design.md`

**Conventions for every task:** run tests with `ZIRA_API_KEY=test .venv/bin/python -m pytest <path> -v`; DB-gated tests are decorated `@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DB")` like the existing forklift store tests. Commit after each task passes.

---

## Phase A — Data plumbing (fill on-time / utilization into the fact table)

### Task 1: `fetch_dashboard(since=...)` + query-string support in the client

**Files:**
- Modify: `src/zira_dashboard/forklift_client.py` (`_get`, `fetch_dashboard`)
- Test: `tests/test_forklift_client.py`

- [ ] **Step 1: Write the failing test**

```python
def test_fetch_dashboard_passes_since_param(monkeypatch):
    monkeypatch.setenv("FORKLIFT_BASE_URL", "https://fk.example")
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _json_response({"driverLeaderboard": []})

    monkeypatch.setattr(forklift_client.requests, "get", fake_get)

    forklift_client.fetch_dashboard(since=0)

    assert captured["url"] == "https://fk.example/api/dashboard"
    assert captured["params"] == {"since": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_client.py::test_fetch_dashboard_passes_since_param -v`
Expected: FAIL (`fetch_dashboard() takes no arguments`)

- [ ] **Step 3: Implement**

In `forklift_client.py`, give `_get` an optional `params` and thread it through `requests.get`, then add `since` to `fetch_dashboard`:

```python
def _get(path: str, params: dict | None = None) -> Any:
    base = os.getenv("FORKLIFT_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    key = os.getenv("FORKLIFT_API_KEY", "")
    headers = {"X-API-Key": key} if key else {}
    try:
        resp = requests.get(f"{base}{path}", headers=headers, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 — surfaced as ForkliftError
        raise ForkliftError(str(exc)) from exc


def fetch_dashboard(since: int | None = None) -> dict:
    params = {"since": since} if since is not None else None
    return _get("/api/dashboard", params=params)
```

(Keep the existing `_TIMEOUT`/headers exactly as they are; only add the `params` plumbing.)

- [ ] **Step 4: Run tests**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_client.py -v`
Expected: PASS (existing client tests still green)

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/forklift_client.py tests/test_forklift_client.py
git commit -m "feat(forklift): fetch_dashboard accepts since= for cumulative history"
```

---

### Task 2: `forklift_store.upsert_driver_metrics` (fill on-time/util only)

**Files:**
- Modify: `src/zira_dashboard/forklift_store.py`
- Test: `tests/test_forklift_store.py`

- [ ] **Step 1: Write the failing test (DB-gated)**

```python
import os
import datetime as dt
import pytest
from zira_dashboard import forklift_store

DBGATE = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DB")


@DBGATE
def test_upsert_driver_metrics_fills_ontime_without_clobbering_calls():
    day = dt.date(2026, 4, 1)
    forklift_store.upsert_driver_daily([
        {"day": day, "driver_id": "d1", "name": "Trent", "calls": 20,
         "on_time": 0, "late": 0, "avg_ms": 50000, "max_ms": 90000,
         "utilization_pct": 0, "on_call_ms": 600000, "available_ms": 0},
    ])
    forklift_store.upsert_driver_metrics([
        {"day": day, "driver_id": "d1", "on_time": 18, "late": 2,
         "on_call_ms": 700000, "available_ms": 3600000, "utilization_pct": 19.4},
    ])
    rows = forklift_store.driver_rows_for_day(day)
    row = next(r for r in rows if r["driver_id"] == "d1")
    assert row["calls"] == 20          # untouched
    assert row["avg_ms"] == 50000      # untouched
    assert row["on_time"] == 18
    assert row["late"] == 2
    assert round(float(row["utilization_pct"]), 1) == 19.4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test DATABASE_URL=$DATABASE_URL .venv/bin/python -m pytest tests/test_forklift_store.py::test_upsert_driver_metrics_fills_ontime_without_clobbering_calls -v`
Expected: FAIL (`module has no attribute 'upsert_driver_metrics'`) — or SKIP if no DB locally (then verify logic by reading; CI has DB).

- [ ] **Step 3: Implement**

Add to `forklift_store.py` (mirror the existing `upsert_driver_daily` connection/cursor pattern):

```python
def upsert_driver_metrics(rows: list[dict]) -> int:
    """Fill on-time/late/utilization columns for existing driver-day rows
    without touching calls/avg_ms/max_ms. Rows missing in the table are
    inserted with calls=0 (reconstruction may run before the snapshot)."""
    if not rows:
        return 0
    with db.cursor() as cur:
        for r in rows:
            cur.execute(
                """
                INSERT INTO forklift_driver_daily
                    (day, driver_id, name, calls, on_time, late,
                     avg_ms, max_ms, utilization_pct, on_call_ms, available_ms)
                VALUES (%(day)s, %(driver_id)s, %(name)s, 0, %(on_time)s, %(late)s,
                        0, 0, %(utilization_pct)s, %(on_call_ms)s, %(available_ms)s)
                ON CONFLICT (day, driver_id) DO UPDATE SET
                    on_time = EXCLUDED.on_time,
                    late = EXCLUDED.late,
                    utilization_pct = EXCLUDED.utilization_pct,
                    on_call_ms = EXCLUDED.on_call_ms,
                    available_ms = EXCLUDED.available_ms,
                    computed_at = now()
                """,
                {"name": r.get("name", r["driver_id"]), **r},
            )
    return len(rows)


def driver_rows_for_day(day) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM forklift_driver_daily WHERE day = %s", (day,)
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
```

(If `driver_rows_for_day` already exists, reuse it and drop the duplicate. Match the module's actual `db` import name.)

- [ ] **Step 4: Run tests**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_store.py -v`
Expected: PASS or SKIP (DB-gated). Ensure import + ruff clean: `.venv/bin/ruff check src/zira_dashboard/forklift_store.py`

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/forklift_store.py tests/test_forklift_store.py
git commit -m "feat(forklift): upsert_driver_metrics fills on-time/util columns"
```

---

### Task 3: Map a dashboard payload → driver-metric rows (pure) + warmer forward capture

**Files:**
- Modify: `src/zira_dashboard/forklift_ingest.py` (new pure `driver_metrics_from_dashboard`)
- Modify: `src/zira_dashboard/app.py` (`_tick_forklift`)
- Test: `tests/test_forklift_ingest.py`

- [ ] **Step 1: Write the failing test (pure)**

```python
from zira_dashboard import forklift_ingest


def test_driver_metrics_from_dashboard_maps_names_to_ids():
    dashboard = {"driverLeaderboard": [
        {"name": "Trent", "onTime": 18, "late": 2,
         "totalOnCallMs": 700000, "availableMs": 3600000, "utilizationPct": 19.4},
        {"name": "Ghost", "onTime": 5, "late": 0,
         "totalOnCallMs": 1000, "availableMs": 2000, "utilizationPct": 50.0},
    ]}
    id_to_name = {"d1": "Trent"}
    rows = forklift_ingest.driver_metrics_from_dashboard(dashboard, id_to_name)
    trent = next(r for r in rows if r["name"] == "Trent")
    assert trent["driver_id"] == "d1"      # resolved via name->id
    assert trent["on_time"] == 18 and trent["late"] == 2
    assert trent["on_call_ms"] == 700000 and trent["available_ms"] == 3600000
    ghost = next(r for r in rows if r["name"] == "Ghost")
    assert ghost["driver_id"] == "Ghost"   # fallback to name when unmapped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_ingest.py::test_driver_metrics_from_dashboard_maps_names_to_ids -v`
Expected: FAIL (`no attribute 'driver_metrics_from_dashboard'`)

- [ ] **Step 3: Implement the pure mapper**

Add to `forklift_ingest.py`:

```python
def driver_metrics_from_dashboard(dashboard: dict, id_to_name: dict[str, str]) -> list[dict]:
    """Extract per-driver on-time/late/utilization rows from a /api/dashboard
    payload. Resolves driver_id by reversing id_to_name on the display name;
    falls back to the name itself when unmapped."""
    name_to_id = {v: k for k, v in (id_to_name or {}).items()}
    out = []
    for d in dashboard.get("driverLeaderboard", []) or []:
        name = str(d.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "driver_id": name_to_id.get(name, name),
            "name": name,
            "on_time": int(d.get("onTime") or 0),
            "late": int(d.get("late") or 0),
            "on_call_ms": int(d.get("totalOnCallMs") or 0),
            "available_ms": int(d.get("availableMs") or 0),
            "utilization_pct": float(d.get("utilizationPct") or 0),
        })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_ingest.py -v`
Expected: PASS

- [ ] **Step 5: Wire forward capture into the warmer**

In `app.py::_tick_forklift`, after the existing `snapshot_today(...)` call (steady-state branch), append (guarded, best-effort — never raise):

```python
        try:
            from zira_dashboard import forklift_client, forklift_ingest, forklift_store
            from zira_dashboard.forklift_snapshot import _site_today  # or the helper already used
            today = _site_today()
            start_ms = int(datetime.combine(today, time.min, SITE_TZ).timestamp() * 1000)
            dash = forklift_client.fetch_dashboard(since=start_ms)
            id_to_name = forklift_store.name_map("driver") or {
                d["id"]: d["name"] for d in forklift_client.fetch_drivers()
            }
            metric_rows = forklift_ingest.driver_metrics_from_dashboard(dash, id_to_name)
            for r in metric_rows:
                r["day"] = today
            n = forklift_store.upsert_driver_metrics(metric_rows)
            _log.warning("forklift warmer: captured on-time metrics -> %d drivers", n)
        except Exception as exc:  # noqa: BLE001
            _log.warning("forklift warmer: on-time capture failed: %s", exc)
```

(Match the exact imports/timezone helper names already used in `_tick_forklift`/`forklift_snapshot`. The `id_to_name` reversal: `name_map("driver")` returns forklift_name→plant_name, NOT id→name — so build id→name from `fetch_drivers()`; only fall back to name_map if you actually store ids there. Read the existing snapshot code to use the same id→name source it uses.)

- [ ] **Step 6: Run the suite + ruff**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_ingest.py -v && .venv/bin/ruff check src/zira_dashboard/app.py src/zira_dashboard/forklift_ingest.py`
Expected: PASS, ruff clean

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/forklift_ingest.py src/zira_dashboard/app.py tests/test_forklift_ingest.py
git commit -m "feat(forklift): warmer captures daily on-time/util from dashboard"
```

---

### Task 4: One-time historical reconstruction (difference cumulative dashboards)

**Files:**
- Modify: `src/zira_dashboard/forklift_backfill.py` (add `reconstruct_ontime_history`)
- Create: `scripts/backfill_forklift_ontime.py`
- Test: `tests/test_forklift_backfill.py`

- [ ] **Step 1: Write the failing test (pure differencing helper)**

```python
from zira_dashboard import forklift_backfill


def test_diff_cumulative_days_clamps_and_subtracts():
    # cum_by_day[d] = {driver_id: {"on_time":.., "late":.., "on_call_ms":.., "available_ms":..}}
    cum = {
        "2026-04-01": {"d1": {"on_time": 100, "late": 10, "on_call_ms": 5000, "available_ms": 9000}},
        "2026-04-02": {"d1": {"on_time": 82,  "late": 8,  "on_call_ms": 4300, "available_ms": 7000}},
    }
    # day 2026-04-01 = cum(04-01) - cum(04-02)
    rows = forklift_backfill.diff_day("2026-04-01", "2026-04-02", cum)
    r = rows[0]
    assert r["on_time"] == 18 and r["late"] == 2
    assert r["on_call_ms"] == 700 and r["available_ms"] == 2000
    assert round(r["utilization_pct"], 1) == 35.0  # 700/2000


def test_diff_day_clamps_negative_to_zero():
    cum = {
        "2026-04-01": {"d1": {"on_time": 5, "late": 0, "on_call_ms": 0, "available_ms": 0}},
        "2026-04-02": {"d1": {"on_time": 9, "late": 0, "on_call_ms": 0, "available_ms": 0}},
    }
    rows = forklift_backfill.diff_day("2026-04-01", "2026-04-02", cum)
    assert rows[0]["on_time"] == 0  # clamp, never negative
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_backfill.py -k diff -v`
Expected: FAIL (`no attribute 'diff_day'`)

- [ ] **Step 3: Implement the pure differencing + the orchestrator**

Add to `forklift_backfill.py`:

```python
def diff_day(day_key: str, next_key: str, cum: dict) -> list[dict]:
    """Per-driver metrics for `day_key` = cumulative(day_key) - cumulative(next_key).
    Cumulative counts run from `since` to now, so the older day's cumulative minus
    the next day's cumulative isolates that single day. Clamps negatives at 0."""
    today_c = cum.get(day_key, {})
    next_c = cum.get(next_key, {})
    rows = []
    for did, t in today_c.items():
        n = next_c.get(did, {})
        on_time = max(0, int(t.get("on_time", 0)) - int(n.get("on_time", 0)))
        late = max(0, int(t.get("late", 0)) - int(n.get("late", 0)))
        on_call = max(0, int(t.get("on_call_ms", 0)) - int(n.get("on_call_ms", 0)))
        avail = max(0, int(t.get("available_ms", 0)) - int(n.get("available_ms", 0)))
        util = round(on_call / avail * 100, 2) if avail else 0.0
        rows.append({"driver_id": did, "on_time": on_time, "late": late,
                     "on_call_ms": on_call, "available_ms": avail,
                     "utilization_pct": util})
    return rows


def reconstruct_ontime_history(client=None, days_back: int = 120) -> dict:
    """Fetch one cumulative dashboard per day boundary, difference consecutive
    days, and upsert per-day on-time/util into forklift_driver_daily. Idempotent;
    best-effort (logs + swallows). Returns a small outcome dict."""
    import datetime as dt
    from zira_dashboard import forklift_client, forklift_ingest, forklift_store
    try:
        from zira_dashboard.forklift_ingest import SITE_TZ  # tz used by aggregate_completions
    except Exception:  # noqa: BLE001
        from zoneinfo import ZoneInfo
        SITE_TZ = ZoneInfo("America/Chicago")
    client = client or forklift_client

    today = dt.datetime.now(SITE_TZ).date()
    days = [today - dt.timedelta(days=i) for i in range(days_back, -1, -1)]
    boundaries = days + [today + dt.timedelta(days=1)]  # need day+1 for the newest diff

    id_to_name = {d["id"]: d["name"] for d in client.fetch_drivers()}
    cum: dict = {}
    for d in boundaries:
        try:
            ms = int(dt.datetime.combine(d, dt.time.min, tzinfo=SITE_TZ).timestamp() * 1000)
            dash = client.fetch_dashboard(since=ms)
            rows = forklift_ingest.driver_metrics_from_dashboard(dash, id_to_name)
            cum[d.isoformat()] = {r["driver_id"]: r for r in rows}
        except Exception as exc:  # noqa: BLE001
            _log.warning("forklift reconstruct: fetch failed for %s: %s", d, exc)

    total = 0
    for d in days:
        day_rows = diff_day(d.isoformat(), (d + dt.timedelta(days=1)).isoformat(), cum)
        for r in day_rows:
            r["day"] = d
        try:
            total += forklift_store.upsert_driver_metrics(day_rows)
        except Exception as exc:  # noqa: BLE001
            _log.warning("forklift reconstruct: upsert failed for %s: %s", d, exc)

    out = {"days": len(days), "rows": total}
    _log.warning("forklift reconstruct on-time history -> %s", out)
    return out
```

(Add `import logging` + `_log = logging.getLogger(__name__)` to `forklift_backfill.py` if not already present. `cum` is keyed by `day.isoformat()` to match `diff_day`'s string keys.) Build the runnable script:

```python
# scripts/backfill_forklift_ontime.py
"""One-time: reconstruct per-day forklift on-time/utilization history."""
import logging
from zira_dashboard import forklift_backfill

logging.basicConfig(level=logging.INFO)
print(forklift_backfill.reconstruct_ontime_history())
```

- [ ] **Step 4: Run tests**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_backfill.py -v && .venv/bin/ruff check src/zira_dashboard/forklift_backfill.py scripts/backfill_forklift_ontime.py`
Expected: PASS, ruff clean

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/forklift_backfill.py scripts/backfill_forklift_ontime.py tests/test_forklift_backfill.py
git commit -m "feat(forklift): reconstruct on-time/util history by differencing dashboards"
```

---

## Phase B — Scoring + awards (the core)

### Task 5: `forklift_score.py` — composite GOAT score (pure)

**Files:**
- Create: `src/zira_dashboard/forklift_score.py`
- Test: `tests/test_forklift_score.py`

- [ ] **Step 1: Write the failing tests**

```python
import math
import pytest
from zira_dashboard import forklift_score as fs


def _row(calls, on_time, late, avg_ms, util):
    return {"calls": calls, "on_time": on_time, "late": late,
            "avg_ms": avg_ms, "utilization_pct": util}


def test_subscores_hit_targets():
    cfg = fs.DEFAULT_SCORE_CONFIG
    # 25 calls -> calls sub = 100; 30s avg -> speed = 100; 100% on-time -> 100; util passthrough
    b = fs.daily_score(_row(25, 100, 0, 30000, 80), cfg)
    c = b.components
    assert round(c["calls"]["sub"]) == 100
    assert round(c["speed"]["sub"]) == 100
    assert round(c["ontime"]["sub"]) == 100
    assert round(c["util"]["sub"]) == 80


def test_calls_subscore_caps_at_100():
    b = fs.daily_score(_row(50, 50, 0, 60000, 50), fs.DEFAULT_SCORE_CONFIG)
    assert b.components["calls"]["sub"] == 100  # 50/25 capped


def test_speed_floor_and_ceiling():
    cfg = fs.DEFAULT_SCORE_CONFIG
    fast = fs.daily_score(_row(20, 20, 0, 30000, 50), cfg).components["speed"]["sub"]
    slow = fs.daily_score(_row(20, 20, 0, 180000, 50), cfg).components["speed"]["sub"]
    assert round(fast) == 100 and round(slow) == 0


def test_ontime_floor_spreads_range():
    cfg = fs.DEFAULT_SCORE_CONFIG  # floor 80
    # 90% on-time -> (90-80)/(100-80)*100 = 50
    b = fs.daily_score(_row(20, 18, 2, 60000, 50), cfg)
    assert round(b.components["ontime"]["sub"]) == 50


def test_gate_returns_none_below_min_calls():
    assert fs.daily_score(_row(7, 7, 0, 30000, 100), fs.DEFAULT_SCORE_CONFIG) is None


def test_weighted_total_matches_hand_calc():
    cfg = fs.DEFAULT_SCORE_CONFIG  # 40/30/20/10
    # subs: calls 100, ontime (97-80)/20*100=85, speed (180-40)/150*100=93.33, util 22
    b = fs.daily_score(_row(31, 97, 3, 40000, 22), cfg)
    expected = 0.4*100 + 0.3*85 + 0.2*(140/150*100) + 0.1*22
    assert math.isclose(b.score, expected, rel_tol=1e-6)


def test_zero_weights_fall_back_to_equal():
    cfg = fs.ScoreConfig(weights={"calls": 0, "ontime": 0, "speed": 0, "util": 0})
    b = fs.daily_score(_row(25, 100, 0, 30000, 100), cfg)
    assert round(b.score) == 100  # equal weights, all subs 100


def test_no_calls_ontime_is_zero_not_crash():
    # gate is 8; use exactly min with on_time+late=0 guard via direct subscore call
    b = fs.daily_score(_row(8, 0, 0, 60000, 0), fs.DEFAULT_SCORE_CONFIG)
    assert b.components["ontime"]["sub"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_score.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement**

```python
# src/zira_dashboard/forklift_score.py
"""Pure composite forklift GOAT score: a weighted 0-100 blend of four
absolute-target-normalized components. No DB, no templates."""
from __future__ import annotations

from dataclasses import dataclass, field

_DEFAULT_WEIGHTS = {"calls": 40.0, "ontime": 30.0, "speed": 20.0, "util": 10.0}


@dataclass(frozen=True)
class ScoreConfig:
    weights: dict = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))
    target_calls: float = 25.0
    ontime_floor: float = 80.0
    fast_secs: float = 30.0
    slow_secs: float = 180.0
    min_calls: int = 8


DEFAULT_SCORE_CONFIG = ScoreConfig()


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    components: dict  # {key: {"sub": float, "points": float}}


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _subscores(row: dict, cfg: ScoreConfig) -> dict:
    calls = float(row.get("calls") or 0)
    on_time = float(row.get("on_time") or 0)
    late = float(row.get("late") or 0)
    avg_secs = float(row.get("avg_ms") or 0) / 1000.0
    util = float(row.get("utilization_pct") or 0)

    s_calls = _clamp(calls / cfg.target_calls * 100) if cfg.target_calls else 0.0
    denom = on_time + late
    pct = (on_time / denom * 100) if denom else 0.0
    spread = (100.0 - cfg.ontime_floor) or 1.0
    s_ontime = _clamp((pct - cfg.ontime_floor) / spread * 100)
    span = (cfg.slow_secs - cfg.fast_secs) or 1.0
    s_speed = _clamp((cfg.slow_secs - avg_secs) / span * 100)
    s_util = _clamp(util)
    return {"calls": s_calls, "ontime": s_ontime, "speed": s_speed, "util": s_util}


def daily_score(row: dict, cfg: ScoreConfig = DEFAULT_SCORE_CONFIG) -> ScoreBreakdown | None:
    if float(row.get("calls") or 0) < cfg.min_calls:
        return None
    subs = _subscores(row, cfg)
    w = {k: float(cfg.weights.get(k, 0) or 0) for k in subs}
    total_w = sum(w.values())
    if total_w <= 0:  # zero weights -> equal weighting
        w = {k: 1.0 for k in subs}
        total_w = float(len(subs))
    components, score = {}, 0.0
    for k, sub in subs.items():
        pts = w[k] / total_w * sub
        components[k] = {"sub": sub, "points": pts}
        score += pts
    return ScoreBreakdown(score=score, components=components)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_score.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/forklift_score.py tests/test_forklift_score.py
git commit -m "feat(forklift): composite GOAT score module (pure, tunable weights)"
```

---

### Task 6: `forklift_awards.py` — per-day awards (goat / annual / monthly / specialists)

**Files:**
- Create: `src/zira_dashboard/forklift_awards.py`
- Test: `tests/test_forklift_awards.py`

- [ ] **Step 1: Write the failing tests (inject fixture rows via a seam)**

```python
import datetime as dt
import pytest
from zira_dashboard import forklift_awards as fa
from zira_dashboard import forklift_score as fs


def _row(day, did, name, calls, on_time, late, avg_ms, util):
    return {"day": day, "driver_id": did, "name": name, "calls": calls,
            "on_time": on_time, "late": late, "avg_ms": avg_ms,
            "max_ms": avg_ms, "utilization_pct": util}


@pytest.fixture
def rows(monkeypatch):
    data = [
        _row(dt.date(2026, 4, 14), "d1", "Trent", 31, 30, 1, 40000, 22),  # big day, high score
        _row(dt.date(2026, 4, 15), "d1", "Trent", 10, 9, 1, 90000, 15),
        _row(dt.date(2026, 4, 14), "d2", "Isidro", 29, 29, 0, 50000, 20),
        _row(dt.date(2026, 4, 16), "d3", "Juan", 5, 5, 0, 30000, 99),     # below gate (5<8)
    ]
    monkeypatch.setattr(fa, "driver_days", lambda start, end: [
        r for r in data if start <= r["day"] <= end])
    return data


def test_goat_is_highest_single_day_score(rows):
    g = fa.goat(fs.DEFAULT_SCORE_CONFIG)
    assert g["name"] == "Trent" and g["day"] == dt.date(2026, 4, 14)
    assert g["score"] > 0


def test_below_gate_day_never_wins(rows):
    g = fa.goat(fs.DEFAULT_SCORE_CONFIG)
    assert g["name"] != "Juan"  # Juan's only day is below the 8-call gate


def test_annual_top_days_sorted_by_score(rows):
    top = fa.annual_top_days(2026, fs.DEFAULT_SCORE_CONFIG)
    assert [t["name"] for t in top][:1] == ["Trent"]
    assert all(top[i]["score"] >= top[i+1]["score"] for i in range(len(top)-1))


def test_annual_fastest_respects_min_calls(rows):
    # Juan has the fastest avg (30s) but only 5 calls < min_calls -> excluded
    f = fa.annual_fastest(2026, min_calls=8)
    assert f["name"] != "Juan"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_awards.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement (mirror `awards.py` cache + structure)**

```python
# src/zira_dashboard/forklift_awards.py
"""Forklift driver awards over forklift_driver_daily, scored by forklift_score.
Mirrors awards.py: derived live, 5-minute in-process TTL cache, defensive."""
from __future__ import annotations

import datetime as dt
import time

from zira_dashboard import forklift_score as fs
from zira_dashboard import forklift_store

ALLTIME_FLOOR = dt.date(2024, 1, 1)
_CACHE: dict = {}
_TTL = 300.0


def _cache(key, fn):
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def invalidate():
    _CACHE.clear()


def _cfg_fp(cfg: fs.ScoreConfig):
    return (tuple(sorted(cfg.weights.items())), cfg.target_calls, cfg.ontime_floor,
            cfg.fast_secs, cfg.slow_secs, cfg.min_calls)


def driver_days(start: dt.date, end: dt.date) -> list[dict]:
    """Per-driver per-day rows in [start, end]. Real impl reads the store;
    tests monkeypatch this seam."""
    return forklift_store.driver_days_between(start, end)


def _scored_days(start, end, cfg):
    out = []
    for r in driver_days(start, end):
        b = fs.daily_score(r, cfg)
        if b is None:
            continue
        out.append({"name": r["name"], "driver_id": r["driver_id"],
                    "day": r["day"], "score": b.score, "calls": r["calls"],
                    "breakdown": b})
    return out


def goat(cfg: fs.ScoreConfig = fs.DEFAULT_SCORE_CONFIG):
    def _f():
        days = _scored_days(ALLTIME_FLOOR, dt.date.today(), cfg)
        if not days:
            return None
        days.sort(key=lambda d: (-d["score"], d["day"], d["name"]))
        return days[0]
    return _cache(("goat", _cfg_fp(cfg)), _f)


def annual_top_days(year: int, cfg: fs.ScoreConfig = fs.DEFAULT_SCORE_CONFIG, n: int = 3):
    def _f():
        days = _scored_days(dt.date(year, 1, 1), dt.date(year, 12, 31), cfg)
        days.sort(key=lambda d: (-d["score"], d["day"], d["name"]))
        return days[:n]
    return _cache(("annual_top", year, _cfg_fp(cfg)), _f)


def monthly_badges(year: int, month: int, cfg: fs.ScoreConfig = fs.DEFAULT_SCORE_CONFIG, n: int = 3):
    def _f():
        start = dt.date(year, month, 1)
        end = (dt.date(year + (month == 12), (month % 12) + 1, 1) - dt.timedelta(days=1))
        days = _scored_days(start, end, cfg)
        days.sort(key=lambda d: (-d["score"], d["day"], d["name"]))
        return days[:n]
    return _cache(("monthly", year, month, _cfg_fp(cfg)), _f)


def _ontime_pct(r):
    denom = (r["on_time"] or 0) + (r["late"] or 0)
    return (r["on_time"] / denom * 100) if denom else 0.0


def annual_best_ontime(year: int, min_calls: int = 50):
    def _f():
        agg = _aggregate_year(year)
        elig = [a for a in agg if a["calls"] >= min_calls]
        if not elig:
            return None
        elig.sort(key=lambda a: (-a["ontime_pct"], -a["calls"], a["name"]))
        return elig[0]
    return _cache(("best_ontime", year, min_calls), _f)


def annual_fastest(year: int, min_calls: int = 50):
    def _f():
        agg = _aggregate_year(year)
        elig = [a for a in agg if a["calls"] >= min_calls]
        if not elig:
            return None
        elig.sort(key=lambda a: (a["avg_ms"], -a["calls"], a["name"]))
        return elig[0]
    return _cache(("fastest", year, min_calls), _f)


def _aggregate_year(year: int) -> list[dict]:
    rows = driver_days(dt.date(year, 1, 1), dt.date(year, 12, 31))
    by_driver: dict = {}
    for r in rows:
        a = by_driver.setdefault(r["driver_id"], {
            "name": r["name"], "driver_id": r["driver_id"], "calls": 0,
            "on_time": 0, "late": 0, "ms_weighted": 0})
        a["calls"] += r["calls"]
        a["on_time"] += r["on_time"] or 0
        a["late"] += r["late"] or 0
        a["ms_weighted"] += (r["avg_ms"] or 0) * r["calls"]
    out = []
    for a in by_driver.values():
        a["ontime_pct"] = (a["on_time"] / (a["on_time"] + a["late"]) * 100) if (a["on_time"] + a["late"]) else 0.0
        a["avg_ms"] = (a["ms_weighted"] / a["calls"]) if a["calls"] else 0
        out.append(a)
    return out
```

Add the store reader `driver_days_between(start, end)` to `forklift_store.py` (same shape as `driver_rows_for_day` but a range, ordered by day):

```python
def driver_days_between(start, end) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM forklift_driver_daily WHERE day BETWEEN %s AND %s ORDER BY day",
            (start, end))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_awards.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/forklift_awards.py src/zira_dashboard/forklift_store.py tests/test_forklift_awards.py
git commit -m "feat(forklift): award computation (goat/annual/monthly/specialists) over scored days"
```

---

### Task 7: `forklift_awards.leaderboard` — four ranked lists incl. Overall score

**Files:**
- Modify: `src/zira_dashboard/forklift_awards.py`
- Test: `tests/test_forklift_awards.py`

- [ ] **Step 1: Write the failing test**

```python
def test_leaderboard_four_lists_with_gated_overall(rows):
    lb = fa.leaderboard(dt.date(2026, 4, 1), dt.date(2026, 4, 30),
                        fs.DEFAULT_SCORE_CONFIG, min_calls=8)
    assert set(lb) == {"most_calls", "on_time", "fastest", "overall"}
    # most_calls is volume-ranked, Trent's two days sum 41 -> top
    assert lb["most_calls"][0]["name"] == "Trent"
    # overall = avg of eligible daily scores; Juan (only sub-gate day) absent
    assert all(r["name"] != "Juan" for r in lb["overall"])
    # overall rows expose an average score and a day count
    assert "score" in lb["overall"][0] and "days" in lb["overall"][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_awards.py::test_leaderboard_four_lists_with_gated_overall -v`
Expected: FAIL (`no attribute 'leaderboard'`)

- [ ] **Step 3: Implement**

```python
def leaderboard(start: dt.date, end: dt.date,
                cfg: fs.ScoreConfig = fs.DEFAULT_SCORE_CONFIG,
                min_calls: int = 50) -> dict:
    rows = driver_days(start, end)
    by_driver: dict = {}
    for r in rows:
        a = by_driver.setdefault(r["driver_id"], {
            "name": r["name"], "driver_id": r["driver_id"], "calls": 0,
            "on_time": 0, "late": 0, "ms_weighted": 0,
            "score_sum": 0.0, "score_days": 0})
        a["calls"] += r["calls"]
        a["on_time"] += r["on_time"] or 0
        a["late"] += r["late"] or 0
        a["ms_weighted"] += (r["avg_ms"] or 0) * r["calls"]
        b = fs.daily_score(r, cfg)
        if b is not None:
            a["score_sum"] += b.score
            a["score_days"] += 1
    drivers = list(by_driver.values())
    for a in drivers:
        a["ontime_pct"] = (a["on_time"] / (a["on_time"] + a["late"]) * 100) if (a["on_time"] + a["late"]) else 0.0
        a["avg_ms"] = (a["ms_weighted"] / a["calls"]) if a["calls"] else 0

    most_calls = sorted(drivers, key=lambda a: (-a["calls"], a["name"]))
    rate = [a for a in drivers if a["calls"] >= min_calls]
    on_time = sorted(rate, key=lambda a: (-a["ontime_pct"], -a["calls"], a["name"]))
    fastest = sorted(rate, key=lambda a: (a["avg_ms"], -a["calls"], a["name"]))
    overall = sorted(
        ({"name": a["name"], "driver_id": a["driver_id"],
          "score": a["score_sum"] / a["score_days"], "days": a["score_days"],
          "calls": a["calls"]}
         for a in drivers if a["score_days"] > 0),
        key=lambda a: (-a["score"], a["name"]))
    return {"most_calls": most_calls, "on_time": on_time,
            "fastest": fastest, "overall": overall}
```

- [ ] **Step 4: Run tests**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_awards.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/forklift_awards.py tests/test_forklift_awards.py
git commit -m "feat(forklift): leaderboard four lists incl windowed overall score"
```

---

### Task 8: `awards_earned_by_driver` + forklift override scopes in `awards.py`

**Files:**
- Modify: `src/zira_dashboard/forklift_awards.py` (`awards_earned_by_driver`)
- Modify: `src/zira_dashboard/awards.py` (teach `apply_overrides*` the forklift scopes)
- Test: `tests/test_forklift_awards.py`, `tests/test_awards.py`

- [ ] **Step 1: Write the failing test**

```python
def test_awards_earned_by_driver_lists_goat(rows, monkeypatch):
    monkeypatch.setattr(fa, "_apply_overrides", lambda items: items)  # no overrides in test
    earned = fa.awards_earned_by_driver("Trent", dt.date(2026, 6, 1),
                                        fs.DEFAULT_SCORE_CONFIG)
    types = {e["type"] for e in earned}
    assert "forklift_goat" in types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_awards.py::test_awards_earned_by_driver_lists_goat -v`
Expected: FAIL

- [ ] **Step 3: Implement reverse lookup + override scopes**

In `forklift_awards.py`:

```python
FORKLIFT_SCOPES = ("forklift_goat", "forklift_top_day",
                   "forklift_best_ontime", "forklift_fastest", "forklift_badge")


def awards_earned_by_driver(name: str, today: dt.date,
                            cfg: fs.ScoreConfig = fs.DEFAULT_SCORE_CONFIG) -> list[dict]:
    earned: list[dict] = []
    g = goat(cfg)
    if g and g["name"] == name:
        earned.append({"type": "forklift_goat", "score": g["score"],
                       "day": g["day"]})
    for yr in (today.year, today.year - 1):
        for i, t in enumerate(annual_top_days(yr, cfg)):
            if t["name"] == name:
                earned.append({"type": "forklift_top_day", "year": yr,
                               "position": i + 1, "score": t["score"], "day": t["day"]})
        bo = annual_best_ontime(yr)
        if bo and bo["name"] == name:
            earned.append({"type": "forklift_best_ontime", "year": yr,
                           "value": bo["ontime_pct"]})
        ff = annual_fastest(yr)
        if ff and ff["name"] == name:
            earned.append({"type": "forklift_fastest", "year": yr, "value": ff["avg_ms"]})
        for m in range(1, 13):
            for i, b in enumerate(monthly_badges(yr, m, cfg)):
                if b["name"] == name:
                    earned.append({"type": "forklift_badge", "year": yr, "month": m,
                                   "position": i + 1, "score": b["score"], "day": b["day"]})
    return _apply_overrides(earned)


def _apply_overrides(items):
    # delegate to awards.apply_overrides_single for each forklift scope so manual
    # replace/delete/reset behaves identically to production awards.
    from zira_dashboard import awards
    return awards.apply_forklift_overrides(items)
```

In `awards.py`, add `apply_forklift_overrides(items)` reusing the same `award_overrides` table read/merge already used by `apply_overrides_single`, recognizing `FORKLIFT_SCOPES`. (Read the existing `apply_overrides_single` and follow its exact replace/delete/reset semantics; do not invent a new mechanism.)

- [ ] **Step 4: Run tests**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_awards.py tests/test_awards.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/forklift_awards.py src/zira_dashboard/awards.py tests/
git commit -m "feat(forklift): player-card reverse lookup + forklift override scopes"
```

---

## Phase C — Settings (GOAT Score panel)

### Task 9: Schema migration + `Resolved.score_config()`

**Files:**
- Modify: `src/zira_dashboard/_schema.py` (guarded ALTERs on `forklift_settings`)
- Modify: `src/zira_dashboard/forklift_settings.py`
- Test: `tests/test_forklift_settings.py`

- [ ] **Step 1: Write the failing test**

```python
from zira_dashboard import forklift_settings as fset
from zira_dashboard import forklift_score as fs


def test_score_config_defaults_when_unset():
    s = fset.Settings()  # all overrides None
    cfg = fset.resolve(s, algo_throughput=16).score_config()
    assert cfg.weights == {"calls": 40.0, "ontime": 30.0, "speed": 20.0, "util": 10.0}
    assert cfg.min_calls == 8 and cfg.target_calls == 25.0


def test_score_config_applies_overrides():
    s = fset.Settings(score_w_calls=50, score_w_ontime=20, score_w_speed=20,
                      score_w_util=10, score_min_calls=12, score_target_calls=30)
    cfg = fset.resolve(s, algo_throughput=16).score_config()
    assert cfg.weights["calls"] == 50.0 and cfg.min_calls == 12 and cfg.target_calls == 30.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_settings.py -k score_config -v`
Expected: FAIL

- [ ] **Step 3: Implement schema + resolver**

In `_schema.py`, after the existing forklift_settings override-column ALTERs, add (guarded, idempotent):

```python
for _col, _type in [
    ("score_w_calls", "NUMERIC"), ("score_w_ontime", "NUMERIC"),
    ("score_w_speed", "NUMERIC"), ("score_w_util", "NUMERIC"),
    ("score_target_calls", "NUMERIC"), ("score_ontime_floor", "NUMERIC"),
    ("score_fast_secs", "NUMERIC"), ("score_slow_secs", "NUMERIC"),
    ("score_min_calls", "INTEGER"),
]:
    cur.execute(f"ALTER TABLE forklift_settings ADD COLUMN IF NOT EXISTS {_col} {_type} NULL")
```

(Match the surrounding loop/execution style actually used in `_schema.py` for the prior override columns.)

In `forklift_settings.py`: add the nine `Optional` fields to the `Settings` dataclass + the row-load/save mapping, then add to `Resolved`:

```python
def score_config(self) -> "fs.ScoreConfig":
    from zira_dashboard import forklift_score as fs
    d = fs.DEFAULT_SCORE_CONFIG
    weights = {
        "calls": _f(self.score_w_calls, d.weights["calls"]),
        "ontime": _f(self.score_w_ontime, d.weights["ontime"]),
        "speed": _f(self.score_w_speed, d.weights["speed"]),
        "util": _f(self.score_w_util, d.weights["util"]),
    }
    return fs.ScoreConfig(
        weights=weights,
        target_calls=_f(self.score_target_calls, d.target_calls),
        ontime_floor=_f(self.score_ontime_floor, d.ontime_floor),
        fast_secs=_f(self.score_fast_secs, d.fast_secs),
        slow_secs=_f(self.score_slow_secs, d.slow_secs),
        min_calls=int(_f(self.score_min_calls, d.min_calls)),
    )
```

where `_f(override, default)` returns `float(override)` if not None else `default` (add the helper if not present). `Resolved` must carry the nine score override fields (thread them through `resolve()`), or hold a reference to `Settings`; follow whichever the existing `Resolved` does.

- [ ] **Step 4: Run tests**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_settings.py -v`
Expected: PASS (pure resolution tests); DB round-trip tests SKIP locally.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/_schema.py src/zira_dashboard/forklift_settings.py tests/test_forklift_settings.py
git commit -m "feat(forklift): score config settings (nullable overrides + resolver)"
```

---

### Task 10: Settings page — GOAT Score panel + POST parsing + live preview

**Files:**
- Modify: `src/zira_dashboard/routes/settings.py` (`_parse_forklift_overrides`, the forklift GET context)
- Modify: `src/zira_dashboard/templates/settings.html`
- Test: `tests/test_settings_routes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_post_forklift_sets_and_resets_score_overrides(client):
    # set a weight override
    r = client.post("/settings/forklift", data={
        "score_w_calls": "50", "score_w_ontime": "auto", "score_min_calls": "12"})
    assert r.status_code in (200, 303)
    # render shows the GOAT Score panel
    page = client.get("/settings").text
    assert "GOAT Score" in page and 'id="score-w-calls"' in page
```

(Use the existing settings-route test client fixture; if settings tests are DB-gated, mark accordingly.)

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_settings_routes.py -k forklift_score -v`
Expected: FAIL

- [ ] **Step 3: Implement parsing + template**

In `routes/settings.py`, extend `_parse_forklift_overrides(form)` to read the nine score fields (blank/"auto" → None; clamp: weights 0–100, target_calls 1–100, ontime_floor 0–99, fast/slow secs 1–600, min_calls 1–100). Pass `demand_summary`-style context for the panel: the resolved `score_config()`, the algorithm defaults (`forklift_score.DEFAULT_SCORE_CONFIG`) for the grey ticks, and one sample scored day (the most recent GOAT-eligible day, or the static sample) for the live example.

In `settings.html`, add a "GOAT Score" subsection under the Forklift section mirroring the existing slider blocks (`id="score-w-calls"` etc.), four weight sliders + advanced targets + gate, each with the grey algorithm tick + ↺ reset and a "reset all" — and a small live worked-example reusing the same normalization formula in JS (round-half-to-even `pyRound`, already defined in this template). The example recomputes the 0–100 score as sliders move; the grey "algorithm" baseline stays fixed. Concrete slider block to copy for each knob:

```html
<div class="fk-knob">
  <label for="score-w-calls">Calls weight <span class="fk-val" id="score-w-calls-val">40%</span></label>
  <input type="range" id="score-w-calls" name="score_w_calls" min="0" max="100" step="1" value="{{ score.weights.calls|int }}">
  <span class="fk-tick" style="left:40%">algorithm</span>
  <button type="button" class="fk-reset" data-target="score-w-calls" data-auto="40">↺</button>
</div>
```

(Repeat for on-time/speed/util weights + target_calls/fast_secs/slow_secs/ontime_floor/min_calls with their default ticks. Follow the exact class names and reset JS the existing forklift sliders use.)

- [ ] **Step 4: Run tests + render check**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_settings_routes.py -v && .venv/bin/ruff check src/zira_dashboard/routes/settings.py`
Expected: PASS, ruff clean

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/settings.py src/zira_dashboard/templates/settings.html tests/test_settings_routes.py
git commit -m "feat(forklift): GOAT Score settings panel with live preview"
```

---

## Phase D — UI surfaces

### Task 11: Dedicated forklift leaderboard page + nav + registration

**Files:**
- Create: `src/zira_dashboard/routes/forklift_leaderboards.py`
- Create: `src/zira_dashboard/templates/forklift_leaderboards.html`
- Modify: `src/zira_dashboard/app.py` (include_router), `src/zira_dashboard/templates/_staffing_subnav.html`
- Test: `tests/test_forklift_leaderboards_route.py`

- [ ] **Step 1: Write the failing test**

```python
def test_forklift_leaderboard_renders_four_cards(client, monkeypatch):
    from zira_dashboard import forklift_awards
    monkeypatch.setattr(forklift_awards, "leaderboard", lambda *a, **k: {
        "overall": [{"name": "Trent", "driver_id": "d1", "score": 86.0, "days": 12, "calls": 513}],
        "most_calls": [{"name": "Trent", "driver_id": "d1", "calls": 513, "on_time": 500, "late": 13,
                        "ontime_pct": 97.5, "avg_ms": 42000}],
        "on_time": [{"name": "Isidro", "driver_id": "d2", "calls": 471, "ontime_pct": 98.5,
                     "on_time": 464, "late": 7, "avg_ms": 51000}],
        "fastest": [{"name": "Trent", "driver_id": "d1", "calls": 513, "avg_ms": 42000,
                     "ontime_pct": 97.5, "on_time": 500, "late": 13}],
    })
    page = client.get("/staffing/forklift").text
    assert "Overall score" in page and "Most calls" in page
    assert "On-time" in page and "Fastest" in page
    assert "Trent" in page
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_leaderboards_route.py -v`
Expected: FAIL (404 / no route)

- [ ] **Step 3: Implement route + template + nav**

`routes/forklift_leaderboards.py` (mirror `routes/leaderboards.py`'s window parsing — reuse its date-range helper; import the resolved score config):

```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from zira_dashboard import forklift_awards, forklift_settings
from zira_dashboard.routes.leaderboards import _window_range  # reuse existing helper
from zira_dashboard.templating import templates  # match the app's templates handle

router = APIRouter()
FORKLIFT_MIN_CALLS = 50


@router.get("/staffing/forklift", response_class=HTMLResponse)
def forklift_leaderboards(request: Request, window: str = "alltime",
                          start: str | None = None, end: str | None = None):
    rng_start, rng_end, label = _window_range(window, start, end)
    cfg = forklift_settings.load_resolved().score_config()
    lb = forklift_awards.leaderboard(rng_start, rng_end, cfg, min_calls=FORKLIFT_MIN_CALLS)
    return templates.TemplateResponse("forklift_leaderboards.html", {
        "request": request, "active": "forklift_leaderboards",
        "window": window, "window_label": label, "lb": lb,
        "min_calls": FORKLIFT_MIN_CALLS})
```

(Use the project's actual templates accessor and the actual settings loader name — read `routes/leaderboards.py` + `routes/settings.py` for the exact symbols, e.g. how the resolved settings singleton is fetched.)

`templates/forklift_leaderboards.html` — extend `_staffing_base.html`, include `_staffing_subnav.html`, render the window selector (copy from `leaderboards.html`) and four `.lb-section` cards using `.lb-table`. Overall/on-time/fastest rows show rank + name + value; most-calls shows the on-time/late split. Example card body:

```html
<div class="lb-section">
  <h3>Overall score</h3>
  <table class="lb-table">
    {% for r in lb.overall %}
    <tr><td>{{ loop.index }}</td>
        <td><a href="/staffing/people/{{ r.name }}">{{ r.name }}</a></td>
        <td class="num">{{ '%.0f'|format(r.score) }}</td></tr>
    {% endfor %}
  </table>
  <p class="lb-note">min {{ min_calls }} calls to qualify</p>
</div>
```

Register in `app.py`: `from zira_dashboard.routes import forklift_leaderboards` and `app.include_router(forklift_leaderboards.router)` next to the other staffing routers. Add to `_staffing_subnav.html`:

```html
<a href="/staffing/forklift" class="{% if active == 'forklift_leaderboards' %}active{% endif %}">Forklift</a>
```

- [ ] **Step 4: Run tests**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_forklift_leaderboards_route.py -v && .venv/bin/ruff check src/zira_dashboard/routes/forklift_leaderboards.py`
Expected: PASS, ruff clean

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/forklift_leaderboards.py src/zira_dashboard/templates/forklift_leaderboards.html src/zira_dashboard/app.py src/zira_dashboard/templates/_staffing_subnav.html tests/test_forklift_leaderboards_route.py
git commit -m "feat(forklift): dedicated forklift leaderboard page"
```

---

### Task 12: Forklift section in the shared trophy case + override handler

**Files:**
- Modify: `src/zira_dashboard/routes/trophies.py` (GET context + `POST /api/awards/override` scope allow-list)
- Modify: `src/zira_dashboard/templates/trophy_case.html`
- Test: `tests/test_trophies_route.py`

- [ ] **Step 1: Write the failing test**

```python
def test_trophy_case_renders_forklift_section(client, monkeypatch):
    from zira_dashboard import forklift_awards as fa
    monkeypatch.setattr(fa, "goat", lambda cfg=None: {
        "name": "Trent", "driver_id": "d1", "score": 86.0, "day": __import__("datetime").date(2026,4,14)})
    monkeypatch.setattr(fa, "annual_top_days", lambda y, cfg=None, n=3: [])
    monkeypatch.setattr(fa, "monthly_badges", lambda y, m, cfg=None, n=3: [])
    monkeypatch.setattr(fa, "annual_best_ontime", lambda y, min_calls=50: None)
    monkeypatch.setattr(fa, "annual_fastest", lambda y, min_calls=50: None)
    page = client.get("/trophies").text
    assert "Forklift" in page and "Trent" in page


def test_override_accepts_forklift_scope(client):
    r = client.post("/api/awards/override", json={
        "scope": "forklift_goat", "action": "replace", "name": "Isidro"})
    assert r.status_code in (200, 303)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_trophies_route.py -k forklift -v`
Expected: FAIL

- [ ] **Step 3: Implement**

In `routes/trophies.py`: load the resolved score config, call the forklift award functions for the selected year/month, and pass them into the template context alongside the production awards. Extend the `POST /api/awards/override` scope allow-list to include `forklift_awards.FORKLIFT_SCOPES`; on save also call `forklift_awards.invalidate()` (next to the existing production cache invalidation).

In `trophy_case.html`: add a `<section id="forklift">` after the production sections, mirroring `.tc-group-block`/`.tc-card`/`.tc-row` with 🚜 header, the GOAT card (name + `score` + compact component line), the Annual block (top-3 by score + best on-time + fastest, reusing the existing year picker), and Monthly ribbons (existing month picker). Reuse the tier-filter classes (`.tc-trophy-gold/silver/bronze`) and the edit-modal markup for the new scopes.

- [ ] **Step 4: Run tests**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_trophies_route.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/trophies.py src/zira_dashboard/templates/trophy_case.html tests/test_trophies_route.py
git commit -m "feat(forklift): forklift section in the shared trophy case"
```

---

### Task 13: Player-card forklift block

**Files:**
- Modify: `src/zira_dashboard/routes/people.py`
- Modify: `src/zira_dashboard/templates/player_card.html`
- Test: `tests/test_people_route.py`

- [ ] **Step 1: Write the failing test**

```python
def test_player_card_shows_forklift_block_when_mapped(client, monkeypatch):
    from zira_dashboard import forklift_awards as fa
    monkeypatch.setattr("zira_dashboard.routes.people._forklift_for_person",
                        lambda name, today, cfg: {
                            "calls": 513, "ontime_pct": 97.5, "avg_ms": 42000,
                            "utilization_pct": 18.0, "best_score": 86.0,
                            "trophies": [{"type": "forklift_goat", "score": 86.0}]})
    page = client.get("/staffing/people/Trent").text
    assert "Forklift" in page and "513" in page


def test_player_card_no_forklift_block_when_unmapped(client, monkeypatch):
    monkeypatch.setattr("zira_dashboard.routes.people._forklift_for_person",
                        lambda name, today, cfg: None)
    page = client.get("/staffing/people/Nobody").text
    assert "Forklift stats" not in page
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_people_route.py -k forklift -v`
Expected: FAIL

- [ ] **Step 3: Implement**

In `people.py`, add `_forklift_for_person(name, today, cfg)`: resolve the plant `name` → forklift driver via `forklift_store.name_map("driver")` (reverse) or direct match; if found, pull the driver's windowed aggregate (calls/ontime/avg_ms/util via `forklift_awards.leaderboard` over the card's range filtered to this driver, or a small dedicated store read), the best-day score (`max` of `daily_score` over the driver's rows), and `forklift_awards.awards_earned_by_driver(name, today, cfg)`; return None if unmapped. Pass to the template.

In `player_card.html`, after the `pc-group-avgs` block, add (guarded by `{% if forklift %}`) a "Forklift stats" `.stat` grid (calls, on-time %, avg response, utilization muted, best-day score) and surface the forklift trophies in the existing trophy-case subsection with 🐐/🏆/🥇 icons.

- [ ] **Step 4: Run tests**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_people_route.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/people.py src/zira_dashboard/templates/player_card.html tests/test_people_route.py
git commit -m "feat(forklift): player-card forklift stats + trophies block"
```

---

## Phase E — Verification

### Task 14: Full suite, lint, and manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest -q`
Expected: all PASS (DB-gated tests SKIP locally; CI runs them with Postgres)

- [ ] **Step 2: Lint**

Run: `.venv/bin/ruff check src/ tests/ scripts/`
Expected: no `F` errors

- [ ] **Step 3: Local smoke (DB available)**

If a `DATABASE_URL` is available: run `ZIRA_API_KEY=test DATABASE_URL=… .venv/bin/python scripts/backfill_forklift_ontime.py` and confirm a non-zero `{"days":…, "rows":…}` outcome, then load `/staffing/forklift`, `/trophies#forklift`, a mapped player card, and `/settings` → Forklift → GOAT Score, confirming numbers render and sliders move the live example. (Note in the PR if no DB was available locally — prod fills via the warmer + reconstruction on deploy.)

- [ ] **Step 4: Commit any lint fixes, then open the PR per the team flow**

```bash
git add -A && git commit -m "chore(forklift): lint + verification fixes" || echo "nothing to fix"
```

(PR creation / merge happens via the established delivery loop — out of this plan's TDD scope, and per the repo rule commits/PsR happen only when Dale asks.)

---

## Notes for the implementer
- **Read before mirroring:** Tasks 10–13 say "mirror X" — actually open `leaderboards.html`, `trophy_case.html`, `player_card.html`, `settings.html`, and the matching routes first and copy their real class names, macros (`_goat_badges.html`), window-range helper, and the templates/settings accessors. The snippets here show intent, not the final class names.
- **Defensive contract:** every render-time call into `forklift_awards`/`forklift_score` must degrade to an empty/None result rather than 500 — mirror `forklift_advisor`'s try/except posture.
- **Cache invalidation:** any settings save that changes the score config must call `forklift_awards.invalidate()` so the trophy case/leaderboard recompute.
- **DB-gated tests:** decorate store/route tests that need Postgres with the existing skipif; pure tests (score, awards with the `driver_days` seam, ingest mapper, diff helpers) must run with no DB.
