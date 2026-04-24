# Zira API Capability Probe — Design

**Date:** 2026-04-24
**Status:** Approved (brainstorming → implementation planning)

## Context

Gruber Pallets is evaluating Zira.us vision AI telematics as a data source for
shop-floor productivity data. Before committing to Zira as a source (or
participation point) for future custom apps, we need to know concretely what
the Zira public API can do: what's readable, what's writable, and how
restrictive each direction is.

The deliverable is **durable capability knowledge**, not a one-off sync. Odoo
ERP is mentioned throughout the broader company roadmap as one possible
downstream, but is **out of scope for this project** — Zira→Odoo is a follow-up
once we know what Zira actually offers.

## Goals

1. Enumerate every reachable Zira API endpoint — documented and undocumented.
2. For each endpoint, record: request shape, response shape, observed limits,
   error codes, validation behavior.
3. Prove a round-trip write-then-read on a sandbox data source.
4. Produce a human-readable capability report that future custom apps (or a
   future Zira→Odoo integration) can plan against.
5. Leave behind a reusable `ZiraClient` Python module as the starting point for
   those future apps.

## Non-Goals

- Odoo integration of any kind.
- A scheduled or production sync.
- Zira features beyond the public API (forms, triggers, documents, tasks,
  people, dashboards are out unless an undocumented endpoint surfaces them).
- A polished CLI or UI around the probe.

## What We Know About the Zira API

From the Zira.us API Guide PDF (dated 2026-04-24 download, v1.0):

- **Base URL:** `https://api.zira.us/public/`
- **Auth:** `X-API-Key` header; key issued via the "Applications" tab in the
  Zira site.
- **Documented endpoints:**
  - `GET /reading?meterId=...&endTime=...[&startTime=...&limit=...&lastValue=...]`
    — raw data-source or form readings. Paginated via `lastValue`.
  - `GET /channels/{channel_id}/analysis?interval=...&fromTime=...&toTime=...`
    — aggregated channel analytics. `interval` is one of
    `"5 minutes"`, `"1 hours"`, `"1 days"`, `"1 weeks"`.
  - `POST /reading/ids/` — append readings to an **existing data source**
    (works for data sources, *not* forms).
- **Documented error code:** `02-001` — too many points requested; reduce
  time range or widen interval.
- **Undocumented hint:** the guide references a "Get Data-Source API" for
  programmatic metricId retrieval but does not document the route. Probing
  for it is part of this project.

### Immediate implication for write scope

The documented write surface only **appends readings to existing data
sources**. It cannot create or modify the data source itself, its metric
schema, channels, forms, or users. This is already a substantive answer to
"how free or limiting is writing" — the design reflects that by probing what
*does* work at a fine grain rather than assuming a broad write surface exists.

## Architecture

### Language and runtime

Python 3.11+. Rationale: matches the Zira doc's own example code, is
Odoo's native language for the eventual follow-up, and fits the Claude Code
editing workflow.

### Project layout

```
zira/
├── .env                       # gitignored; holds ZIRA_API_KEY, ZIRA_BASE_URL
├── .env.example               # committed template
├── .gitignore                 # excludes .env, results/, __pycache__, .venv
├── config.yaml                # IDs collected from the Zira UI — safe to commit
├── pyproject.toml             # deps: requests, pyyaml, python-dotenv
├── README.md                  # how to set up and run
├── docs/
│   └── superpowers/specs/     # this document and future specs
├── src/
│   └── zira_probe/
│       ├── __init__.py
│       ├── client.py          # ZiraClient — the reusable module
│       ├── runner.py          # entry point: loads config, runs every probe
│       ├── report.py          # writes CAPABILITY_REPORT.md from ProbeResults
│       └── probes/
│           ├── __init__.py
│           ├── reads.py
│           ├── writes_happy.py
│           ├── writes_error_surface.py
│           └── undocumented.py
├── tests/
│   └── test_client.py         # unit tests for ZiraClient (mocked HTTP)
├── results/                   # raw per-probe JSON logs (gitignored)
└── CAPABILITY_REPORT.md       # auto-generated; the durable deliverable
```

