"""Pure data/computation helpers for the recycling dashboards, extracted from
routes/departments.py. No DB / Odoo / Request / template imports — callers pass
already-loaded data + injected callables. Lets the goal math be unit-tested
without a backend.
"""

from __future__ import annotations


def progress_color(pct_of_target: float | None) -> str | None:
    """HSL color for an actual-vs-goal percentage. Neutral gray at 100%
    (was pure white, invisible on light-mode backgrounds); ramps to red
    below and green above. Saturation/lightness step in 12 buckets so
    big misses stand out and small ones are subtle.
    """
    if pct_of_target is None:
        return None
    delta = max(-100.0, min(100.0, pct_of_target - 100.0))
    if abs(delta) < 1.0:
        return "#9ca3af"  # neutral gray — readable on both light + dark
    step = min(12, max(1, round(abs(delta) / 100.0 * 12)))
    sat = 55.0 + step * 2.0
    light = 65.0 - step * 3.5
    hue = 130 if delta > 0 else 0
    return f"hsl({hue:.0f}, {sat:.0f}%, {light:.0f}%)"
