from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import shutil
from unittest.mock import patch

os.environ.setdefault("AUTH_DISABLED", "1")
os.environ.setdefault("ZIRA_API_KEY", "preview-dummy")

from fastapi.testclient import TestClient  # noqa: E402

from zira_dashboard import _http_cache  # noqa: E402
from zira_dashboard.app import app  # noqa: E402


OUT = Path("scripts/_preview_out/new_leaderboard")
STATIC = Path("src/zira_dashboard/static")


def _row(rank: int, name: str, ytd: float, l30: float) -> dict:
    return {
        "rank": rank,
        "name": name,
        "ytd": {"eligible": True, "avg_units": ytd, "days": 24 - rank, "label": None},
        "l30": {"eligible": True, "avg_units": l30, "days": 8 - rank, "label": None},
    }


def _payload(active: list[str]) -> dict:
    names = {
        "Juniors": [("Alex M.", 642.0, 668.0), ("Jordan R.", 621.0, 650.0), ("Sam T.", 603.0, 611.0)],
        "Woodpecker": [("Taylor N.", 588.0, 610.0), ("Morgan P.", 571.0, 582.0), ("Riley C.", 548.0, 559.0)],
        "Hand Build": [("Jamie V.", 184.0, 192.0), ("Avery D.", 179.0, 186.0), ("Quinn S.", 171.0, 176.0)],
    }
    families = {
        family: {
            "thresholds": {"ytd": 2, "l30": 1},
            "rows": [
                _row(index, person, ytd, l30)
                for index, (person, ytd, l30) in enumerate(names[family], 1)
            ] if family in active else [],
        }
        for family in names
    }
    month_labels = ["Jul", "Jun", "May", "Apr", "Mar", "Feb", "Jan", "Dec", "Nov", "Oct", "Sep", "Aug"]
    ribbons = []
    for index, label in enumerate(month_labels):
        year = 2026 if index < 7 else 2025
        month = 7 - index if index < 7 else 19 - index
        ribbons.append({
            "year": year,
            "month": month,
            "month_label": label,
            "winners": {
                family: {
                    "name": names[family][0][0],
                    "day": date(year, month, 2),
                    "amount": names[family][0][1] + 40,
                    "days": 1,
                }
                for family in active
            },
        })
    return {
        "ytd_start": date(2026, 1, 1),
        "ytd_end": date(2026, 7, 10),
        "l30_start": date(2026, 6, 11),
        "l30_end": date(2026, 7, 10),
        "active_families": active,
        "families": families,
        "ribbons": ribbons,
        "current_goats": [
            {"label": f"{family.rstrip('s')} GOAT", "group": family,
             "name": names[family][0][0], "units": names[family][0][1] + 69, "day": date(2026, 7, 2)}
            for family in active
        ],
        "error_message": None,
    }


def _write(client: TestClient, filename: str, url: str, payload: dict) -> None:
    _http_cache.invalidate_today_cache()
    with patch("zira_dashboard.routes.new_leaderboard._leaderboard_payload", lambda today: payload):
        response = client.get(url)
    response.raise_for_status()
    html = response.text.replace('href="/static/', 'href="static/').replace('src="/static/', 'src="static/')
    (OUT / filename).write_text(html, encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copytree(STATIC, OUT / "static", dirs_exist_ok=True)
    client = TestClient(app)
    junior = _payload(["Juniors"])
    future = _payload(["Juniors", "Woodpecker", "Hand Build"])
    _write(client, "dashboard-junior-only.html", "/new-leaderboard", junior)
    _write(client, "tv-dark-junior-only.html", "/tv/new-leaderboard?theme=dark", junior)
    _write(client, "tv-dark-three-families.html", "/tv/new-leaderboard?theme=dark", future)
    _write(client, "tv-light-three-families.html", "/tv/new-leaderboard?theme=light", future)
    print(OUT.resolve())


if __name__ == "__main__":
    main()
