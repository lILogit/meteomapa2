"""Place-name → coordinates via the OpenStreetMap Nominatim API.

Only used for ad-hoc names that are not saved by the user. Respects the
Nominatim usage policy: one request per query, with a identifying User-Agent.
Disable via ``GEOCODE_ENABLED=false``.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

log = logging.getLogger("meteomapa.geocode")

ENDPOINT = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "Meteomapa/0.1 (radar notifier)"


def geocode(query: str) -> Optional[tuple[float, float, str]]:
    """Return (lat, lon, display_name) for a place name, or None."""
    try:
        r = requests.get(
            ENDPOINT,
            params={"q": query, "format": "json", "limit": 1, "accept-language": "cs,en"},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        hit = data[0]
        return float(hit["lat"]), float(hit["lon"]), hit.get("display_name", query)
    except Exception as e:  # noqa: BLE001
        log.warning("geocode failed for %r: %s", query, e)
        return None
