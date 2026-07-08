"""The machine-breakdown detection tick is registered in the warmer list."""
import asyncio

from zira_dashboard import app as app_module


def test_machine_breakdown_warmer_registered():
    names = [name for name, _tick, _interval in app_module._WARMERS]
    assert "machine breakdown" in names


def test_tick_machine_breakdown_calls_run_detect_tick(monkeypatch):
    called = []
    from zira_dashboard import machine_breakdown
    monkeypatch.setattr(machine_breakdown, "run_detect_tick", lambda: called.append(1))
    asyncio.run(app_module._tick_machine_breakdown())
    assert called == [1]
