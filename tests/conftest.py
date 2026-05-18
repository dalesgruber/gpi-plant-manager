"""Pytest bootstrap.

Set ``AUTH_DISABLED=1`` before any test module imports ``zira_dashboard.app``
so the new ``RequireAuthMiddleware`` short-circuits and existing TestClient
tests (which don't carry session cookies) keep working unchanged.

Also seeds a deterministic ``SESSION_SECRET`` so the session signer doesn't
randomly invalidate fixtures between runs.

Both use ``setdefault`` — a test that wants to exercise the real auth gate
can still set the env vars before importing the app.
"""

from __future__ import annotations

import os

os.environ.setdefault("AUTH_DISABLED", "1")
os.environ.setdefault(
    "SESSION_SECRET", "test-secret-32-bytes-of-random-data!!"
)
