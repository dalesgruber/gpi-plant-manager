"""Per-request lookup: who has which certifications.

Reads from the local Postgres tables that the Odoo sync populates:
- `skills` rows with skill_type='Certifications' are the cert master list.
- `person_skills` rows link a person to a cert. Any link counts as
  'has this cert' — the level value is ignored (binary semantics).

Cached in-process for 60 seconds. Invalidated by `odoo_sync.sync()`
after a refresh so cert changes surface without waiting for the TTL.
"""

from __future__ import annotations

from . import db
from ._cache import TTLCache

_CACHE = TTLCache(ttl_seconds=60.0, max_entries=2)


def load_person_certs() -> dict[str, list[str]]:
    """Return {person_name: [cert_name, ...]} for everyone with at least
    one certification record. Cert lists are alphabetical. 60-second
    in-process cache."""
    return _CACHE.get_or_compute("all", _query)


def _query() -> dict[str, list[str]]:
    sql = """
        SELECT p.name AS person, s.name AS cert
        FROM person_skills ps
        JOIN skills s ON s.id = ps.skill_id
        JOIN people p ON p.id = ps.person_id
        WHERE s.skill_type = 'Certifications'
        ORDER BY p.name, lower(s.name)
    """
    out: dict[str, list[str]] = {}
    for row in db.query(sql):
        out.setdefault(row["person"], []).append(row["cert"])
    return out


def invalidate_cache() -> None:
    """Clear the cache. Called after Odoo sync so fresh certs surface
    immediately."""
    _CACHE.invalidate()
