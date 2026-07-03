#!/usr/bin/env python3
"""Normalize every Odoo timezone field to the plant's zone (America/Chicago).

Odoo defaults new employees/resources to UTC, which corrupts how it computes
hr.leave datetimes and attendance for a Central-time plant (see
docs/superpowers/specs/2026-07-02-partial-day-time-off-display-design.md and
the partial-day-time-off memory). This sweeps res.users, resource.calendar,
and resource.resource (all incl. archived; hr.employee.tz follows its
resource) to a single canonical zone. res.company has no tz field in Odoo
(timezone is per-user), so it is intentionally not touched.

Dry-run by default; pass --apply to write. Reversible: --apply prints and
saves every prior (model, id, old_tz) to a backup JSON.

Run with Railway-injected Odoo creds:
    railway run python -m scripts.normalize_odoo_timezones          # preview
    railway run python -m scripts.normalize_odoo_timezones --apply  # write
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ROOT / ".env", override=False)
except ImportError:
    pass

CANON = "America/Chicago"

# (model, domain, label) — domains cover archived records too, since an
# archived person/resource can be reactivated and must not carry a stale tz.
# hr.employee is verified but never written: its tz is a stored related field
# off resource_id.tz, so writing the resource propagates automatically.
_ANY_ACTIVE = ["|", ("active", "=", True), ("active", "=", False)]
TARGETS = [
    ("res.users", _ANY_ACTIVE),
    ("resource.calendar", _ANY_ACTIVE),
    ("resource.resource", _ANY_ACTIVE),
]


def rows_to_normalize(rows: list[dict[str, Any]], canon: str = CANON) -> list[dict[str, Any]]:
    """Pure: subset of ``rows`` whose ``tz`` isn't already ``canon``.

    Treats Odoo's ``False``/``None`` empty-tz the same as any other
    non-canonical value — a blank tz on a template/system user still gets set
    so the whole database reads one zone."""
    return [r for r in rows if r.get("tz") != canon]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="perform the writes (default: dry-run preview)")
    ap.add_argument("--backup", default=str(ROOT / "tz_backup.json"),
                    help="where to write the reversibility backup JSON")
    args = ap.parse_args()

    from zira_dashboard import odoo_client

    backup: dict[str, dict[str, Any]] = {}
    total = 0
    for model, domain in TARGETS:
        rows = odoo_client.execute(
            model, "search_read", domain, fields=["id", "name", "tz"])
        to_fix = rows_to_normalize(rows)
        backup[model] = {str(r["id"]): r.get("tz") for r in to_fix}
        total += len(to_fix)
        print(f"### {model}: {len(rows)} total, {len(to_fix)} to normalize -> {CANON}")
        for r in to_fix:
            print(f"    id={r['id']:<4} {str(r.get('name'))[:36]:36} "
                  f"{str(r.get('tz')):16} -> {CANON}")
        if args.apply and to_fix:
            odoo_client.execute(model, "write", [r["id"] for r in to_fix], {"tz": CANON})
            print(f"    WROTE {len(to_fix)} {model} records")

    Path(args.backup).write_text(json.dumps(backup, indent=2))
    print(f"\nbackup ({total} records) -> {args.backup}")

    if not args.apply:
        print("\nDRY-RUN — re-run with --apply to write.")
        return 0

    print("\n=== VERIFY ===")
    ok = True
    for model, domain in TARGETS + [("hr.employee", [("active", "=", True)])]:
        rows = odoo_client.execute(
            model, "search_read", domain, fields=["id", "name", "tz"])
        bad = [r for r in rows if r.get("tz") != CANON]
        print(f"    {model}: {len(rows)} records, non-Central={len(bad)}")
        ok = ok and not bad
    print("\nRESULT:", "ALL CENTRAL" if ok else "SOME REMAIN")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
