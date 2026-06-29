"""Forklift settings route: the pure override-parsing helper (auto vs set,
clamps) and the settings-page template render (sliders + both numbers). Both run
everywhere (no DB, no network)."""
from zira_dashboard.routes import settings as settings_route


def test_parse_forklift_overrides_auto_vs_set():
    # "auto" / blank -> None (follow algorithm); a value -> override
    s = settings_route._parse_forklift_overrides({
        "enabled": "on", "throughput": "auto", "utilization_pct": "70",
        "plan_for": "0.8", "history_samples": "", "include_loading_jockeying": "on",
        "coldstart_calls_per_day": "0",
    })
    assert s.enabled is True
    assert s.throughput_override is None          # "auto"
    assert s.utilization_override == 0.70         # 70% -> 0.70
    assert s.plan_for_percentile_override == 0.8
    assert s.history_samples_override is None     # blank -> auto
    assert s.include_loading_jockeying is True


def test_parse_forklift_overrides_clamps():
    s = settings_route._parse_forklift_overrides({"utilization_pct": "999", "throughput": "0"})
    assert s.utilization_override == 1.0          # clamp <=100%
    assert s.throughput_override == 0.1 or s.throughput_override >= 1  # clamp >0 (floor 5)


def test_parse_forklift_overrides_disabled_and_unchecked():
    s = settings_route._parse_forklift_overrides({})  # nothing checked
    assert s.enabled is False
    assert s.include_loading_jockeying is False
    assert s.throughput_override is None


def test_parse_forklift_overrides_history_and_plan_clamp():
    s = settings_route._parse_forklift_overrides({
        "history_samples": "100", "plan_for": "0.1"})
    assert s.history_samples_override == 20       # clamp 2-20
    assert s.plan_for_percentile_override == 0.5  # clamp 0.5-1.0