### Secrets and config

Secrets live in `.env` (gitignored) and are loaded with `python-dotenv`.

- `ZIRA_API_KEY` — the UUID-style key from the Applications tab.
- `ZIRA_BASE_URL` — defaults to `https://api.zira.us/public/` but overridable.

Non-secret IDs live in `config.yaml` (committed), with this shape:

```yaml
test_data_source:
  meter_id: "<from Zira UI>"
  metrics:
    number: "<metricId of the number field>"
    text:   "<metricId of the text field>"

read_targets:
  data_sources:
    - meter_id: "<real DS id #1>"
      label: "Shop floor DS 1"
    - meter_id: "<real DS id #2>"
      label: "Shop floor DS 2"
  channels:
    - channel_id: "<channel id #1>"
      label: "Channel 1"

probe_settings:
  read_window_days: 7
  undocumented_timeout_seconds: 10
```

### Entry point

```
python -m zira_probe.runner              # full suite
python -m zira_probe.runner --auth-only  # preflight: verify API key only
python -m zira_probe.runner --skip-writes  # reads + undocumented only
```

### Data flow

```
.env + config.yaml
      ↓
runner.py loads both, constructs ZiraClient
      ↓
runner invokes each probe function:
    probe(client, config) → ProbeResult
      ↓
ProbeResults aggregated in a list; also each raw request+response
written to results/{probe_name}_{timestamp}.json
      ↓
report.py renders ProbeResults → CAPABILITY_REPORT.md
```

A `ProbeResult` is a simple dataclass:

```python
@dataclass
class ProbeResult:
    name: str
    category: str            # "reads" | "writes_happy" | "writes_error_surface" | "undocumented"
    endpoint: str            # e.g. "GET /reading"
    status: str              # "success" | "expected_failure" | "unexpected_failure" | "skipped"
    request_summary: dict    # method, url, params/body (secrets redacted)
    response_summary: dict   # status_code, headers_of_interest, body_excerpt
    observations: list[str]  # human-readable notes
    raw_log_path: str        # pointer to results/*.json
```

## Probe Catalog

### Reads — documented

| Probe | What it does | Success signal |
|---|---|---|
| `read_ds_single_window` | `GET /reading` on each real DS over a 7-day window | 200 + ≥1 reading returned |
| `read_ds_paginated` | `GET /reading` with a small `limit`, follow `lastValue` for 2 pages | Pagination works; no duplicate keys across pages |
| `read_ds_empty_window` | `GET /reading` over a window we expect to be empty (1 minute in the far past) | 200 + empty list (not an error) |
| `read_channel_analysis_intervals` | `GET /channels/{id}/analysis` for each configured channel at all four intervals | 200 for all four |
| `read_channel_analysis_too_many_points` | `GET /channels/{id}/analysis` with `5 minutes` interval over a 90-day window | Confirms error code `02-001` |

### Writes — happy path (test DS only)

| Probe | What it does | Success signal |
|---|---|---|
| `write_single_number` | `POST /reading/ids/` with one reading, number metric only | 2xx; subsequent read returns the value |
| `write_single_text` | `POST /reading/ids/` with one reading, text metric only | 2xx; read-back returns the text |
| `write_both_metrics` | `POST /reading/ids/` with one reading, both metrics in `values` | 2xx; read-back returns both |
| `write_batch_5` | `POST /reading/ids/` with 5 readings, unique timestamps | 2xx; all 5 readable |

Each write uses a timestamp with a recognizable prefix (e.g., `2099-...` or a
designated marker metric value) so the test data is easy to spot and ignore in
the Zira UI.

### Writes — error surface (characterization, not happy path)

All against the test DS. Each probe's "success" is that the API rejects the
request in an informative way.

