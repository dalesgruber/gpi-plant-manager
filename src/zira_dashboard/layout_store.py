"""Persistent per-page widget layouts for the dashboard (Gridstack positions)."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

LAYOUTS_PATH = Path("layouts.json")
_lock = RLock()


def load(page: str) -> list[dict]:
    with _lock:
        if not LAYOUTS_PATH.exists():
            return []
        try:
            data = json.loads(LAYOUTS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict):
            return []
        items = data.get(page) or []
        if not isinstance(items, list):
            return []
        return [_normalize(i) for i in items if isinstance(i, dict) and i.get("id")]


def save(page: str, layout: list[dict]) -> None:
    with _lock:
        blob: dict = {}
        if LAYOUTS_PATH.exists():
            try:
                loaded = json.loads(LAYOUTS_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    blob = loaded
            except json.JSONDecodeError:
                blob = {}
        blob[page] = [_normalize(i) for i in layout if isinstance(i, dict) and i.get("id")]
        LAYOUTS_PATH.write_text(json.dumps(blob, indent=2), encoding="utf-8")


def _normalize(item: dict) -> dict:
    return {
        "id": str(item.get("id")),
        "x": int(item.get("x", 0) or 0),
        "y": int(item.get("y", 0) or 0),
        "w": int(item.get("w", 1) or 1),
        "h": int(item.get("h", 1) or 1),
    }


def layout_map(page: str) -> dict[str, dict]:
    return {item["id"]: item for item in load(page)}
