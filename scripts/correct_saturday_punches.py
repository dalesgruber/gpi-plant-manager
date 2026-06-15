#!/usr/bin/env python3
"""One-shot correction for the 2026-06-13 (Saturday) missed-punch-out incident.

Auto-lunch re-clocked 8 workers in at the end of their lunch break; none of
those afternoon records were closed by the employee, so the midnight sweep
closed them at 00:00 and flagged them. They actually worked their full shift.
This sets each afternoon record's check-out to the REAL departure time Dale
confirmed, and resolves the missed-punch flag -- exactly what the manager
"Missed Punch Out" modal does (odoo_client.clock_out + missed_punch_out.correct).

Idempotent: a record whose flag is already resolved is skipped. Validates
check_in < corrected_ts <= auto_closed_at before writing (same bounds the
correct route enforces).

Run from the project root (env injected by railway):
    railway run --service web <venv-python> -m scripts.correct_saturday_punches
Add --dry-run to print the plan without writing.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ROOT / ".env", override=False)
except ImportError:
    pass

# attendance_id -> (real local punch-out HH:MM, worker name for the log).
# Afternoon records from the Jun 13 diagnostic. Morning records were already
# closed correctly by auto-lunch at 10:00 and are left untouched.
CORRECTIONS = {
    1919: ("12:00", "Alejandro Velazquez"),
    1921: ("12:00", "Carlos Jimenez"),
    1922: ("12:00", "Gerardo Vergara"),
    1923: ("12:00", "Iban Penaloza"),
    1924: ("12:00", "Jesus Martinez"),
    1920: ("12:00", "Fausto Jimenez"),
    1925: ("11:40", "Lauro Benitez"),
    1918: ("13:00", "Juan Delgado"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    args = ap.parse_args()

    from zira_dashboard import db, shift_config, missed_punch_out, odoo_client
    site = shift_config.SITE_TZ
    db.init_pool()

    print(f"Saturday punch-out correction  (dry_run={args.dry_run})\n")
    ok = skipped = failed = 0
    for att_id, (hhmm, name) in CORRECTIONS.items():
        try:
            flag = missed_punch_out.get_unresolved(att_id)
            if flag is None:
                print(f"  SKIP  {name:<22} att={att_id}: no unresolved flag "
                      f"(already corrected, or not flagged)")
                skipped += 1
                continue
            check_in = flag["check_in"]
            auto_closed_at = flag["auto_closed_at"]
            day = check_in.astimezone(site).date()
            hh, mm = (int(x) for x in hhmm.split(":"))
            corrected_ts = datetime.combine(day, time(hh, mm), tzinfo=site)

            # Same validation the correct route enforces.
            if not (check_in < corrected_ts <= auto_closed_at):
                print(f"  FAIL  {name:<22} att={att_id}: {corrected_ts} out of bounds "
                      f"(check_in={check_in.astimezone(site)}, "
                      f"midnight={auto_closed_at.astimezone(site)})")
                failed += 1
                continue

            print(f"  {'PLAN' if args.dry_run else 'FIX '}  {name:<22} att={att_id}: "
                  f"check_out {auto_closed_at.astimezone(site):%H:%M} -> "
                  f"{corrected_ts:%H:%M} {corrected_ts.tzname()}")
            if not args.dry_run:
                odoo_client.clock_out(att_id, corrected_ts)   # overwrite check_out in Odoo
                missed_punch_out.correct(att_id, corrected_ts)  # resolve the flag
            ok += 1
        except Exception as e:  # noqa: BLE001 — one record never stops the rest
            print(f"  FAIL  {name:<22} att={att_id}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\nDone. corrected={ok} skipped={skipped} failed={failed}"
          f"{'  (dry run — nothing written)' if args.dry_run else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
