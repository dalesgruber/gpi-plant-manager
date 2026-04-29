"""Hardcoded cert-name → inline-SVG icon mapping.

Lookup is case-insensitive on the trimmed cert name. Returns None when
no mapping exists; callers render a small text pill in that case so a
new Odoo cert is never silently invisible.

All SVGs use stroke="currentColor" so badges inherit the surrounding
text color (theme-friendly). Sized via CSS, not via SVG width/height
attributes.
"""

from __future__ import annotations

# Lucide "forklift"
_SVG_FORKLIFT = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M12 12H5a2 2 0 0 0-2 2v5"/>'
    '<circle cx="13" cy="19" r="2"/>'
    '<circle cx="5" cy="19" r="2"/>'
    '<path d="M8 19h3m5-17v17h6M6 12V7c0-1.1.9-2 2-2h3l5 5"/>'
    '</svg>'
)

# Lucide "truck" — used for both CDL variants
_SVG_SEMI = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/>'
    '<path d="M15 18H9"/>'
    '<path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.624l-3.48-4.35'
    'A1 1 0 0 0 17.52 8H14"/>'
    '<circle cx="17" cy="18" r="2"/>'
    '<circle cx="7" cy="18" r="2"/>'
    '</svg>'
)

# Lucide "wrench"
_SVG_WRENCH = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77'
    'a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91'
    'a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>'
    '</svg>'
)

# Hand-rolled spotter / yard-truck silhouette: stubby cab-forward + flat
# bed. Visually distinct from the semi-truck used for CDL.
_SVG_SPOTTER = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="2" y="8" width="6" height="8" rx="1"/>'
    '<rect x="8" y="11" width="13" height="5"/>'
    '<circle cx="5" cy="18" r="2"/>'
    '<circle cx="17" cy="18" r="2"/>'
    '<line x1="9" y1="18" x2="15" y2="18"/>'
    '</svg>'
)

_CERT_ICONS: dict[str, str] = {
    "forklift certified": _SVG_FORKLIFT,
    "cdl (automatics) certified": _SVG_SEMI,
    "cdl (manuals) certified": _SVG_SEMI,
    "dot certified": _SVG_WRENCH,
    "spotter truck certified": _SVG_SPOTTER,
}


def icon_for(cert_name: str) -> str | None:
    """Return the inline SVG for the given cert name, or None if unmapped.

    Match is case-insensitive on the trimmed name.
    """
    if not cert_name:
        return None
    return _CERT_ICONS.get(cert_name.strip().lower())
