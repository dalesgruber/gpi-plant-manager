"""Per-widget visual customization (title, color, etc.) persisted to JSON."""

from __future__ import annotations

import json
import re
from pathlib import Path
from threading import RLock

CUSTOM_PATH = Path("widget_customizations.json")
_lock = RLock()

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

ORIENTATIONS = {"horizontal", "vertical"}
NUMBER_POSITIONS = {"widget", "bar", "inside", "hidden"}
SORTS = {"preset", "desc", "asc", "alpha"}
ALIGNS = {"left", "center", "right"}


def _read_all() -> dict:
    if not CUSTOM_PATH.exists():
        return {}
    try:
        data = json.loads(CUSTOM_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(data: dict) -> None:
    CUSTOM_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_all(page: str) -> dict[str, dict]:
    with _lock:
        data = _read_all()
        page_data = data.get(page)
        return dict(page_data) if isinstance(page_data, dict) else {}


def load_one(page: str, widget_id: str) -> dict:
    return load_all(page).get(widget_id, {})


def save_one(page: str, widget_id: str, config: dict) -> dict:
    clean: dict = {}
    title = config.get("title")
    if isinstance(title, str) and title.strip():
        clean["title"] = title.strip()[:120]
    color = config.get("color")
    if isinstance(color, str) and _HEX_RE.match(color.strip()):
        clean["color"] = color.strip().lower()
    for key, allowed in (
        ("orientation", ORIENTATIONS),
        ("number_position", NUMBER_POSITIONS),
        ("sort", SORTS),
        ("align", ALIGNS),
    ):
        v = config.get(key)
        if isinstance(v, str) and v in allowed:
            clean[key] = v
    for key in ("show_target", "show_legend"):
        if key in config:
            clean[key] = bool(config[key])

    with _lock:
        data = _read_all()
        page_data = data.get(page) if isinstance(data.get(page), dict) else {}
        if clean:
            page_data[widget_id] = clean
        else:
            page_data.pop(widget_id, None)
        data[page] = page_data
        _write_all(data)
    return clean


def reset_one(page: str, widget_id: str) -> None:
    with _lock:
        data = _read_all()
        page_data = data.get(page)
        if isinstance(page_data, dict):
            page_data.pop(widget_id, None)
            data[page] = page_data
            _write_all(data)
