# Odoo Integration Setup

The People Matrix pulls from Odoo (`hr_skills` module). Source of truth is
Odoo; the dashboard caches in `roster.json` with a 1-hour TTL.

## Required env vars (Railway)

- `ODOO_URL` — base URL like `https://gruber-pallets.odoo.com` (no `/odoo`)
- `ODOO_DB` — database name (e.g. `Production`)
- `ODOO_LOGIN` — username (email)
- `ODOO_API_KEY` — API key from Odoo Settings → Users → Account Security
  → New API Key

Set in Railway → Variables. Never commit.

## Skill type setup in Odoo

The dashboard reads two skill types: **"Production"** and **"Supervisor"**.
Skills under those types become matrix columns. Levels under each type
are bucketed to 0–3 by rank (lowest level = 0, highest = 3).

To add a column to the matrix: add a skill to one of those types in Odoo.

To add a level: add a level to the type in Odoo. The bucket math
re-distributes 0–3 across all levels in rank order.

## First-time migration

After env vars are set, run the schedule-name migration once:

```bash
python -m scripts.migrate_schedule_names_to_odoo
```

This pulls Odoo employees, proposes a mapping from current local names to
Odoo names, and on your confirmation, rewrites every file under
`schedules/`. The original schedules are backed up to `schedules.bak/`.

## Routine refresh

The matrix auto-refreshes once an hour on first page load. Click
**"Refresh from Odoo"** at the top of the matrix to force an immediate
sync.

## What if Odoo is down?

The dashboard serves the last-cached `roster.json` and shows a warning
banner. Refresh button retries on demand.
