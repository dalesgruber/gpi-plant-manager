"""Warmer self-heal of the forklift on-time/utilization history.

Drives the decision helper directly (no DB / no event loop required) to
verify: (1) sparse history triggers exactly one reconstruction, and (2) the
once-per-process guard prevents it from running again on later ticks.
"""
import asyncio

from zira_dashboard import app, forklift_backfill, forklift_store


def _run(coro):
    return asyncio.run(coro)


def test_self_heal_runs_reconstruction_once_when_sparse(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(forklift_store, "ontime_history_day_count", lambda: 0)
    monkeypatch.setattr(
        forklift_backfill, "reconstruct_ontime_history",
        lambda: calls.update(n=calls["n"] + 1) or {"days": 90, "rows": 5},
    )
    monkeypatch.setattr(app, "_forklift_ontime_reconstructed", False)

    _run(app._maybe_reconstruct_ontime())
    assert calls["n"] == 1  # ran once

    # Guard: a second tick must NOT call reconstruction again, even though
    # the source still reports sparse on-time history.
    _run(app._maybe_reconstruct_ontime())
    assert calls["n"] == 1


def test_self_heal_skips_when_history_is_sufficient(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(forklift_store, "ontime_history_day_count", lambda: 30)
    monkeypatch.setattr(
        forklift_backfill, "reconstruct_ontime_history",
        lambda: calls.update(n=calls["n"] + 1),
    )
    monkeypatch.setattr(app, "_forklift_ontime_reconstructed", False)

    _run(app._maybe_reconstruct_ontime())
    assert calls["n"] == 0


def test_self_heal_guard_set_even_when_reconstruction_raises(monkeypatch):
    """A transient failure must still flip the guard so the ~90-call
    reconstruction doesn't hammer the API every 600s tick."""
    calls = {"n": 0}
    monkeypatch.setattr(forklift_store, "ontime_history_day_count", lambda: 0)

    def boom():
        calls["n"] += 1
        raise RuntimeError("source down")

    monkeypatch.setattr(forklift_backfill, "reconstruct_ontime_history", boom)
    monkeypatch.setattr(app, "_forklift_ontime_reconstructed", False)

    _run(app._maybe_reconstruct_ontime())  # must not raise
    assert calls["n"] == 1
    assert app._forklift_ontime_reconstructed is True

    _run(app._maybe_reconstruct_ontime())
    assert calls["n"] == 1  # guard held despite the earlier failure
