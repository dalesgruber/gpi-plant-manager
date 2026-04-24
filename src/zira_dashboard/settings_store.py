"""Read/write user-editable targets (pallets/day) at the station and group level.

Storage is pallets-per-day. Consumers can ask for per-day or per-hour; the
per-hour derivation uses the shift's productive minutes (shift length minus
scheduled breaks).
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from .shift_config import TARGET_PER_DAY, productive_minutes_per_day
from .stations import STATIONS, Station

SETTINGS_VERSION = 2  # v1 = pallets/hr values, v2 = pallets/day values

# Deferred import to avoid circularity (work_centers_store imports staffing + shift_config).
def _wc_store():
    from . import work_centers_store
    return work_centers_store


def _loc_for_station(station: Station):
    """Map a Zira Station (has meter_id) to its work-center Location, or None."""
    from .staffing import LOCATIONS
    for loc in LOCATIONS:
        if loc.meter_id == station.meter_id:
            return loc
    return None

SETTINGS_PATH = Path("settings.json")

_lock = RLock()
_state: dict[str, dict[str, int]] = {"station_targets": {}, "group_targets": {}}


def _isint(v) -> bool:
    try:
        int(v)
        return True
    except (TypeError, ValueError):
        return False


def _productive_hours() -> float:
    m = productive_minutes_per_day()
    return (m / 60.0) if m else 0.0


def _load_from_disk() -> None:
    if not SETTINGS_PATH.exists():
        return
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    version = int(data.get("version", 1)) if _isint(data.get("version", 1)) else 1
    st = {str(k): int(v) for k, v in (data.get("station_targets") or {}).items() if _isint(v)}
    gp = {str(k): int(v) for k, v in (data.get("group_targets") or {}).items() if _isint(v)}

    if version < 2:
        # Migrate v1 (pallets/hr) → v2 (pallets/day) by multiplying by productive hours.
        hrs = _productive_hours() or 1.0
        st = {k: int(round(v * hrs)) for k, v in st.items()}
        gp = {k: int(round(v * hrs)) for k, v in gp.items()}
        _state["station_targets"] = st
        _state["group_targets"] = gp
        _write_to_disk()  # persist with current version
        return

    _state["station_targets"] = st
    _state["group_targets"] = gp


def _write_to_disk() -> None:
    payload = {
        "version": SETTINGS_VERSION,
        "station_targets": dict(_state["station_targets"]),
        "group_targets": dict(_state["group_targets"]),
    }
    SETTINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


with _lock:
    _load_from_disk()


def save(station_targets: dict[str, int], group_targets: dict[str, int]) -> None:
    with _lock:
        _state["station_targets"] = {str(k): int(v) for k, v in station_targets.items()}
        _state["group_targets"] = {str(k): int(v) for k, v in group_targets.items()}
        _write_to_disk()


def station_target_per_day(station: Station) -> int:
    """Per-day goal for a Zira station, sourced from work_centers_store."""
    loc = _loc_for_station(station)
    if loc is not None:
        return _wc_store().goal_per_day(loc)
    return int(TARGET_PER_DAY.get(station.category, 0))


def group_target_per_day(category: str) -> int:
    with _lock:
        override = _state["group_targets"].get(category)
        if override is not None:
            return int(override)
    members = [s for s in STATIONS if s.category == category]
    if not members:
        return 0
    return sum(station_target_per_day(s) for s in members)


def station_target(station: Station) -> float:
    hrs = _productive_hours()
    return (station_target_per_day(station) / hrs) if hrs else 0.0


def group_target(category: str) -> float:
    hrs = _productive_hours()
    return (group_target_per_day(category) / hrs) if hrs else 0.0


def snapshot() -> dict:
    with _lock:
        return {
            "station_targets": dict(_state["station_targets"]),
            "group_targets": dict(_state["group_targets"]),
        }
