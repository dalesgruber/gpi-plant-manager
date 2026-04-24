# Zira API Capability Probe

A Python toolkit that probes the Zira.us public API and writes a human-readable
capability report to `CAPABILITY_REPORT.md`. Documented in
`docs/superpowers/specs/2026-04-24-zira-api-capability-probe-design.md`.

## Setup

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in your `ZIRA_API_KEY`
(created in the Zira site under the **Applications** tab).

Open `config.yaml` and replace every `REPLACE_WITH_*` value with the IDs you
copied from the Zira UI:

- **`test_data_source.meter_id`** — a small "API Test DS" you create in Zira's
  Data Sources tab. Give it two metrics: one number, one text.
- **`test_data_source.metrics.number` / `.text`** — metric IDs from inside
  that test DS (edit the DS, click the copy icon next to each metric).
- **`read_targets.data_sources[].meter_id`** — one or two existing
  production data sources to read from (no writes land here).
- **`read_targets.channels[].channel_id`** — one or two channel IDs for
  aggregated analytics.

## Running

```bash
python -m zira_probe.runner               # full probe suite
python -m zira_probe.runner --auth-only   # just verify the API key
python -m zira_probe.runner --skip-writes # reads + undocumented only
```

Outputs:

- `CAPABILITY_REPORT.md` — human-readable summary by category.
- `results/*.json` — raw per-probe request/response logs (API key redacted).

## Tests

```bash
pytest -v
```

Unit tests cover `ZiraClient` (mocked HTTP), `Config` loading, `ProbeResult`
and the raw-log writer, and the report renderer. Probes themselves run
against the live Zira API and are validated by reviewing the generated
report.
