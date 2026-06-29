#!/usr/bin/env python3
"""List employees whose Odoo work schedule conflicts with the plant's workdays.

Read-only. Thin CLI over zira_dashboard.calendar_conflicts. Run from a laptop
via `railway run` (injects Odoo creds; Postgres is optional enrichment):

    railway run python scripts/diagnose_odoo_calendar_conflicts.py [--all]

See docs/superpowers/specs/2026-06-29-calendar-conflict-monitor-design.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zira_dashboard.calendar_conflicts import (  # noqa: E402
    classify_conflict,  # re-exported for backwards-compat imports
    fmt_days,
    gather_rows,
    plant_weekdays,
)

_BUCKETS = [
    ("no_calendar", "No Odoo work schedule"),
    ("flexible", "Flexible / no fixed hours"),
    ("missing_days", "Calendar missing plant workday(s)"),
]

__all__ = ["classify_conflict", "main"]


def _print_report(rows, weekdays, show_all: bool, notes=()) -> None:
    for note in notes:
        print(f"NOTE: {note}")
    if notes:
        print()
    conflicts = [r for r in rows if r["verdict"] != "ok"]
    print(
        f"{len(conflicts)} of {len(rows)} employees have an Odoo work-schedule "
        f"conflict (plant runs {fmt_days(weekdays)})."
    )
    print()
    for key, title in _BUCKETS:
        group = sorted((r for r in rows if r["verdict"] == key), key=lambda r: r["name"].lower())
        if not group:
            continue
        print(f"{title} ({len(group)}):")
        for r in group:
            line = f"  • {r['name']} (id {r['odoo_id']}) · calendar {r['cal_name']!r}"
            if key == "missing_days":
                line += f" · covers {fmt_days(r['covered'])} · missing {fmt_days(r['missing'])}"
            print(line)
        print()
    if show_all:
        ok = sorted((r for r in rows if r["verdict"] == "ok"), key=lambda r: r["name"].lower())
        print(f"OK ({len(ok)}):")
        for r in ok:
            print(
                f"  • {r['name']} (id {r['odoo_id']}) · calendar {r['cal_name']!r} "
                f"· covers {fmt_days(r['covered'])}"
            )


def _parse_args(argv):
    ap = argparse.ArgumentParser(
        description="List employees whose Odoo work schedule conflicts with plant workdays."
    )
    ap.add_argument("--all", action="store_true", help="Also list employees whose calendar is fine.")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    weekdays, week_note = plant_weekdays()
    rows, notes = gather_rows(weekdays)
    _print_report(rows, weekdays, show_all=args.all, notes=([week_note] if week_note else []) + notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
