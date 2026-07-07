"""Render the recycling dashboard to static HTML for cross-resolution QA.

Renders the REAL template through the app (TestClient) with a representative
"busy" data fixture (6 dismantlers + repairs + downtime + a full day of
15-min progress buckets) and an "empty/weekend" fixture, for the editor view
and both TV themes. Output goes to scripts/_preview_out/ with a `static`
symlink to the real static assets, ready to serve with `python -m http.server`.

Run:
    DATABASE_URL='postgresql://postgres:@/postgres?host=<...>/pgdata_review' \
    ZIRA_API_KEY=test .venv/bin/python scripts/preview_recycling.py
Then serve + browse (see .claude/launch.json 'recycling-preview').
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AUTH_DISABLED", "1")
os.environ.setdefault("SESSION_SECRET", "preview-secret-32-bytes-of-data!!!!")
os.environ.setdefault("ZIRA_API_KEY", "preview-dummy")

from fastapi.testclient import TestClient  # noqa: E402

from zira_dashboard.app import app  # noqa: E402
from zira_dashboard.routes import departments  # noqa: E402
from zira_dashboard.stations import Station  # noqa: E402

OUT = Path(__file__).parent / "_preview_out"


def _buckets(hi_lo):
    """15-min progress buckets 07:00-15:00 (32 buckets). hi_lo scales actuals."""
    out = []
    for i in range(32):
        hh = 7 + i // 4
        mm = (i % 4) * 15
        actual = 0 if hi_lo == 0 else int(9 + (i % 5) + hi_lo)
        out.append({
            "label": f"{hh}:{mm:02d}",
            "actual": actual,
            "target": 12,
            "in_progress": i == 31,
        })
    return out


def _busy_day(d, now, is_today_d, align_to_standard=False):
    dnames = [f"Dismantler {i}" for i in range(1, 7)]
    rnames = [f"Repair-{i}" for i in range(1, 5)]
    per_units = {**{n: 40 + i * 7 for i, n in enumerate(dnames)},
                 **{n: 55 + i * 5 for i, n in enumerate(rnames)}}
    per_dt = {**{n: (i * 6) % 25 for i, n in enumerate(dnames)},
              **{n: (i * 4) % 20 for i, n in enumerate(rnames)}}
    per_exp = {n: 60.0 for n in per_units}
    per_cat = {**{n: "Dismantler" for n in dnames}, **{n: "Repair" for n in rnames}}
    per_obj = {n: Station(meter_id=f"m{n}", name=n, category=per_cat[n], cell="Recycling")
               for n in per_units}
    who = {**{n: f"Operator {chr(65+i)}" for i, n in enumerate(dnames)},
           **{n: f"Operator {chr(75+i)}" for i, n in enumerate(rnames)}}
    return {
        "total_units": sum(per_units.values()),
        "total_downtime": sum(per_dt.values()),
        "elapsed": 360, "available": 360 * len(per_units),
        "uptime_minutes": 360 * len(per_units) - sum(per_dt.values()),
        "total_man_hours": 60.0, "total_recycling_people": 10,
        "per_wc_units": per_units, "per_wc_downtime": per_dt,
        "per_wc_expected": per_exp, "per_wc_who": who,
        "per_wc_state": {n: "working" for n in per_units},
        "dism_buckets": _buckets(3), "repair_buckets": _buckets(1),
        "shift_start_label": "7:00 AM",
        "schedule_assignments": {n: [who[n]] for n in per_units},
        "active_wc_names": set(per_units.keys()),
        "per_wc_category": per_cat, "per_wc_station_obj": per_obj,
    }


def _empty_day(d, now, is_today_d, align_to_standard=False):
    return {
        "total_units": 0, "total_downtime": 0, "elapsed": 0, "available": 0,
        "uptime_minutes": 0, "total_man_hours": 0.0, "total_recycling_people": 0,
        "per_wc_units": {}, "per_wc_downtime": {}, "per_wc_expected": {},
        "per_wc_who": {}, "per_wc_state": {},
        "dism_buckets": [], "repair_buckets": [],
        "shift_start_label": "7:00 AM", "schedule_assignments": {},
        "active_wc_names": set(), "per_wc_category": {}, "per_wc_station_obj": {},
    }


def _render(client, url):
    r = client.get(url)
    assert r.status_code == 200, (url, r.status_code, r.text[:500])
    return r.text


def main():
    OUT.mkdir(exist_ok=True)
    # `static` symlink so root-absolute /static/... refs resolve when served.
    link = OUT / "static"
    real_static = Path(__file__).resolve().parent.parent / "src/zira_dashboard/static"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(real_static)

    client = TestClient(app)
    variants = [
        ("editor_busy.html", _busy_day, "/recycling"),
        ("editor_empty.html", _empty_day, "/recycling"),
        ("tv_dark_busy.html", _busy_day, "/tv/recycling?theme=dark"),
        ("tv_light_busy.html", _busy_day, "/tv/recycling?theme=light"),
        ("tv_dark_empty.html", _empty_day, "/tv/recycling?theme=dark"),
    ]
    for fname, fixture, url in variants:
        with patch.object(departments, "_recycling_day_data", fixture):
            # bypass the per-variant response cache between renders
            from zira_dashboard import _http_cache
            _http_cache.invalidate_all_cache()
            html = _render(client, url)
        (OUT / fname).write_text(html)
        print("wrote", OUT / fname, len(html), "bytes")

    # Operator dashboard (/wc/{slug}) shares recycling.css — render one (with
    # whatever the DB holds) so base-CSS changes can be eyeballed there too.
    from zira_dashboard import _http_cache
    op = client.get("/operator", follow_redirects=False)
    slug = op.headers.get("location", "/wc/repair-1").split("/wc/")[-1]
    for fname, url in [("op_editor.html", f"/wc/{slug}"),
                       ("op_tv_dark.html", f"/tv/wc/{slug}?theme=dark")]:
        _http_cache.invalidate_all_cache()
        html = _render(client, url)
        (OUT / fname).write_text(html)
        print("wrote", OUT / fname, len(html), "bytes")


if __name__ == "__main__":
    main()
