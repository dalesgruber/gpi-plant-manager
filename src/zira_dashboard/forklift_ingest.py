"""Pure transforms: gpiforklift.com API payloads -> snapshot row dicts.

No I/O. Keys match the forklift_calls_daily / forklift_driver_daily columns.
JSONB hour keys are stored as strings (slot number) for stable round-tripping.
"""
from __future__ import annotations

from collections import Counter
from datetime import date


def build_calls_daily(day: date, dashboard: dict, history: list[dict]) -> dict:
    completed = [c for c in history if c.get("status") == "completed"]
    by_station = Counter(c.get("workstationName") for c in completed if c.get("workstationName"))
    by_skill = Counter(c.get("requiredSkillId") for c in completed if c.get("requiredSkillId"))
    urgent = sum(1 for c in completed if c.get("priority") == "urgent")

    by_hour: dict[str, dict] = {}
    overload = neglected = 0
    for slot in (dashboard or {}).get("hourlyClaimAvgs", []) or []:
        key = str(slot.get("slot"))
        by_hour[key] = {
            "calls": int(slot.get("calls") or 0),
            "overload": int(slot.get("overloadCount") or 0),
            "neglected": int(slot.get("neglectedCount") or 0),
            "avg_minutes": float(slot.get("avgMinutes") or 0),
        }
        overload += int(slot.get("overloadCount") or 0)
        neglected += int(slot.get("neglectedCount") or 0)

    return {
        "day": day,
        "total_calls": len(completed),
        "urgent_calls": urgent,
        "overload_count": overload,
        "neglected_count": neglected,
        "by_hour": by_hour,
        "by_station": dict(by_station),
        "by_skill": dict(by_skill),
    }


def build_driver_daily(day: date, dashboard: dict) -> list[dict]:
    rows = []
    for d in (dashboard or {}).get("driverLeaderboard", []) or []:
        rows.append({
            "day": day,
            "driver_id": str(d.get("driverId") or d.get("name")),
            "name": d.get("name") or "",
            "calls": int(d.get("total") or 0),
            "on_time": int(d.get("onTime") or 0),
            "late": int(d.get("late") or 0),
            "avg_ms": int(d.get("avgMs") or 0),
            "max_ms": int(d.get("maxMs") or 0),
            "utilization_pct": float(d.get("utilizationPct") or 0),
            "on_call_ms": int(d.get("totalOnCallMs") or 0),
            "available_ms": int(d.get("availableMs") or 0),
        })
    return rows
