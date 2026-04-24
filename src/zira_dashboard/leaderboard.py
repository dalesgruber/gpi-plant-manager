"""Fetch and aggregate Zira readings into per-station daily totals."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any

from zira_probe.client import ZiraClient

from .shift_config import SITE_TZ, in_shift
from .stations import Station

PAGE_SIZE = 500
MAX_PAGES = 20
WORKING_STATUS = "Working"


@dataclass(frozen=True)
class StationTotal:
    station: Station
    units: int
    reading_count: int
    truncated: bool
    downtime_minutes: int
    last_reading_at: datetime | None
    last_status: str | None
    samples: tuple[tuple[datetime, int], ...]  # (event_dt_utc, units) for shift rows with units > 0


def day_window_utc(day: date) -> tuple[str, str]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = datetime.combine(day, time.max, tzinfo=timezone.utc)
    return _iso_z(start), _iso_z(end)


def _iso_z(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _envelope(payload: Any) -> tuple[list[dict], str | None]:
    if isinstance(payload, dict):
        rows = payload.get("data") or []
        cursor = payload.get("lastValue") or None
        return (rows if isinstance(rows, list) else []), cursor
    if isinstance(payload, list):
        return payload, None
    return [], None


def _parse_event_date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_station_day(
    client: ZiraClient, station: Station, start_iso: str, end_iso: str
) -> StationTotal:
    total = 0
    count = 0
    downtime = 0
    last_reading_at: datetime | None = None
    last_status: str | None = None
    last_value: str | None = None
    truncated = False
    samples: list[tuple[datetime, int]] = []
    for _ in range(MAX_PAGES):
        payload = client.get_readings(
            meter_id=station.meter_id,
            end_time=end_iso,
            start_time=start_iso,
            limit=PAGE_SIZE,
            last_value=last_value,
        )
        rows, cursor = _envelope(payload)
        if not rows:
            break
        if last_reading_at is None:
            first = rows[0]
            last_reading_at = _parse_event_date(first.get("event_date"))
            last_status = first.get("status")
        for r in rows:
            u = r.get("units")
            u_int = int(u) if isinstance(u, (int, float)) else 0
            total += u_int
            status = r.get("status")
            duration = r.get("duration")
            event_dt = _parse_event_date(r.get("event_date"))
            event_local = event_dt.astimezone(SITE_TZ) if event_dt else None
            in_shift_now = event_local is not None and in_shift(event_local)
            if status and status != WORKING_STATUS and isinstance(duration, (int, float)) and in_shift_now:
                downtime += int(duration)
            if u_int > 0 and event_dt is not None and in_shift_now:
                samples.append((event_dt, u_int))
        count += len(rows)
        if not cursor or len(rows) < PAGE_SIZE:
            break
        last_value = cursor
    else:
        truncated = True
    return StationTotal(
        station=station,
        units=total,
        reading_count=count,
        truncated=truncated,
        downtime_minutes=downtime,
        last_reading_at=last_reading_at,
        last_status=last_status,
        samples=tuple(samples),
    )


def leaderboard(
    client: ZiraClient,
    stations: list[Station],
    day: date,
) -> list[StationTotal]:
    start_iso, end_iso = day_window_utc(day)
    with ThreadPoolExecutor(max_workers=min(10, len(stations) or 1)) as pool:
        results = list(
            pool.map(
                lambda s: fetch_station_day(client, s, start_iso, end_iso),
                stations,
            )
        )
    results.sort(key=lambda r: (-r.units, r.station.name))
    return results
