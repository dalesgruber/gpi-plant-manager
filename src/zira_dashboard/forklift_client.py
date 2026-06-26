"""Read-only REST client for the GPI Forklift app (gpiforklift.com).

The forklift app runs a call-and-dispatch queue; this client pulls demand and
driver-performance data into the Plant Manager. GET-only — we never write.

Config (read per-call, so importing this module has no side effects):
  FORKLIFT_API_KEY   - sent as the X-API-Key header (best-effort; the API
                       currently serves reads without auth).
  FORKLIFT_BASE_URL  - defaults to https://www.gpiforklift.com
"""
from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_BASE_URL = "https://www.gpiforklift.com"
_TIMEOUT = 15


class ForkliftError(Exception):
    """Raised on any forklift API failure."""


def _base_url() -> str:
    return (os.environ.get("FORKLIFT_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _get(path: str) -> Any:
    """GET {base}{path} with the API key header; return parsed JSON.
    Wraps any transport/HTTP error in ForkliftError."""
    url = f"{_base_url()}{path}"
    headers = {}
    key = os.environ.get("FORKLIFT_API_KEY")
    if key:
        headers["X-API-Key"] = key
    try:
        r = requests.get(url, headers=headers, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001 - normalize to one error type
        raise ForkliftError(f"GET {path} failed: {e}") from e


def fetch_dashboard() -> dict:
    """Today's precomputed analytics: driverLeaderboard, hourlyClaimAvgs, etc."""
    return _get("/api/dashboard")


def fetch_queue_history() -> list[dict]:
    """Today's call records (the API only exposes 'today')."""
    return _get("/api/queue/history")


def fetch_drivers() -> list[dict]:
    """Forklift drivers: {id, name, isOverloadResponder, skills}."""
    return _get("/api/drivers")
