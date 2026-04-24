# Zira API Capability Probe — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python toolkit that probes Zira.us's public API, exercises reads/writes/error surfaces on a sandbox data source plus undocumented endpoint guesses, and emits `CAPABILITY_REPORT.md` — a durable artifact future custom apps can plan against.

**Architecture:** A small Python 3.11+ package under `src/zira_probe/`. A reusable `ZiraClient` wraps the three documented HTTP endpoints plus a generic request method for undocumented probing. A collection of probe functions in `src/zira_probe/probes/` exercise each capability and return `ProbeResult` dataclasses. A runner orchestrates the probes against a user-supplied `config.yaml` + `.env`; a report module renders results to `CAPABILITY_REPORT.md`.

**Tech Stack:** Python 3.11+, `requests`, `pyyaml`, `python-dotenv`, `pytest`, `responses` (HTTP mocking for unit tests).

---

## File Structure

**Created by this plan:**

```
zira/
├── .gitignore                                  Task 1
├── .env.example                                Task 1
├── pyproject.toml                              Task 1
├── config.yaml                                 Task 1  (template; user fills in IDs)
├── README.md                                   Task 14
├── src/zira_probe/
│   ├── __init__.py                             Task 1
│   ├── config.py                               Task 2
│   ├── results.py                              Task 3
│   ├── client.py                               Tasks 4–7
│   ├── report.py                               Task 12
│   ├── runner.py                               Task 13
│   └── probes/
│       ├── __init__.py                         Task 8
│       ├── reads.py                            Task 8
│       ├── writes_happy.py                     Task 9
│       ├── writes_error_surface.py             Task 10
│       └── undocumented.py                     Task 11
└── tests/
    ├── __init__.py                             Task 2
    ├── test_config.py                          Task 2
    ├── test_results.py                         Task 3
    ├── test_client.py                          Tasks 4–7
    └── test_report.py                          Task 12
```

**Responsibility per file:**

- `config.py` — load `.env` (secrets) and `config.yaml` (IDs), return a typed `Config` object.
- `results.py` — `ProbeResult` dataclass + helper that writes raw request/response JSON to `results/*.json` with `X-API-Key` redacted.
- `client.py` — `ZiraClient` with one method per documented endpoint plus a generic `request()` for undocumented probing.
- `probes/reads.py`, `probes/writes_happy.py`, `probes/writes_error_surface.py`, `probes/undocumented.py` — one function per probe in the spec's probe catalog; each returns a `ProbeResult`.
- `runner.py` — CLI entry. Loads config, iterates probes, catches exceptions per probe, calls `report.render()`.
- `report.py` — renders a list of `ProbeResult`s to `CAPABILITY_REPORT.md`.

---

### Task 1: Project scaffolding

**Files:**
- Create: `C:\Users\dale.gruber\Projects\zira\.gitignore`
- Create: `C:\Users\dale.gruber\Projects\zira\.env.example`
- Create: `C:\Users\dale.gruber\Projects\zira\pyproject.toml`
- Create: `C:\Users\dale.gruber\Projects\zira\config.yaml`
- Create: `C:\Users\dale.gruber\Projects\zira\src\zira_probe\__init__.py`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
# Secrets
.env

# Probe output
results/
CAPABILITY_REPORT.md

# Python
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/
.venv/
venv/
.coverage
htmlcov/
dist/
build/
*.egg-info/

# IDE
.vscode/
.idea/
```

Note: `CAPABILITY_REPORT.md` is gitignored during development; re-add it explicitly once you want to commit a report snapshot.

- [ ] **Step 2: Create `.env.example`**

```
# Copy this file to .env and fill in your values.
# .env is gitignored; never commit your real key.

ZIRA_API_KEY=00000000-0000-0000-0000-000000000000
ZIRA_BASE_URL=https://api.zira.us/public/
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "zira-probe"
version = "0.1.0"
description = "Capability probe for the Zira.us public API"
requires-python = ">=3.11"
dependencies = [
    "requests>=2.31",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "responses>=0.24",
]

