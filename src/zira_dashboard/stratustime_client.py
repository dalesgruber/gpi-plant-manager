"""Client for the StratusTime time-clock web services API.

Auth is configured via two env vars:
  - STRATUSTIME_SHARED_KEY   (UUID configured in StratusTime's "Inbound Services" admin page)
  - STRATUSTIME_WS_PASSWORD  (the wsuser password set on that same page)

The exact auth wire format is not documented publicly, so `health_check()`
tries several common patterns in order and returns which one worked.
Once we know the right one, callers can use `request()` directly.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
import urllib.error
from typing import Any

BASE_URL = "https://stratustime.centralservers.com/Service/ws-json"
DEFAULT_VERSION = "v1"
TIMEOUT_SECONDS = 30


def _shared_key() -> str | None:
    return os.environ.get("STRATUSTIME_SHARED_KEY")


def _ws_password() -> str | None:
    return os.environ.get("STRATUSTIME_WS_PASSWORD")


def _build_url(path: str, version: str = DEFAULT_VERSION) -> str:
    path = path.lstrip("/")
    return f"{BASE_URL}/{version}/{path}"


# Auth strategies — each is (name, request-builder).
# A request-builder receives the URL, body, shared key, password and returns
# (final_url, headers_dict, final_body_bytes). The body is JSON-encoded by us
# at the call site.

def _auth_basic(url: str, body: dict, key: str, pwd: str):
    """Basic auth: base64(SharedKey:wsPassword)."""
    token = base64.b64encode(f"{key}:{pwd}".encode()).decode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {token}",
    }
    return url, headers, json.dumps(body).encode()


def _auth_header_pair(url: str, body: dict, key: str, pwd: str):
    """Custom headers Shared-Key + Password."""
    headers = {
        "Content-Type": "application/json",
        "Shared-Key": key,
        "Password": pwd,
    }
    return url, headers, json.dumps(body).encode()


def _auth_body_wrapper(url: str, body: dict, key: str, pwd: str):
    """Credentials embedded in JSON body."""
    wrapped = {"SharedKey": key, "Password": pwd, **body}
    headers = {"Content-Type": "application/json"}
    return url, headers, json.dumps(wrapped).encode()


AUTH_STRATEGIES = [
    ("basic", _auth_basic),
    ("header-pair", _auth_header_pair),
    ("body-wrapper", _auth_body_wrapper),
]


def _try_request(method: str, url: str, body: dict, scheme_name: str, builder) -> tuple[int, str]:
    """Make an HTTP call using the given auth strategy. Returns (status, body_text)."""
    key = _shared_key() or ""
    pwd = _ws_password() or ""
    final_url, headers, payload = builder(url, body, key, pwd)
    req = urllib.request.Request(
        final_url,
        data=payload if method != "GET" else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
    except urllib.error.URLError as e:
        return 0, f"network error: {e.reason}"
    except Exception as e:
        return 0, f"error: {e}"


def health_check() -> dict:
    """Try to authenticate against a known endpoint and report results.

    Returns:
      {
        "ok": bool,
        "configured": bool,           # both env vars present
        "scheme": str | None,         # which auth pattern worked (None if all failed)
        "endpoint": str,              # the URL we hit
        "status": int,                # HTTP status of the working call (0 if all failed)
        "body_preview": str,          # first 200 chars of the working response
        "attempts": [                 # diagnostic trail
          {"scheme": str, "status": int, "body_preview": str},
          ...
        ],
      }
    """
    if not _shared_key() or not _ws_password():
        return {
            "ok": False,
            "configured": False,
            "scheme": None,
            "endpoint": "",
            "status": 0,
            "body_preview": "",
            "attempts": [],
        }

    # Use a likely-stable smoke endpoint. We don't know the exact path yet,
    # so try the employees list — most TWS systems expose one. If 404, the
    # error body should hint at the correct path.
    smoke_path = "/Employees"
    url = _build_url(smoke_path)

    attempts = []
    for name, builder in AUTH_STRATEGIES:
        status, body = _try_request("GET", url, {}, name, builder)
        preview = body[:200].replace("\n", " ")
        attempts.append({"scheme": name, "status": status, "body_preview": preview})
        if 200 <= status < 300:
            return {
                "ok": True,
                "configured": True,
                "scheme": name,
                "endpoint": url,
                "status": status,
                "body_preview": preview,
                "attempts": attempts,
            }

    return {
        "ok": False,
        "configured": True,
        "scheme": None,
        "endpoint": url,
        "status": attempts[-1]["status"] if attempts else 0,
        "body_preview": attempts[-1]["body_preview"] if attempts else "",
        "attempts": attempts,
    }


def list_employees() -> list[dict]:
    """Smoke fetch — once health_check succeeds, this should return employees.

    Uses the auth scheme discovered by health_check. If health_check hasn't
    succeeded, returns []. Caller should display health_check details first.
    """
    hc = health_check()
    if not hc["ok"]:
        return []
    builder = dict(AUTH_STRATEGIES)[hc["scheme"]]
    status, body = _try_request("GET", hc["endpoint"], {}, hc["scheme"], builder)
    if not (200 <= status < 300):
        return []
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("Employees", "employees", "data", "Data", "Items"):
            if key in data and isinstance(data[key], list):
                return data[key]
    return []
