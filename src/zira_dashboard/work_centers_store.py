"""Per-work-center overrides + cell/value-stream group goals.

Storage: work_centers.json
{
  "work_centers": {
     "<key>": {"goal_per_day": ..., "min_ops": ..., "max_ops": ...,
               "required_skills": [...], "note": "...",
               "cell": "...", "value_stream": "..."}
  },
  "group_overrides": {
     "cell": {"Recycling": 2000, ...},
     "value_stream": {"Recycled": 3500, ...}
  }
}

Absorbs the legacy flat dict (per-meter-id) on first run and the legacy
`station_targets` from settings.json if present.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from .shift_config import TARGET_PER_DAY, productive_minutes_per_day
from .staffing import LOCATIONS, SKILLS, Location, required_skills_for

WORK_CENTERS_PATH = Path("work_centers.json")
_LEGACY_SETTINGS_PATH = Path("settings.json")

GROUP_KINDS = ("group", "value_stream")  # "group" = user-defined Groups (multi-valued per WC)

# Value stream options are fixed.
VALUE_STREAMS: tuple[str, ...] = ("New", "Recycled", "Transportation")

_lock = RLock()
_state: dict = {
    "work_centers": {},
    "group_overrides": {"group": {}, "value_stream": {}},
    "groups": [],  # registry of group names users have defined
}


def _isint(v) -> bool:
    try:
        int(v); return True
    except (TypeError, ValueError):
        return False


def _read_file() -> dict:
    if not WORK_CENTERS_PATH.exists():
        return {}
    try:
        data = json.loads(WORK_CENTERS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_file() -> None:
    WORK_CENTERS_PATH.write_text(json.dumps(_state, indent=2), encoding="utf-8")


def _default_goal_for(loc: Location) -> int:
    category = {"Dismantler": "Dismantler", "Repair": "Repair"}.get(loc.skill, "Other")
    return int(TARGET_PER_DAY.get(category, 0))


def _migrate_from_legacy_settings() -> dict[str, dict]:
    """Pull `station_targets` from legacy settings.json if present."""
    if not _LEGACY_SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(_LEGACY_SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    station_targets = data.get("station_targets") or {}
    out: dict[str, dict] = {}
    for meter_id, goal in station_targets.items():
        if _isint(goal):
            out[str(meter_id)] = {"goal_per_day": int(goal)}
    return out


def _key_for(loc: Location) -> str:
    return loc.meter_id if loc.meter_id else f"name:{loc.name}"


def _load() -> None:
    global _state
    raw = _read_file()

    if "work_centers" in raw and isinstance(raw["work_centers"], dict):
        wc_raw = {str(k): (v if isinstance(v, dict) else {}) for k, v in raw["work_centers"].items()}
        # Migrate legacy `cell: str` → `groups: [str]` for any WC records that still carry it.
        for k, rec in wc_raw.items():
            if isinstance(rec, dict) and "cell" in rec and "groups" not in rec:
                c = rec.pop("cell", "")
                rec["groups"] = [c] if isinstance(c, str) and c else []
        overrides = raw.get("group_overrides") or {}
        if not isinstance(overrides, dict):
            overrides = {}
        # Accept both legacy "cell" and new "group" keys in overrides.
        group_o = overrides.get("group") or overrides.get("cell") or {}
        vs_o = overrides.get("value_stream") or {}
        # Registry: prefer new "groups" list, fall back to legacy "cells".
        registry_list = raw.get("groups") or raw.get("cells") or []
        if not isinstance(registry_list, list):
            registry_list = []
        registry_clean = sorted({str(c).strip() for c in registry_list if isinstance(c, str) and str(c).strip()}, key=str.lower)
        _state = {
            "work_centers": wc_raw,
            "group_overrides": {
                "group": {str(k): int(v) for k, v in group_o.items() if _isint(v)},
                "value_stream": {str(k): int(v) for k, v in vs_o.items() if _isint(v)},
            },
            "groups": registry_clean,
        }
        return

    # Old flat shape (or empty): migrate.
    wc: dict[str, dict] = {}
    if raw and isinstance(raw, dict):
        # existing work_centers.json with flat keys per meter_id
        for k, v in raw.items():
            if isinstance(v, dict):
                wc[str(k)] = v
    if not wc:
        # First-ever run: try legacy settings.json too.
        wc = _migrate_from_legacy_settings()

    _state = {
        "work_centers": wc,
        "group_overrides": {"group": {}, "value_stream": {}},
        "groups": [],
    }
    if wc:
        _write_file()


with _lock:
    _load()


def _override_for(loc: Location) -> dict:
    rec = _state["work_centers"].get(_key_for(loc))
    return rec if isinstance(rec, dict) else {}


# ---------- effective per-work-center ----------

def effective(loc: Location) -> dict:
    o = _override_for(loc)
    req = o.get("required_skills")
    if not (isinstance(req, list) and all(isinstance(s, str) and s in SKILLS for s in req)):
        req = list(required_skills_for(loc))
    raw_groups = o.get("groups")
    if isinstance(raw_groups, list):
        wc_groups = [str(g) for g in raw_groups if isinstance(g, str) and g]
    elif isinstance(o.get("cell"), str) and o.get("cell"):  # legacy single-value fallback
        wc_groups = [o["cell"]]
    else:
        wc_groups = []
    raw_defaults = o.get("default_people")
    if isinstance(raw_defaults, list):
        defaults = [str(n).strip() for n in raw_defaults if isinstance(n, str) and str(n).strip()]
    else:
        defaults = []
    return {
        "goal_per_day": int(o["goal_per_day"]) if _isint(o.get("goal_per_day")) else _default_goal_for(loc),
        "min_ops":      int(o["min_ops"])      if _isint(o.get("min_ops"))      else int(loc.min_ops),
        "max_ops":      (int(o["max_ops"])     if _isint(o.get("max_ops"))      else loc.max_ops),
        "required_skills": req,
        "note":         (o.get("note") or loc.note or "") or "",
        "groups":       wc_groups,
        "value_stream": (o.get("value_stream") or "") or "",
        "default_people": defaults,
    }


def goal_per_day(loc: Location) -> int:  return effective(loc)["goal_per_day"]
def min_ops(loc: Location) -> int:        return int(effective(loc)["min_ops"])
def max_ops(loc: Location):               return effective(loc)["max_ops"]
def required_skills(loc: Location):       return list(effective(loc)["required_skills"])
def note(loc: Location) -> str:           return effective(loc)["note"]
def groups(loc: Location) -> list[str]:   return list(effective(loc)["groups"])
def value_stream(loc: Location) -> str:   return effective(loc)["value_stream"]
def default_people(loc: Location) -> list[str]: return list(effective(loc)["default_people"])


def goal_per_hour(loc: Location) -> float:
    hrs = productive_minutes_per_day() / 60.0
    return (goal_per_day(loc) / hrs) if hrs else 0.0


# ---------- groups (cell / value_stream) ----------

def members(kind: str, name: str) -> list[Location]:
    if kind not in GROUP_KINDS or not name:
        return []
    if kind == "group":
        return [loc for loc in LOCATIONS if name in groups(loc)]
    return [loc for loc in LOCATIONS if value_stream(loc) == name]


def group_goal_auto(kind: str, name: str) -> int:
    return sum(goal_per_day(loc) for loc in members(kind, name))


def group_goal_override(kind: str, name: str):
    if kind not in GROUP_KINDS:
        return None
    v = _state["group_overrides"].get(kind, {}).get(name)
    return int(v) if _isint(v) else None


def group_goal(kind: str, name: str) -> int:
    o = group_goal_override(kind, name)
    return int(o) if o is not None else group_goal_auto(kind, name)


def all_group_names(kind: str) -> list[str]:
    seen, out = set(), []
    if kind == "group":
        for loc in LOCATIONS:
            for g in groups(loc):
                if g not in seen:
                    seen.add(g); out.append(g)
        for v in _state.get("groups", []):
            if v not in seen:
                seen.add(v); out.append(v)
    else:
        for loc in LOCATIONS:
            v = value_stream(loc)
            if v and v not in seen:
                seen.add(v); out.append(v)
        for v in VALUE_STREAMS:
            if v not in seen:
                seen.add(v); out.append(v)
    for v in _state["group_overrides"].get(kind, {}):
        if v not in seen:
            seen.add(v); out.append(v)
    return sorted(out, key=str.lower)


# ---------- write ----------

def save_one(loc: Location, updates: dict) -> dict:
    clean: dict = {}
    # numeric goal
    if "goal_per_day" in updates:
        v = updates["goal_per_day"]
        if isinstance(v, str): v = v.strip()
        if v not in (None, "") and _isint(v):
            clean["goal_per_day"] = max(0, int(v))
    # numeric min/max ops
    for k in ("min_ops", "max_ops"):
        if k in updates:
            v = updates[k]
            if isinstance(v, str): v = v.strip()
            if v not in (None, "") and _isint(v):
                iv = int(v)
                if iv >= 0:
                    clean[k] = iv
    # required skills
    if "required_skills" in updates:
        v = updates["required_skills"]
        if isinstance(v, list):
            clean["required_skills"] = [s for s in v if isinstance(s, str) and s in SKILLS]
    # note: free text
    if "note" in updates:
        v = updates["note"]
        if isinstance(v, str):
            clean["note"] = v.strip()[:200]
    # value_stream: must be in VALUE_STREAMS or empty
    if "value_stream" in updates:
        v = updates["value_stream"]
        if isinstance(v, str):
            v = v.strip()
            if v == "" or v in VALUE_STREAMS:
                clean["value_stream"] = v
    # groups: list of registered group names
    if "groups" in updates:
        v = updates["groups"]
        if isinstance(v, list):
            registry = set(_state.get("groups", []))
            clean["groups"] = [str(g) for g in v if isinstance(g, str) and g in registry]
    # default_people: list of person names (we don't validate against roster here —
    # it's cheap to store and roster can change; UI shows a multi-select)
    if "default_people" in updates:
        v = updates["default_people"]
        if isinstance(v, list):
            seen, out = set(), []
            for n in v:
                if isinstance(n, str):
                    s = n.strip()[:80]
                    if s and s not in seen:
                        seen.add(s); out.append(s)
            clean["default_people"] = out

    with _lock:
        key = _key_for(loc)
        existing = _state["work_centers"].get(key, {})
        if not isinstance(existing, dict):
            existing = {}
        merged = {**existing, **clean}
        # Drop truly empty strings so they revert to default.
        for k in ("note", "cell", "value_stream"):
            if k in merged and merged[k] == "":
                merged.pop(k)
        # Drop legacy "cell" key if it lingers — now unused.
        merged.pop("cell", None)
        if not merged:
            _state["work_centers"].pop(key, None)
        else:
            _state["work_centers"][key] = merged
        _write_file()
    return effective(loc)


def registered_groups() -> list[str]:
    with _lock:
        return list(_state.get("groups", []))


def add_group(name: str) -> None:
    name = (name or "").strip()
    if not name:
        return
    name = name[:80]
    with _lock:
        current = list(_state.get("groups", []))
        if name not in current:
            current.append(name)
            _state["groups"] = sorted(set(current), key=str.lower)
            _write_file()


def rename_group(old: str, new: str) -> None:
    old = (old or "").strip(); new = (new or "").strip()
    if not old or not new or old == new:
        return
    new = new[:80]
    with _lock:
        current = [g for g in _state.get("groups", []) if g != old]
        if new not in current:
            current.append(new)
        _state["groups"] = sorted(set(current), key=str.lower)
        # Update WC membership lists.
        for rec in _state["work_centers"].values():
            if isinstance(rec, dict) and isinstance(rec.get("groups"), list):
                rec["groups"] = [new if g == old else g for g in rec["groups"]]
        # Update override key.
        bucket = _state["group_overrides"].setdefault("group", {})
        if old in bucket:
            bucket[new] = bucket.pop(old)
        _write_file()


def delete_group(name: str) -> None:
    name = (name or "").strip()
    if not name:
        return
    with _lock:
        _state["groups"] = [g for g in _state.get("groups", []) if g != name]
        for rec in _state["work_centers"].values():
            if isinstance(rec, dict) and isinstance(rec.get("groups"), list):
                rec["groups"] = [g for g in rec["groups"] if g != name]
        _state["group_overrides"].get("group", {}).pop(name, None)
        _write_file()


def save_group_override(kind: str, name: str, value) -> None:
    if kind not in GROUP_KINDS or not name:
        return
    with _lock:
        bucket = _state["group_overrides"].setdefault(kind, {})
        if isinstance(value, str):
            value = value.strip()
        if value in (None, ""):
            bucket.pop(name, None)
        elif _isint(value):
            bucket[name] = int(value)
        _write_file()


def snapshot() -> dict:
    with _lock:
        return {
            "work_centers": {k: dict(v) for k, v in _state["work_centers"].items()},
            "group_overrides": {
                "cell": dict(_state["group_overrides"].get("cell", {})),
                "value_stream": dict(_state["group_overrides"].get("value_stream", {})),
            },
        }
