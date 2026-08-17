"""Address cleaning, geocoding (Nominatim -> ArcGIS fallback) and distance.

Logic carried over from ProgramTesting9, wrapped with the persistent
geocode cache so an address is never geocoded twice across runs.
"""
from __future__ import annotations

import os
import re
import time
from typing import List, Optional, Tuple

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from geopy.distance import geodesic
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import ArcGIS, Nominatim

PHONE_FAX_KEYS = ["main phone", "phone", "fax", "tel", "telephone"]


def select_address_block(raw_block: Optional[str]) -> Optional[str]:
    """Capital IQ 'Offices' cells can contain several blocks; prefer HQ."""
    if not isinstance(raw_block, str) or not raw_block.strip():
        return None
    blocks = re.split(r"\n\s*\n", raw_block.strip())
    blocks = [b.strip() for b in blocks if b.strip()]
    if len(blocks) <= 1:
        return raw_block
    for block in blocks:
        if "headquarters" in block.lower():
            return block
    return blocks[1] if len(blocks) >= 2 else blocks[0]


def clean_address_block(block) -> Optional[str]:
    """Convert a messy multi-line address block into one geocodable line."""
    if not isinstance(block, str):
        return None
    raw_lines: List[str] = [re.sub(r"\s+", " ", ln.strip()) for ln in block.splitlines()]
    keep: List[str] = []
    for ln in raw_lines:
        lower = ln.lower().strip()
        if not lower:
            continue
        if "headquarters" in lower:
            continue
        if lower in ("united states", "usa", "u.s.", "us"):
            continue
        if any(key in lower for key in PHONE_FAX_KEYS):
            continue
        keep.append(ln)
    if not keep:
        return None
    addr = ", ".join(keep)
    addr = re.sub(r"\s*,\s*", ", ", addr)
    addr = re.sub(r"\s{2,}", " ", addr)
    addr = addr.strip(" ,;")
    if addr and not re.search(r"\b(united states|usa|u\.s\.)\b", addr, flags=re.I):
        addr = f"{addr}, USA"
    return addr or None


class Geocoder:
    def __init__(self, user_agent: str, rate_limit_seconds: float, caches):
        self.caches = caches
        min_delay = max(1.05, float(rate_limit_seconds or 1.05))
        nom = Nominatim(user_agent=user_agent, timeout=15)
        self._nominatim = RateLimiter(
            nom.geocode, min_delay_seconds=min_delay, swallow_exceptions=False
        )
        self._arcgis = ArcGIS(timeout=15)

    def geocode(self, cleaned_address: str) -> Tuple[Optional[float], Optional[float], str]:
        """Return (lat, lon, provider). Checks cache first; caches results
        including failures (provider='failed') so bad addresses aren't
        retried on every refresh."""
        if not cleaned_address:
            return None, None, "no-address"

        cached = self.caches.get_geocode(cleaned_address)
        if cached is not None:
            return cached.get("lat"), cached.get("lon"), cached.get("provider") or "cache"

        lat, lon, provider = self._geocode_live(cleaned_address)
        self.caches.set_geocode(cleaned_address, lat, lon, provider)
        return lat, lon, provider

    def _geocode_live(self, address: str):
        try:
            loc = self._nominatim(address)
            if loc and getattr(loc, "latitude", None) and getattr(loc, "longitude", None):
                return float(loc.latitude), float(loc.longitude), "nominatim"
        except Exception:
            pass
        time.sleep(0.3)
        try:
            loc = self._arcgis.geocode(address)
            if loc and getattr(loc, "latitude", None) and getattr(loc, "longitude", None):
                return float(loc.latitude), float(loc.longitude), "arcgis"
        except Exception:
            pass
        return None, None, "failed"


def distance_miles(lat: Optional[float], lon: Optional[float],
                   reference: Tuple[float, float]) -> Optional[float]:
    if lat is None or lon is None:
        return None
    try:
        return round(geodesic((lat, lon), tuple(reference)).miles, 2)
    except Exception:
        return None
