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