[tool.hatch.build.targets.wheel]
packages = ["src/zira_probe"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 4: Create `config.yaml`** (template — user fills in values from Zira UI before running)

```yaml
# Fill in the IDs by copying them from the Zira UI.
# "Share..." on a data source or channel reveals its ID.

test_data_source:
  meter_id: "REPLACE_WITH_TEST_DS_METER_ID"
  metrics:
    number: "REPLACE_WITH_NUMBER_METRIC_ID"
    text: "REPLACE_WITH_TEXT_METRIC_ID"

read_targets:
  data_sources:
    - meter_id: "REPLACE_WITH_REAL_DS_ID_1"
      label: "Shop floor DS 1"
  channels:
    - channel_id: "REPLACE_WITH_CHANNEL_ID_1"
      label: "Channel 1"

probe_settings:
  read_window_days: 7
  undocumented_timeout_seconds: 10
```

- [ ] **Step 5: Create `src/zira_probe/__init__.py`**

```python
"""Zira.us API capability probe."""

__version__ = "0.1.0"
```

- [ ] **Step 6: Verify folder structure**

Run: `ls -R src tests 2>&1 | head -30`
Expected: shows `src/zira_probe/__init__.py`

- [ ] **Step 7: Commit**

```bash
git add .gitignore .env.example pyproject.toml config.yaml src/zira_probe/__init__.py
git commit -m "Scaffold zira_probe project (pyproject, config template, gitignore)"
```

---

### Task 2: Config loader

**Files:**
- Create: `src/zira_probe/config.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Create `tests/__init__.py` (empty file — makes `tests` a package).

Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest

from zira_probe.config import Config, load_config


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_load_config_reads_env_and_yaml(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    _write(env_path, "ZIRA_API_KEY=key-abc123\nZIRA_BASE_URL=https://api.zira.us/public/\n")

    yaml_path = tmp_path / "config.yaml"
    _write(
        yaml_path,
        """
test_data_source:
  meter_id: "999"
  metrics:
    number: "1"
    text: "2"
read_targets:
  data_sources:
    - meter_id: "100"
      label: "Real DS 1"
  channels:
    - channel_id: "200"
      label: "Channel 1"
probe_settings:
  read_window_days: 7
  undocumented_timeout_seconds: 10
""",
    )

    config = load_config(env_path=env_path, yaml_path=yaml_path)

    assert isinstance(config, Config)
    assert config.api_key == "key-abc123"
    assert config.base_url == "https://api.zira.us/public/"
    assert config.test_ds_meter_id == "999"
    assert config.test_ds_number_metric == "1"
    assert config.test_ds_text_metric == "2"
    assert len(config.read_data_sources) == 1
    assert config.read_data_sources[0].meter_id == "100"
    assert config.read_channels[0].channel_id == "200"
    assert config.read_window_days == 7
    assert config.undocumented_timeout_seconds == 10


def test_load_config_missing_api_key_raises(tmp_path):
    env_path = tmp_path / ".env"
    _write(env_path, "ZIRA_BASE_URL=https://api.zira.us/public/\n")
    yaml_path = tmp_path / "config.yaml"
    _write(
        yaml_path,
        """
test_data_source:
  meter_id: "999"
  metrics:
    number: "1"
    text: "2"
read_targets:
  data_sources: []
  channels: []
probe_settings:
  read_window_days: 7
  undocumented_timeout_seconds: 10
""",
    )

    with pytest.raises(RuntimeError, match="ZIRA_API_KEY"):
        load_config(env_path=env_path, yaml_path=yaml_path)


def test_load_config_default_base_url(tmp_path):
    env_path = tmp_path / ".env"
    _write(env_path, "ZIRA_API_KEY=key-abc123\n")
    yaml_path = tmp_path / "config.yaml"
    _write(
        yaml_path,
        """
test_data_source:
  meter_id: "999"
  metrics:
    number: "1"
    text: "2"
read_targets:
  data_sources: []
  channels: []
probe_settings:
  read_window_days: 7
  undocumented_timeout_seconds: 10
""",
    )

    config = load_config(env_path=env_path, yaml_path=yaml_path)

    assert config.base_url == "https://api.zira.us/public/"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: 3 failures — `ModuleNotFoundError: No module named 'zira_probe.config'`

- [ ] **Step 3: Write `src/zira_probe/config.py`**

```python
"""Configuration loader: merges .env secrets with config.yaml structure."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://api.zira.us/public/"


@dataclass(frozen=True)
class ReadDataSource:
    meter_id: str
    label: str


@dataclass(frozen=True)
class ReadChannel:
    channel_id: str
    label: str


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    test_ds_meter_id: str
    test_ds_number_metric: str
    test_ds_text_metric: str
    read_data_sources: list[ReadDataSource] = field(default_factory=list)
    read_channels: list[ReadChannel] = field(default_factory=list)
    read_window_days: int = 7
    undocumented_timeout_seconds: int = 10


def load_config(
    env_path: Path | str = ".env",
    yaml_path: Path | str = "config.yaml",
) -> Config:
    env_path = Path(env_path)
    yaml_path = Path(yaml_path)

    load_dotenv(dotenv_path=env_path, override=False)

    api_key = os.environ.get("ZIRA_API_KEY")
    if not api_key:
        raise RuntimeError(
            f"ZIRA_API_KEY missing. Set it in {env_path} (see .env.example)."
        )

    base_url = os.environ.get("ZIRA_BASE_URL", DEFAULT_BASE_URL)

    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

    test_ds = raw["test_data_source"]
    targets = raw["read_targets"]
    settings = raw["probe_settings"]

    return Config(
        api_key=api_key,
        base_url=base_url,
        test_ds_meter_id=str(test_ds["meter_id"]),
        test_ds_number_metric=str(test_ds["metrics"]["number"]),
        test_ds_text_metric=str(test_ds["metrics"]["text"]),
        read_data_sources=[
            ReadDataSource(meter_id=str(d["meter_id"]), label=str(d["label"]))
            for d in targets.get("data_sources", [])
        ],
        read_channels=[
            ReadChannel(channel_id=str(c["channel_id"]), label=str(c["label"]))
            for c in targets.get("channels", [])
        ],
        read_window_days=int(settings["read_window_days"]),
        undocumented_timeout_seconds=int(settings["undocumented_timeout_seconds"]),
    )
```

- [ ] **Step 4: Install dependencies (one-time)**

Run: `pip install -e ".[dev]"`
Expected: successfully installs `zira-probe` + dev deps.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/zira_probe/config.py tests/__init__.py tests/test_config.py
git commit -m "Add Config loader with .env + config.yaml support"
```

---

### Task 3: ProbeResult dataclass + raw log writer

**Files:**
- Create: `src/zira_probe/results.py`
- Create: `tests/test_results.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_results.py`:

```python
import json
from pathlib import Path

from zira_probe.results import ProbeResult, redact_api_key, write_raw_log


def test_probe_result_defaults():
    result = ProbeResult(
        name="demo",
        category="reads",
        endpoint="GET /reading",
        status="success",
    )
    assert result.observations == []
    assert result.request_summary == {}
    assert result.response_summary == {}
    assert result.raw_log_path == ""


def test_redact_api_key_keeps_last_four():
    assert redact_api_key("abcd-efgh-ijkl-mnop-qrst-1234") == "****1234"


def test_redact_api_key_short_input_returns_asterisks():
    assert redact_api_key("xyz") == "****"


def test_write_raw_log_writes_file_and_returns_path(tmp_path):
    path = write_raw_log(
        results_dir=tmp_path,
        name="my_probe",
        request={"method": "GET", "url": "u", "headers": {"X-API-Key": "****1234"}},
        response={"status_code": 200, "body": {"ok": True}},
    )

    assert path.parent == tmp_path
    assert path.name.startswith("my_probe_")
    assert path.suffix == ".json"
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["request"]["method"] == "GET"
    assert data["response"]["status_code"] == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_results.py -v`
Expected: failures — `ModuleNotFoundError: No module named 'zira_probe.results'`

- [ ] **Step 3: Write `src/zira_probe/results.py`**

```python
"""ProbeResult dataclass and raw-log writer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ProbeResult:
    """Outcome of a single probe. See spec for semantics of `status`."""

    name: str
    category: str  # "reads" | "writes_happy" | "writes_error_surface" | "undocumented"
    endpoint: str
    status: str  # "success" | "expected_failure" | "unexpected_failure" | "skipped"
    request_summary: dict = field(default_factory=dict)
    response_summary: dict = field(default_factory=dict)
    observations: list[str] = field(default_factory=list)
    raw_log_path: str = ""


def redact_api_key(key: str) -> str:
    if len(key) < 4:
        return "****"
    return "****" + key[-4:]


def write_raw_log(
    results_dir: Path,
    name: str,
    request: dict,
    response: dict,
) -> Path:
    """Write a timestamped raw request/response log; return its path."""

    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = results_dir / f"{name}_{stamp}.json"
    payload = {
        "name": name,
        "timestamp": stamp,
        "request": request,
        "response": response,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_results.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_probe/results.py tests/test_results.py
git commit -m "Add ProbeResult dataclass and raw-log writer"
```

---

### Task 4: ZiraClient skeleton + `GET /reading`

**Files:**
- Create: `src/zira_probe/client.py`
- Create: `tests/test_client.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_client.py`:

```python
import pytest
import responses

from zira_probe.client import ZiraClient


@pytest.fixture
def client():
    return ZiraClient(api_key="test-key-1234", base_url="https://api.zira.us/public/")


@responses.activate
def test_get_readings_sends_api_key_header_and_params(client):
    responses.add(
        method=responses.GET,
        url="https://api.zira.us/public/reading",
        json=[{"id": "r1"}, {"id": "r2"}],
        status=200,
    )

    result = client.get_readings(meter_id="999", end_time="2026-04-24T00:00:00Z")

    assert result == [{"id": "r1"}, {"id": "r2"}]
    call = responses.calls[0]
    assert call.request.headers["X-API-Key"] == "test-key-1234"
    assert "meterId=999" in call.request.url
    assert "endTime=2026-04-24T00%3A00%3A00Z" in call.request.url


@responses.activate
def test_get_readings_passes_optional_params(client):
    responses.add(
        method=responses.GET,
        url="https://api.zira.us/public/reading",
        json=[],
        status=200,
    )

    client.get_readings(
        meter_id="999",
        end_time="2026-04-24T00:00:00Z",
        start_time="2026-04-17T00:00:00Z",
        limit=50,
        last_value="abc",
    )

    url = responses.calls[0].request.url
    assert "startTime=2026-04-17T00%3A00%3A00Z" in url
    assert "limit=50" in url
    assert "lastValue=abc" in url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_client.py -v`
Expected: failures — `ModuleNotFoundError: No module named 'zira_probe.client'`

- [ ] **Step 3: Write `src/zira_probe/client.py`**

```python
"""Thin HTTP client for the Zira.us public API.

Exposes one method per documented endpoint plus a generic `request()` method
used by the undocumented-probing suite.
"""

from __future__ import annotations

from typing import Any

import requests


class ZiraClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.zira.us/public/",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})

    def _url(self, path: str) -> str:
        return self.base_url + path.lstrip("/")

    def get_readings(
        self,
        meter_id: str,
        end_time: str,
        start_time: str | None = None,
        limit: int | None = None,
        last_value: str | None = None,
    ) -> Any:
        params: dict[str, str] = {"meterId": meter_id, "endTime": end_time}
        if start_time is not None:
            params["startTime"] = start_time
        if limit is not None:
            params["limit"] = str(limit)
        if last_value is not None:
            params["lastValue"] = last_value

        resp = self.session.get(
            self._url("reading"), params=params, timeout=self.timeout_seconds
        )
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_client.py -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_probe/client.py tests/test_client.py
git commit -m "Add ZiraClient skeleton + get_readings()"
```

---

### Task 5: `ZiraClient.get_channel_analysis`

**Files:**
- Modify: `src/zira_probe/client.py`
- Modify: `tests/test_client.py`

- [ ] **Step 1: Append failing tests to `tests/test_client.py`**

```python
@responses.activate
def test_get_channel_analysis_builds_url_and_params(client):
    responses.add(
        method=responses.GET,
        url="https://api.zira.us/public/channels/42301/analysis",
        json={"points": []},
        status=200,
    )

    result = client.get_channel_analysis(
        channel_id="42301",
        interval="1 days",
        from_time="2026-04-01T00:00:00Z",
        to_time="2026-04-10T00:00:00Z",
    )

    assert result == {"points": []}
    url = responses.calls[0].request.url
    assert "interval=1+days" in url or "interval=1%20days" in url
    assert "fromTime=2026-04-01T00%3A00%3A00Z" in url
    assert "toTime=2026-04-10T00%3A00%3A00Z" in url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_client.py::test_get_channel_analysis_builds_url_and_params -v`
Expected: FAIL — `AttributeError: 'ZiraClient' object has no attribute 'get_channel_analysis'`

- [ ] **Step 3: Add `get_channel_analysis` to `src/zira_probe/client.py`**

Append inside the `ZiraClient` class:

```python
    def get_channel_analysis(
        self,
        channel_id: str,
        interval: str,
        from_time: str,
        to_time: str,
    ) -> Any:
        params = {"interval": interval, "fromTime": from_time, "toTime": to_time}
        resp = self.session.get(
            self._url(f"channels/{channel_id}/analysis"),
            params=params,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_client.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_probe/client.py tests/test_client.py
git commit -m "Add ZiraClient.get_channel_analysis()"
```

---

### Task 6: `ZiraClient.add_readings` (POST)

**Files:**
- Modify: `src/zira_probe/client.py`
- Modify: `tests/test_client.py`

- [ ] **Step 1: Append failing test to `tests/test_client.py`**

```python
import json


@responses.activate
def test_add_readings_posts_json_payload(client):
    responses.add(
        method=responses.POST,
        url="https://api.zira.us/public/reading/ids/",
        json={"ok": True},
        status=200,
    )

    payload = [
        {
            "meterId": "3978",
            "timestamp": "2026-04-24T12:00:00Z",
            "values": [{"metricId": "6", "value": 0}],
        }
    ]
    result = client.add_readings(payload)

    assert result == {"ok": True}
    sent = responses.calls[0].request
    assert sent.headers["X-API-Key"] == "test-key-1234"
    assert sent.headers["Content-Type"] == "application/json"
    assert json.loads(sent.body) == payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_client.py::test_add_readings_posts_json_payload -v`
Expected: FAIL — `AttributeError: 'ZiraClient' object has no attribute 'add_readings'`

- [ ] **Step 3: Add `add_readings` to `src/zira_probe/client.py`**

Append inside the `ZiraClient` class:

```python
    def add_readings(self, readings: list[dict]) -> Any:
        resp = self.session.post(
            self._url("reading/ids/"),
            json=readings,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_client.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_probe/client.py tests/test_client.py
git commit -m "Add ZiraClient.add_readings()"
```

---

### Task 7: `ZiraClient.request` for undocumented probing

**Files:**
- Modify: `src/zira_probe/client.py`
- Modify: `tests/test_client.py`

- [ ] **Step 1: Append failing tests to `tests/test_client.py`**

```python
@responses.activate
def test_request_returns_response_object(client):
    responses.add(
        method=responses.GET,
        url="https://api.zira.us/public/data-sources",
        json={"items": []},
        status=200,
    )

    resp = client.request("GET", "data-sources")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


@responses.activate
def test_request_does_not_raise_on_4xx(client):
    responses.add(
        method=responses.GET,
        url="https://api.zira.us/public/does-not-exist",
        json={"error": "not found"},
        status=404,
    )

    resp = client.request("GET", "does-not-exist")

    assert resp.status_code == 404


@responses.activate
def test_request_passes_through_json_body(client):
    responses.add(
        method=responses.POST,
        url="https://api.zira.us/public/whatever",
        json={"ok": True},
        status=200,
    )

    client.request("POST", "whatever", json_body={"hello": "world"})

    import json as _json
    sent = responses.calls[0].request
    assert _json.loads(sent.body) == {"hello": "world"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_client.py -v`
Expected: 3 new failures — `AttributeError: 'ZiraClient' object has no attribute 'request'`

- [ ] **Step 3: Add `request` to `src/zira_probe/client.py`**

Append inside the `ZiraClient` class:

```python
    def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: Any = None,
        timeout_seconds: float | None = None,
    ) -> requests.Response:
        """Generic request for undocumented-endpoint probing.

        Never raises on 4xx/5xx — returns the response so probes can record
        the exact error shape.
        """

        return self.session.request(
            method=method.upper(),
            url=self._url(path),
            params=params,
            json=json_body,
            timeout=timeout_seconds or self.timeout_seconds,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_client.py -v`
Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_probe/client.py tests/test_client.py
git commit -m "Add ZiraClient.request() for undocumented probing"
```

---

### Task 8: Read probes

**Files:**
- Create: `src/zira_probe/probes/__init__.py`
- Create: `src/zira_probe/probes/reads.py`

No unit tests — probes are validated by running them against the real API. The client they depend on is already tested.

- [ ] **Step 1: Create `src/zira_probe/probes/__init__.py`**

```python
"""Probe functions. Each returns a ProbeResult."""
```

- [ ] **Step 2: Create `src/zira_probe/probes/reads.py`**

```python
"""Read probes against documented endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from zira_probe.client import ZiraClient
from zira_probe.config import Config
from zira_probe.results import ProbeResult, redact_api_key, write_raw_log


def _iso_utc(dt: datetime) -> str:
    """Zira-friendly ISO 8601 with trailing Z, millisecond precision."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _safe_call(fn, *args, **kwargs):
    """Run `fn`; return (response_or_none, error_dict_or_none)."""
    try:
        return fn(*args, **kwargs), None
    except requests.HTTPError as exc:
        resp = exc.response
        return None, {
            "type": "HTTPError",
            "status_code": resp.status_code if resp is not None else None,
            "body": resp.text if resp is not None else str(exc),
        }
    except Exception as exc:
        return None, {"type": type(exc).__name__, "message": str(exc)}


def probe_read_ds_single_window(
    client: ZiraClient, config: Config, results_dir: Path
) -> list[ProbeResult]:
    out: list[ProbeResult] = []
    now = datetime.now(timezone.utc)
    end_time = _iso_utc(now)
    start_time = _iso_utc(now - timedelta(days=config.read_window_days))

    for ds in config.read_data_sources:
        name = f"read_ds_single_window[{ds.label}]"
        request_summary = {
            "method": "GET",
            "url": f"{client.base_url}reading",
            "params": {
                "meterId": ds.meter_id,
                "startTime": start_time,
                "endTime": end_time,
            },
            "headers": {"X-API-Key": redact_api_key(client.api_key)},
        }

        data, err = _safe_call(
            client.get_readings,
            meter_id=ds.meter_id,
            end_time=end_time,
            start_time=start_time,
        )

        if err is not None:
            response_summary = {"error": err}
            status = "unexpected_failure"
            observations = [f"Request failed: {err['type']}"]
        else:
            count = len(data) if isinstance(data, list) else None
            response_summary = {
                "status_code": 200,
                "kind": type(data).__name__,
                "count": count,
                "body_excerpt": data[:3] if isinstance(data, list) else data,
            }
            status = "success" if count and count > 0 else "expected_failure"
            observations = [
                f"Returned {count} readings over {config.read_window_days} days"
                if count is not None
                else f"Unexpected response type: {type(data).__name__}"
            ]

        log_path = write_raw_log(
            results_dir, name, request_summary, response_summary
        )
        out.append(
            ProbeResult(
                name=name,
                category="reads",
                endpoint="GET /reading",
                status=status,
                request_summary=request_summary,
                response_summary=response_summary,
                observations=observations,
                raw_log_path=str(log_path),
            )
        )
    return out


def probe_read_ds_paginated(
    client: ZiraClient, config: Config, results_dir: Path
) -> list[ProbeResult]:
    out: list[ProbeResult] = []
    now = datetime.now(timezone.utc)
    end_time = _iso_utc(now)
    start_time = _iso_utc(now - timedelta(days=config.read_window_days))

    for ds in config.read_data_sources:
        name = f"read_ds_paginated[{ds.label}]"
        observations: list[str] = []

        page1, err1 = _safe_call(
            client.get_readings,
            meter_id=ds.meter_id,
            end_time=end_time,
            start_time=start_time,
            limit=5,
        )
        if err1 is not None or not isinstance(page1, list) or not page1:
            response_summary = {"page1": err1 or {"body": page1}}
            out.append(
                ProbeResult(
                    name=name,
                    category="reads",
                    endpoint="GET /reading (paginated)",
                    status="expected_failure",
                    request_summary={"limit": 5, "meter_id": ds.meter_id},
                    response_summary=response_summary,
                    observations=["Not enough data to paginate."],
                    raw_log_path=str(
                        write_raw_log(results_dir, name, {}, response_summary)
                    ),
                )
            )
            continue

        last_key = None
        for item in page1:
            if isinstance(item, dict):
                for k in ("id", "key", "lastValue", "uuid"):
                    if k in item:
                        last_key = item[k]
                        break
            if last_key:
                break

        if last_key is None:
            observations.append(
                "Could not locate a last-value key in page 1; pagination unverified."
            )
            status = "expected_failure"
            page2 = None
            err2 = None
        else:
            page2, err2 = _safe_call(
                client.get_readings,
                meter_id=ds.meter_id,
                end_time=end_time,
                start_time=start_time,
                limit=5,
                last_value=str(last_key),
            )
            if err2 is not None:
                status = "unexpected_failure"
                observations.append(f"Page 2 errored: {err2['type']}")
            else:
                page1_ids = {id(x) for x in page1}
                overlap = False
                if isinstance(page2, list):
                    for x in page2:
                        if id(x) in page1_ids:
                            overlap = True
                            break
                status = "success"
                observations.append(
                    f"Page 1 returned {len(page1)}; page 2 returned "
                    f"{len(page2) if isinstance(page2, list) else 'n/a'}; "
                    f"overlap detected: {overlap}"
                )

        response_summary = {
            "page1_count": len(page1),
            "last_key_guess": last_key,
            "page2": err2 or ({"count": len(page2)} if isinstance(page2, list) else page2),
        }
        log_path = write_raw_log(results_dir, name, {"meter_id": ds.meter_id}, response_summary)
        out.append(
            ProbeResult(
                name=name,
                category="reads",
                endpoint="GET /reading (paginated)",
                status=status,
                request_summary={"meter_id": ds.meter_id, "limit": 5},
                response_summary=response_summary,
                observations=observations,
                raw_log_path=str(log_path),
            )
        )
    return out


def probe_read_ds_empty_window(
    client: ZiraClient, config: Config, results_dir: Path
) -> list[ProbeResult]:
    out: list[ProbeResult] = []
    far_past_end = _iso_utc(datetime(2000, 1, 1, 0, 1, tzinfo=timezone.utc))
    far_past_start = _iso_utc(datetime(2000, 1, 1, 0, 0, tzinfo=timezone.utc))

    for ds in config.read_data_sources:
        name = f"read_ds_empty_window[{ds.label}]"
        data, err = _safe_call(
            client.get_readings,
            meter_id=ds.meter_id,
            end_time=far_past_end,
            start_time=far_past_start,
        )
        if err is not None:
            response_summary = {"error": err}
            status = "unexpected_failure"
            obs = [f"Empty window errored: {err['type']}"]
        else:
            response_summary = {"kind": type(data).__name__, "body": data}
            status = "success" if isinstance(data, list) and not data else "expected_failure"
            obs = [f"Empty-window response: {type(data).__name__}={data}"]
        log_path = write_raw_log(
            results_dir, name, {"meter_id": ds.meter_id}, response_summary
        )
        out.append(
            ProbeResult(
                name=name,
                category="reads",
                endpoint="GET /reading (empty window)",
                status=status,
                request_summary={"meter_id": ds.meter_id, "window": "1 minute in year 2000"},
                response_summary=response_summary,
                observations=obs,
                raw_log_path=str(log_path),
            )
        )
    return out


def probe_read_channel_analysis_intervals(
    client: ZiraClient, config: Config, results_dir: Path
) -> list[ProbeResult]:
    out: list[ProbeResult] = []
    now = datetime.now(timezone.utc)
    to_time = _iso_utc(now)
    from_time = _iso_utc(now - timedelta(days=config.read_window_days))
    intervals = ["5 minutes", "1 hours", "1 days", "1 weeks"]

    for ch in config.read_channels:
        for interval in intervals:
            name = f"read_channel_analysis[{ch.label}][{interval}]"
            data, err = _safe_call(
                client.get_channel_analysis,
                channel_id=ch.channel_id,
                interval=interval,
                from_time=from_time,
                to_time=to_time,
            )
            if err is not None:
                response_summary = {"error": err}
                status = "unexpected_failure"
                obs = [f"Errored at interval={interval}: {err['type']}"]
            else:
                response_summary = {"kind": type(data).__name__, "body_excerpt": _truncate(data)}
                status = "success"
                obs = [f"Returned data of type {type(data).__name__}"]
            log_path = write_raw_log(
                results_dir, name, {"channel_id": ch.channel_id, "interval": interval}, response_summary
            )
            out.append(
                ProbeResult(
                    name=name,
                    category="reads",
                    endpoint="GET /channels/{id}/analysis",
                    status=status,
                    request_summary={"channel_id": ch.channel_id, "interval": interval},
                    response_summary=response_summary,
                    observations=obs,
                    raw_log_path=str(log_path),
                )
            )
    return out


def probe_read_channel_analysis_too_many_points(
    client: ZiraClient, config: Config, results_dir: Path
) -> list[ProbeResult]:
    out: list[ProbeResult] = []
    now = datetime.now(timezone.utc)
    to_time = _iso_utc(now)
    from_time = _iso_utc(now - timedelta(days=90))

    for ch in config.read_channels:
        name = f"read_channel_analysis_too_many[{ch.label}]"
        data, err = _safe_call(
            client.get_channel_analysis,
            channel_id=ch.channel_id,
            interval="5 minutes",
            from_time=from_time,
            to_time=to_time,
        )
        if err is not None:
            response_summary = {"error": err}
            body = err.get("body") or ""
            matched = "02-001" in str(body)
            status = "success" if matched else "expected_failure"
            obs = [
                f"Got error {err['type']}; contained '02-001': {matched}",
            ]
        else:
            response_summary = {"kind": type(data).__name__, "body_excerpt": _truncate(data)}
            status = "expected_failure"
            obs = ["Expected error 02-001 but request succeeded."]

        log_path = write_raw_log(
            results_dir, name, {"channel_id": ch.channel_id, "interval": "5 minutes", "window": "90 days"}, response_summary
        )
        out.append(
            ProbeResult(
                name=name,
                category="reads",
                endpoint="GET /channels/{id}/analysis (too-many-points)",
                status=status,
                request_summary={"channel_id": ch.channel_id},
                response_summary=response_summary,
                observations=obs,
                raw_log_path=str(log_path),
            )
        )
    return out


def _truncate(data, max_len: int = 500):
    s = repr(data)
    if len(s) <= max_len:
        return data
    return s[:max_len] + "...[truncated]"


ALL_READ_PROBES = [
    probe_read_ds_single_window,
    probe_read_ds_paginated,
    probe_read_ds_empty_window,
    probe_read_channel_analysis_intervals,
    probe_read_channel_analysis_too_many_points,
]
```

- [ ] **Step 3: Quick import sanity check**

Run: `python -c "from zira_probe.probes.reads import ALL_READ_PROBES; print(len(ALL_READ_PROBES))"`
Expected: `5`

- [ ] **Step 4: Commit**

```bash
git add src/zira_probe/probes/__init__.py src/zira_probe/probes/reads.py
git commit -m "Add read probes (5 probes covering documented read endpoints)"
```

---

### Task 9: Write happy-path probes

**Files:**
- Create: `src/zira_probe/probes/writes_happy.py`

- [ ] **Step 1: Create `src/zira_probe/probes/writes_happy.py`**

```python
"""Happy-path write probes against the sandbox data source."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from zira_probe.client import ZiraClient
from zira_probe.config import Config
from zira_probe.results import ProbeResult, redact_api_key, write_raw_log


# All happy-path writes use timestamps in a clearly-labeled future range so
# they're easy to spot in the Zira UI and ignore for real analytics.
WRITE_MARKER_BASE = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _safe_post(client: ZiraClient, payload):
    try:
        return client.add_readings(payload), None
    except requests.HTTPError as exc:
        r = exc.response
        return None, {
            "type": "HTTPError",
            "status_code": r.status_code if r is not None else None,
            "body": r.text if r is not None else str(exc),
        }
    except Exception as exc:
        return None, {"type": type(exc).__name__, "message": str(exc)}


def _run_write(
    client: ZiraClient,
    config: Config,
    results_dir: Path,
    name: str,
    payload: list[dict],
) -> ProbeResult:
    request_summary = {
        "method": "POST",
        "url": f"{client.base_url}reading/ids/",
        "headers": {"X-API-Key": redact_api_key(client.api_key), "Content-Type": "application/json"},
        "body": payload,
    }
    data, err = _safe_post(client, payload)
    if err is not None:
        response_summary = {"error": err}
        status = "unexpected_failure"
        obs = [f"Write errored: {err['type']} — {err.get('status_code')}"]
    else:
        response_summary = {"status_code": 200, "body_excerpt": data}
        status = "success"
        obs = [f"Write returned: {data!r}"]
    log_path = write_raw_log(results_dir, name, request_summary, response_summary)
    return ProbeResult(
        name=name,
        category="writes_happy",
        endpoint="POST /reading/ids/",
        status=status,
        request_summary=request_summary,
        response_summary=response_summary,
        observations=obs,
        raw_log_path=str(log_path),
    )


def probe_write_single_number(
    client: ZiraClient, config: Config, results_dir: Path
) -> list[ProbeResult]:
    ts = _iso(WRITE_MARKER_BASE + timedelta(minutes=1))
    payload = [
        {
            "meterId": config.test_ds_meter_id,
            "timestamp": ts,
            "values": [{"metricId": config.test_ds_number_metric, "value": 42}],
        }
    ]
    return [_run_write(client, config, results_dir, "write_single_number", payload)]


def probe_write_single_text(
    client: ZiraClient, config: Config, results_dir: Path
) -> list[ProbeResult]:
    ts = _iso(WRITE_MARKER_BASE + timedelta(minutes=2))
    payload = [
        {
            "meterId": config.test_ds_meter_id,
            "timestamp": ts,
            "values": [{"metricId": config.test_ds_text_metric, "value": "probe_marker"}],
        }
    ]
    return [_run_write(client, config, results_dir, "write_single_text", payload)]


def probe_write_both_metrics(
    client: ZiraClient, config: Config, results_dir: Path
) -> list[ProbeResult]:
    ts = _iso(WRITE_MARKER_BASE + timedelta(minutes=3))
    payload = [
        {
            "meterId": config.test_ds_meter_id,
            "timestamp": ts,
            "values": [
                {"metricId": config.test_ds_number_metric, "value": 7},
                {"metricId": config.test_ds_text_metric, "value": "probe_both"},
            ],
        }
    ]
    return [_run_write(client, config, results_dir, "write_both_metrics", payload)]


def probe_write_batch_5(
    client: ZiraClient, config: Config, results_dir: Path
) -> list[ProbeResult]:
    payload = [
        {
            "meterId": config.test_ds_meter_id,
            "timestamp": _iso(WRITE_MARKER_BASE + timedelta(hours=i + 1)),
            "values": [
                {"metricId": config.test_ds_number_metric, "value": i},
                {"metricId": config.test_ds_text_metric, "value": f"batch_{i}"},
            ],
        }
        for i in range(5)
    ]
    return [_run_write(client, config, results_dir, "write_batch_5", payload)]


ALL_WRITE_HAPPY_PROBES = [
    probe_write_single_number,
    probe_write_single_text,
    probe_write_both_metrics,
    probe_write_batch_5,
]
```

- [ ] **Step 2: Import sanity check**

Run: `python -c "from zira_probe.probes.writes_happy import ALL_WRITE_HAPPY_PROBES; print(len(ALL_WRITE_HAPPY_PROBES))"`
Expected: `4`

- [ ] **Step 3: Commit**

```bash
git add src/zira_probe/probes/writes_happy.py
git commit -m "Add happy-path write probes (number, text, combined, batch)"
```

---

### Task 10: Write error-surface probes

**Files:**
- Create: `src/zira_probe/probes/writes_error_surface.py`

- [ ] **Step 1: Create `src/zira_probe/probes/writes_error_surface.py`**

```python
"""Deliberate-error write probes.

Each probe sends a malformed or boundary-testing payload and records exactly
how Zira responds. A 4xx response is this probe category's "success" — we
got information about validation behavior.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from zira_probe.client import ZiraClient
from zira_probe.config import Config
from zira_probe.results import ProbeResult, redact_api_key, write_raw_log


WRITE_MARKER_BASE = datetime(2099, 6, 1, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _run(
    client: ZiraClient,
    config: Config,
    results_dir: Path,
    name: str,
    payload,
    expectation: str,
) -> ProbeResult:
    """Send `payload` via client.request (so 4xx doesn't raise); record response."""
    request_summary = {
        "method": "POST",
        "url": f"{client.base_url}reading/ids/",
        "headers": {"X-API-Key": redact_api_key(client.api_key), "Content-Type": "application/json"},
        "body": payload,
        "expectation": expectation,
    }
    try:
        resp = client.request("POST", "reading/ids/", json_body=payload)
        response_summary = {
            "status_code": resp.status_code,
            "body_excerpt": (resp.text[:500] + "...") if len(resp.text) > 500 else resp.text,
        }
        if 400 <= resp.status_code < 500:
            status = "success"  # rejected as expected
            obs = [f"Rejected with {resp.status_code} — as expected."]
        elif 200 <= resp.status_code < 300:
            status = "expected_failure"
            obs = [f"Unexpected 2xx — payload was accepted."]
        else:
            status = "unexpected_failure"
            obs = [f"Unexpected status {resp.status_code}."]
    except requests.RequestException as exc:
        response_summary = {"exception": type(exc).__name__, "message": str(exc)}
        status = "unexpected_failure"
        obs = [f"Network/transport failure: {exc}"]

    log_path = write_raw_log(results_dir, name, request_summary, response_summary)
    return ProbeResult(
        name=name,
        category="writes_error_surface",
        endpoint="POST /reading/ids/",
        status=status,
        request_summary=request_summary,
        response_summary=response_summary,
        observations=obs,
        raw_log_path=str(log_path),
    )


def probe_write_bad_meter_id(client, config, results_dir):
    payload = [
        {
            "meterId": "this-meter-id-does-not-exist-0000",
            "timestamp": _iso(WRITE_MARKER_BASE),
            "values": [{"metricId": config.test_ds_number_metric, "value": 1}],
        }
    ]
    return [_run(client, config, results_dir, "write_bad_meter_id", payload,
                 "Zira rejects unknown meterId.")]


def probe_write_bad_metric_id(client, config, results_dir):
    payload = [
        {
            "meterId": config.test_ds_meter_id,
            "timestamp": _iso(WRITE_MARKER_BASE + timedelta(minutes=1)),
            "values": [{"metricId": "999999999", "value": 1}],
        }
    ]
    return [_run(client, config, results_dir, "write_bad_metric_id", payload,
                 "Zira rejects unknown metricId (or silently drops it).")]


def probe_write_wrong_value_type(client, config, results_dir):
    payload = [
        {
            "meterId": config.test_ds_meter_id,
            "timestamp": _iso(WRITE_MARKER_BASE + timedelta(minutes=2)),
            "values": [
                {"metricId": config.test_ds_number_metric, "value": "not_a_number"},
                {"metricId": config.test_ds_text_metric, "value": 12345},
            ],
        }
    ]
    return [_run(client, config, results_dir, "write_wrong_value_type", payload,
                 "Zira rejects or coerces mismatched value types.")]


def probe_write_missing_meter_id(client, config, results_dir):
    payload = [
        {
            "timestamp": _iso(WRITE_MARKER_BASE + timedelta(minutes=3)),
            "values": [{"metricId": config.test_ds_number_metric, "value": 1}],
        }
    ]
    return [_run(client, config, results_dir, "write_missing_meter_id", payload,
                 "Zira rejects payload lacking meterId.")]


def probe_write_missing_timestamp(client, config, results_dir):
    payload = [
        {
            "meterId": config.test_ds_meter_id,
            "values": [{"metricId": config.test_ds_number_metric, "value": 1}],
        }
    ]
    return [_run(client, config, results_dir, "write_missing_timestamp", payload,
                 "Zira rejects payload lacking timestamp.")]


def probe_write_duplicate_timestamp(client, config, results_dir):
    ts = _iso(WRITE_MARKER_BASE + timedelta(minutes=4))
    payload = [
        {
            "meterId": config.test_ds_meter_id,
            "timestamp": ts,
            "values": [{"metricId": config.test_ds_number_metric, "value": 1}],
        },
        {
            "meterId": config.test_ds_meter_id,
            "timestamp": ts,
            "values": [{"metricId": config.test_ds_number_metric, "value": 2}],
        },
    ]
    return [_run(client, config, results_dir, "write_duplicate_timestamp", payload,
                 "Observe dedup/overwrite/accept-both behavior for identical timestamps.")]


def probe_write_future_timestamp(client, config, results_dir):
    future = datetime(2199, 1, 1, tzinfo=timezone.utc)
    payload = [
        {
            "meterId": config.test_ds_meter_id,
            "timestamp": _iso(future),
            "values": [{"metricId": config.test_ds_number_metric, "value": 1}],
        }
    ]
    return [_run(client, config, results_dir, "write_future_timestamp", payload,
                 "Observe whether Zira gates very-distant future timestamps.")]


def probe_write_large_batch(client, config, results_dir):
    payload = [
        {
            "meterId": config.test_ds_meter_id,
            "timestamp": _iso(WRITE_MARKER_BASE + timedelta(seconds=i)),
            "values": [{"metricId": config.test_ds_number_metric, "value": i}],
        }
        for i in range(500)
    ]
    return [_run(client, config, results_dir, "write_large_batch_500", payload,
                 "Observe batch-size limits.")]


def probe_write_empty_body(client, config, results_dir):
    return [_run(client, config, results_dir, "write_empty_body", [],
                 "Observe empty-array behavior.")]


ALL_WRITE_ERROR_PROBES = [
    probe_write_bad_meter_id,
    probe_write_bad_metric_id,
    probe_write_wrong_value_type,
    probe_write_missing_meter_id,
    probe_write_missing_timestamp,
    probe_write_duplicate_timestamp,
    probe_write_future_timestamp,
    probe_write_large_batch,
    probe_write_empty_body,
]
```

- [ ] **Step 2: Import sanity check**

Run: `python -c "from zira_probe.probes.writes_error_surface import ALL_WRITE_ERROR_PROBES; print(len(ALL_WRITE_ERROR_PROBES))"`
Expected: `9`

- [ ] **Step 3: Commit**

```bash
git add src/zira_probe/probes/writes_error_surface.py
git commit -m "Add write error-surface probes (9 deliberate-error cases)"
```

---

### Task 11: Undocumented endpoint probes

**Files:**
- Create: `src/zira_probe/probes/undocumented.py`

- [ ] **Step 1: Create `src/zira_probe/probes/undocumented.py`**

```python
"""Probes for plausible undocumented endpoints.

Not a fuzzer — just well-educated guesses based on the documented terminology
(meterId, channel_id) and REST conventions.
"""

from __future__ import annotations

from pathlib import Path

import requests

from zira_probe.client import ZiraClient
from zira_probe.config import Config
from zira_probe.results import ProbeResult, redact_api_key, write_raw_log


UNDOCUMENTED_TARGETS: list[tuple[str, str, str]] = [
    # (method, path, rationale)
    ("GET", "data-sources", "Guide hints at 'Get Data-Source API'."),
    ("GET", "meters", "'meterId' terminology suggests resource named 'meters'."),
    ("GET", "channels", "Channel IDs have no documented list endpoint."),
    ("GET", "forms", "Forms are readable per docs but no list endpoint is documented."),
    ("GET", "applications", "'Applications' tab manages API keys."),
    ("GET", "", "Base URL may return a capabilities descriptor."),
    ("GET", "health", "Common diagnostic path."),
    ("GET", "ping", "Common diagnostic path."),
    ("GET", "version", "Common diagnostic path."),
    ("OPTIONS", "", "OPTIONS sometimes exposes allowed methods."),
]


def probe_undocumented_endpoints(
    client: ZiraClient, config: Config, results_dir: Path
) -> list[ProbeResult]:
    out: list[ProbeResult] = []
    for method, path, rationale in UNDOCUMENTED_TARGETS:
        name = f"undoc_{method.lower()}_{path or 'root'}".replace("/", "_")
        request_summary = {
            "method": method,
            "url": f"{client.base_url}{path}",
            "headers": {"X-API-Key": redact_api_key(client.api_key)},
            "rationale": rationale,
        }
        try:
            resp = client.request(
                method,
                path,
                timeout_seconds=config.undocumented_timeout_seconds,
            )
            body_text = resp.text or ""
            response_summary = {
                "status_code": resp.status_code,
                "headers": {
                    k: v for k, v in resp.headers.items()
                    if k.lower() in {"content-type", "allow", "server", "x-ratelimit-remaining"}
                },
                "body_excerpt": (body_text[:500] + "...") if len(body_text) > 500 else body_text,
            }
            interesting = resp.status_code not in (403, 404) and not (500 <= resp.status_code < 600)
            status = "success" if interesting else "expected_failure"
            obs = [
                f"{method} /{path} → {resp.status_code}"
                + (" (INTERESTING)" if interesting else ""),
            ]
        except requests.RequestException as exc:
            response_summary = {"exception": type(exc).__name__, "message": str(exc)}
            status = "unexpected_failure"
            obs = [f"Transport error: {exc}"]

        log_path = write_raw_log(results_dir, name, request_summary, response_summary)
        out.append(
            ProbeResult(
                name=name,
                category="undocumented",
                endpoint=f"{method} /{path}",
                status=status,
                request_summary=request_summary,
                response_summary=response_summary,
                observations=obs,
                raw_log_path=str(log_path),
            )
        )
    return out


ALL_UNDOCUMENTED_PROBES = [probe_undocumented_endpoints]
```

- [ ] **Step 2: Import sanity check**

Run: `python -c "from zira_probe.probes.undocumented import UNDOCUMENTED_TARGETS; print(len(UNDOCUMENTED_TARGETS))"`
Expected: `10`

- [ ] **Step 3: Commit**

```bash
git add src/zira_probe/probes/undocumented.py
git commit -m "Add undocumented-endpoint probe with 10 plausible targets"
```

---

### Task 12: Report generator

**Files:**
- Create: `src/zira_probe/report.py`
- Create: `tests/test_report.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_report.py`:

```python
from zira_probe.report import render_report
from zira_probe.results import ProbeResult


def test_render_report_includes_all_categories_and_status_glyphs():
    results = [
        ProbeResult(
            name="read_ds_single_window[DS1]",
            category="reads",
            endpoint="GET /reading",
            status="success",
            observations=["Returned 42 readings."],
        ),
        ProbeResult(
            name="write_single_number",
            category="writes_happy",
            endpoint="POST /reading/ids/",
            status="unexpected_failure",
            observations=["Write errored: HTTPError 400."],
        ),
        ProbeResult(
            name="undoc_get_data-sources",
            category="undocumented",
            endpoint="GET /data-sources",
            status="success",
            observations=["GET /data-sources → 200 (INTERESTING)"],
        ),
        ProbeResult(
            name="write_bad_meter_id",
            category="writes_error_surface",
            endpoint="POST /reading/ids/",
            status="success",
            observations=["Rejected with 404 — as expected."],
        ),
    ]

    md = render_report(results)

    assert "# Zira API Capability Report" in md
    assert "## Reads" in md
    assert "## Writes — Happy Path" in md
    assert "## Writes — Error Surface" in md
    assert "## Undocumented Endpoints" in md
    assert "✅" in md  # success glyph used somewhere
    assert "❌" in md  # unexpected_failure glyph
    assert "INTERESTING" in md
    # All four probe names must appear in the doc
    for r in results:
        assert r.name in md


def test_render_report_lists_open_questions_when_undocumented_was_interesting():
    interesting = ProbeResult(
        name="undoc_get_data-sources",
        category="undocumented",
        endpoint="GET /data-sources",
        status="success",
        observations=["GET /data-sources → 200 (INTERESTING)"],
    )
    md = render_report([interesting])
    assert "Open Questions" in md
    assert "data-sources" in md
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report.py -v`
Expected: failures — `ModuleNotFoundError: No module named 'zira_probe.report'`

- [ ] **Step 3: Create `src/zira_probe/report.py`**

```python
"""Render a list of ProbeResults as CAPABILITY_REPORT.md."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from zira_probe.results import ProbeResult


_GLYPH = {
    "success": "✅",
    "expected_failure": "⚠️",
    "unexpected_failure": "❌",
    "skipped": "⏭️",
}

_CATEGORY_SECTIONS = [
    ("reads", "## Reads"),
    ("writes_happy", "## Writes — Happy Path"),
    ("writes_error_surface", "## Writes — Error Surface"),
    ("undocumented", "## Undocumented Endpoints"),
]


def _format_result(r: ProbeResult) -> str:
    glyph = _GLYPH.get(r.status, "?")
    parts = [f"### {glyph} `{r.name}`", f"**Endpoint:** `{r.endpoint}`"]
    if r.observations:
        parts.append("**Observations:**")
        parts.extend(f"- {o}" for o in r.observations)
    if r.request_summary:
        parts.append(
            "<details><summary>Request</summary>\n\n```json\n"
            + json.dumps(r.request_summary, indent=2, default=str)
            + "\n```\n\n</details>"
        )
    if r.response_summary:
        parts.append(
            "<details><summary>Response</summary>\n\n```json\n"
            + json.dumps(r.response_summary, indent=2, default=str)
            + "\n```\n\n</details>"
        )
    if r.raw_log_path:
        parts.append(f"_Raw log: `{r.raw_log_path}`_")
    return "\n\n".join(parts)


def render_report(results: list[ProbeResult]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Zira API Capability Report",
        "",
        f"Generated: {now}",
        "",
        "Glyphs: ✅ success · ⚠️ expected failure (informative) · ❌ unexpected failure · ⏭️ skipped",
        "",
    ]

    by_cat: dict[str, list[ProbeResult]] = {cat: [] for cat, _ in _CATEGORY_SECTIONS}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)

    for cat, heading in _CATEGORY_SECTIONS:
        lines.append("")
        lines.append(heading)
        lines.append("")
        if not by_cat.get(cat):
            lines.append("_No probes in this category ran._")
            continue
        for r in by_cat[cat]:
            lines.append(_format_result(r))
            lines.append("")

    # Open Questions: surface anything labeled INTERESTING
    interesting = [
        r
        for r in results
        if any("INTERESTING" in o for o in r.observations)
    ]
    if interesting:
        lines.append("")
        lines.append("## Open Questions")
        lines.append("")
        lines.append("The following probes returned surprising results and warrant investigation:")
        lines.append("")
        for r in interesting:
            obs = "; ".join(r.observations)
            lines.append(f"- **{r.name}** (`{r.endpoint}`): {obs}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_report.py -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_probe/report.py tests/test_report.py
git commit -m "Add capability report renderer"
```

---

### Task 13: Runner CLI

**Files:**
- Create: `src/zira_probe/runner.py`

- [ ] **Step 1: Create `src/zira_probe/runner.py`**

```python
"""Entry point: loads config, runs every probe, writes CAPABILITY_REPORT.md."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zira_probe.client import ZiraClient
from zira_probe.config import load_config
from zira_probe.probes.reads import ALL_READ_PROBES
from zira_probe.probes.undocumented import ALL_UNDOCUMENTED_PROBES
from zira_probe.probes.writes_error_surface import ALL_WRITE_ERROR_PROBES
from zira_probe.probes.writes_happy import ALL_WRITE_HAPPY_PROBES
from zira_probe.report import render_report
from zira_probe.results import ProbeResult


def _auth_preflight(client: ZiraClient, meter_id: str) -> bool:
    """Hit one cheap read to confirm the API key works."""
    from datetime import datetime, timezone

    end = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        client.get_readings(meter_id=meter_id, end_time=end, limit=1)
        return True
    except Exception as exc:
        print(f"[preflight] auth check failed: {exc}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zira API capability probe.")
    parser.add_argument("--env", default=".env", help="Path to .env file (default: .env)")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml (default: config.yaml)")
    parser.add_argument("--results-dir", default="results", help="Where to write raw per-probe logs")
    parser.add_argument("--report", default="CAPABILITY_REPORT.md", help="Report output path")
    parser.add_argument("--auth-only", action="store_true", help="Run preflight only, then exit")
    parser.add_argument("--skip-writes", action="store_true", help="Skip both write categories")
    args = parser.parse_args(argv)

    config = load_config(env_path=args.env, yaml_path=args.config)
    client = ZiraClient(api_key=config.api_key, base_url=config.base_url)

    preflight_target = (
        config.read_data_sources[0].meter_id
        if config.read_data_sources
        else config.test_ds_meter_id
    )
    ok = _auth_preflight(client, preflight_target)
    if not ok:
        return 2
    print("[preflight] auth OK", file=sys.stderr)
    if args.auth_only:
        return 0

    results_dir = Path(args.results_dir)

    probe_groups: list[tuple[str, list]] = [("reads", ALL_READ_PROBES)]
    if not args.skip_writes:
        probe_groups.append(("writes_happy", ALL_WRITE_HAPPY_PROBES))
        probe_groups.append(("writes_error_surface", ALL_WRITE_ERROR_PROBES))
    probe_groups.append(("undocumented", ALL_UNDOCUMENTED_PROBES))

    all_results: list[ProbeResult] = []
    for group_name, probes in probe_groups:
        for probe_fn in probes:
            probe_name = probe_fn.__name__
            print(f"[{group_name}] running {probe_name}...", file=sys.stderr)
            try:
                res = probe_fn(client, config, results_dir)
                all_results.extend(res)
            except Exception as exc:
                print(f"[{group_name}] {probe_name} exploded: {exc}", file=sys.stderr)
                all_results.append(
                    ProbeResult(
                        name=probe_name,
                        category=group_name,
                        endpoint="(runner-level failure)",
                        status="unexpected_failure",
                        observations=[f"Probe function raised: {type(exc).__name__}: {exc}"],
                    )
                )

    md = render_report(all_results)
    Path(args.report).write_text(md, encoding="utf-8")
    print(f"[done] wrote {args.report} with {len(all_results)} probe results", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Sanity check — parse --help**

Run: `python -m zira_probe.runner --help`
Expected: usage text shows `--auth-only`, `--skip-writes`, `--report`, etc. (No real probe is run because argparse prints help and exits.)

- [ ] **Step 3: Run the full unit test suite**

Run: `pytest -v`
Expected: all tests PASS (12+ tests across `test_config.py`, `test_results.py`, `test_client.py`, `test_report.py`).

- [ ] **Step 4: Commit**

```bash
git add src/zira_probe/runner.py
git commit -m "Add runner CLI with --auth-only and --skip-writes"
```

---

### Task 14: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Create `README.md`**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Add README with setup and run instructions"
```

---

### Task 15: End-to-end smoke run (manual verification)

**Files:** None. This task is a manual verification step performed by the user against their real Zira site.

- [ ] **Step 1: Prepare the Zira side**

In the Zira web UI:
1. Create an **Application** (puzzle icon → Applications → "+ Add New Application"). Copy the API key.
2. Create a **Data Source** named `API Test DS` with exactly two metrics:
   - one of **type Number**
   - one of **type Text**
3. For the test DS, copy the **meterId** (3 dots → Share → Copy ID) and both **metricIds** (edit DS → copy icon next to each metric).
4. Pick one existing production data source; copy its meterId.
5. Pick one existing channel; copy its channel_id (Channels tab → 3 dots → Share → Copy ID).

- [ ] **Step 2: Fill in `.env` and `config.yaml`**

```bash
cp .env.example .env
# edit .env and paste the API key

# edit config.yaml and paste all six IDs from step 1
```

- [ ] **Step 3: Preflight**

Run: `python -m zira_probe.runner --auth-only`
Expected stderr: `[preflight] auth OK`
If you see an auth error: re-check `ZIRA_API_KEY`, re-check that the API key hasn't been revoked in the Zira Applications tab.

- [ ] **Step 4: Dry-run reads only**

Run: `python -m zira_probe.runner --skip-writes`
Expected: completes without Python traceback; writes `CAPABILITY_REPORT.md` and several files under `results/`.
Open `CAPABILITY_REPORT.md` and review the Reads + Undocumented sections.

- [ ] **Step 5: Full run (includes writes to test DS)**

Run: `python -m zira_probe.runner`
Expected: completes; report now contains Writes — Happy Path and Writes — Error Surface sections.
In the Zira UI, navigate to the test DS and confirm that readings from year 2099 and 2199 appear there (these are the marker timestamps used by the happy-path probes). No writes should appear in any other DS.

- [ ] **Step 6: Commit the first report snapshot**

The `.gitignore` excludes `CAPABILITY_REPORT.md` by default. If you want to commit this first snapshot as a milestone:

```bash
git add -f CAPABILITY_REPORT.md
git commit -m "Add first capability report snapshot"
```

---

## Self-Review Notes

Checklist run against the spec on 2026-04-24:

- **Spec coverage:** Every probe in the spec's probe catalog has a corresponding task (reads → Task 8, writes happy → Task 9, writes error-surface → Task 10, undocumented → Task 11). `CAPABILITY_REPORT.md` is produced by Task 12 (renderer) wired up in Task 13 (runner). `--auth-only` preflight is in Task 13. `ZiraClient` unit tests with `responses` library are in Tasks 4–7. `.env` / `config.yaml` separation is in Task 1.
- **Placeholders:** None. Every code block is complete; every `REPLACE_WITH_*` marker is in `config.yaml` where the user is *supposed* to edit it, not in implementation code.
- **Type consistency:** `ProbeResult` field names and types are identical everywhere they appear. `ZiraClient` method names match across client, probes, and runner. `Config` attribute names (`test_ds_meter_id`, `test_ds_number_metric`, etc.) match between `config.py`, tests, and probes.
- **Scope:** Odoo is not mentioned in any task. No scheduled sync. No forms API (the doc calls out writes don't support forms). Tight to the spec.