| Probe | Input | What we learn |
|---|---|---|
| `write_bad_meter_id` | meterId that doesn't exist | Error shape for unknown DS |
| `write_bad_metric_id` | unknown metricId inside `values` | Whether unknown metrics are ignored, rejected, or partially accepted |
| `write_wrong_value_type` | text in the number metric, number in the text metric | Type coercion or rejection behavior |
| `write_missing_meter_id` | payload missing `meterId` | Required-field validation |
| `write_missing_timestamp` | payload missing `timestamp` | Required-field validation |
| `write_duplicate_timestamp` | two payloads with the same DS + timestamp | Dedup / overwrite / conflict behavior |
| `write_future_timestamp` | timestamp 10 years in the future | Any time-range guardrails |
| `write_large_batch` | 500 readings in a single POST | Batch size limits |
| `write_empty_body` | `POST /reading/ids/` with `[]` | Empty payload handling |

### Undocumented endpoint probing

Each probe attempts a plausible REST path, records status and body, and
concludes "exists / 404 / auth error / something else." We are looking for
anything that responds with structured data that isn't a 404 or generic
gateway error.

| Path | Method | Rationale |
|---|---|---|
| `/data-sources` | GET | The guide hints at a "Get Data-Source API" |
| `/data-sources/{id}` | GET | Same |
| `/meters` | GET | `meterId` terminology; common REST pluralization |
| `/meters/{id}` | GET | Same |
| `/channels` | GET | Lists channels |
| `/channels/{id}` | GET | Get single channel metadata |
| `/forms` | GET | Forms are readable per the docs but the list endpoint isn't documented |
| `/applications` | GET | The Applications tab clearly drives API keys |
| `/metrics/{id}` | GET | Individual metric metadata |
| `/` (root) | GET, OPTIONS | Sometimes returns a capabilities descriptor |
| `/health`, `/ping`, `/version` | GET | Conventional diagnostics |

The undocumented probe set is deliberately short — we're not fuzzing, we're
trying likely patterns.

## Outputs

### `CAPABILITY_REPORT.md`

Human-readable, committed to the repo, grouped by category. Each entry looks
like:

```markdown
### `GET /reading`

**Status:** ✅ Works

**Request example**
`GET https://api.zira.us/public/reading?meterId=14995&endTime=2026-04-24T00:00:00Z`

**Response excerpt**
```json
{ ... }
```

**Observations**
- Pagination via `lastValue` works; no duplicate keys observed across pages.
- Empty window returns `[]` with status 200, not an error.
- Default page size appears to be N (observed).
```

The report ends with an **Open Questions** section listing anything the probe
couldn't answer (e.g., "we couldn't determine if `/data-sources` exists — it
returned 403 rather than 404, which is suggestive").

### `results/*.json`

Raw log per probe: full request URL + headers (API key redacted to last 4
chars) + full response status/headers/body, timestamped filename. This is the
forensic record for anyone who wants to look past the summary.

## Error Handling

- Every probe wraps its HTTP call in try/except. Exceptions become recorded
  data, never crashes.
- 15s HTTP timeout. Exactly one retry on `requests.ConnectionError` or
  `requests.Timeout`; no retry on any HTTP status (4xx and 5xx responses are
  meaningful data and retrying would mask them).
- A probe's failure never stops the suite — runner always iterates through
  every probe and then renders the report.
- Secrets are read from `.env` only. Logs redact `X-API-Key` to last 4 chars.

## Testing

- `ZiraClient` has unit tests in `tests/test_client.py` using the `responses`
  library to mock HTTP. Coverage: correct method/URL/headers/params/body for
  each of the three documented endpoints, and correct parsing of each shape.
- No recorded HTTP fixtures (VCR) — the whole point of the probe suite is
  exercising live behavior.
- The runner exposes `--auth-only` as a fast preflight that hits one read
  endpoint to confirm the API key works before running the full suite.

## Open Questions for Implementation

None that block the plan. The implementation plan (next step) should handle:

1. Exact pyproject.toml dependency versions.
2. Whether to pin the undocumented-probe list to only the table above, or
   accept additional paths from `config.yaml`.
3. Whether `results/` should be per-run (timestamped subdirectories) or flat.
