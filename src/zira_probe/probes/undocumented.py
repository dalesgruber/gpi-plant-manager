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
