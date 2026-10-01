"""Persistent JSON caches: geocodes, embeddings, company-level AI scores.

Everything lives in data/cache/ as human-readable JSON. Caches make
datasource refreshes and repeated faculty runs cheap:
  - geocode.json     keyed by cleaned address string
  - embeddings.json  keyed by sha256 of the embedded text (so a changed
                     description automatically re-embeds)
  - company_scores.json  keyed by company name + the text judged; holds
                     faculty-independent partnership scores
  - company_profiles.json  AI-written company profiles, keyed by source text
  - alignment_scores.json  AI alignment scores, keyed by faculty profile +
                     company text, so an interrupted run resumes for free
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def _norm_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _text_key(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class JsonCache:
    """A dict that persists to JSON, and only rewrites itself when it has
    actually changed.

    The dirty flag is not a micro-optimisation. embeddings.json holds a
    1536-float vector per company and reaches 724MB on a full datasource,
    and enrich_companies calls save_all() every 25 rows so an interrupt
    loses nothing. On a re-run where every geocode and embedding is already
    cached - the common case - that was 872 rewrites of an unchanged 724MB
    file, about 631GB of disk writes, and it was the ENTIRE cost of the run:
    2.5 hours of serialising data that nobody had modified.
    """

    def __init__(self, path: Path):
        self.path = path
        self._data: dict = {}
        self._dirty = False
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                # Corrupt cache -> start fresh rather than crash
                self._data = {}

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value) -> None:
        # Writing back an identical value is not a change. Callers only set
        # on a cache MISS today, but that is their business, not ours.
        if key in self._data and self._data[key] == value:
            return
        self._data[key] = value
        self._dirty = True

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def save(self, force: bool = False) -> None:
        """Write the cache out, unless nothing has changed since it loaded."""
        if not self._dirty and not force and self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # indent=1 on a 724MB file costs real time and buys nothing - these
        # are sha256 keys and float vectors, not something anyone reads.
        tmp.write_text(json.dumps(self._data), encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = False


class Caches:
    def __init__(self, cache_dir: Path):
        self.geocode = JsonCache(cache_dir / "geocode.json")
        self.embeddings = JsonCache(cache_dir / "embeddings.json")
        self.company_scores = JsonCache(cache_dir / "company_scores.json")
        self.profiles = JsonCache(cache_dir / "company_profiles.json")
        self.alignment = JsonCache(cache_dir / "alignment_scores.json")

    # ---------- geocode ----------
    def get_geocode(self, cleaned_address: str):
        return self.geocode.get(cleaned_address)

    def set_geocode(self, cleaned_address: str, lat, lon, provider) -> None:
        self.geocode.set(cleaned_address, {"lat": lat, "lon": lon, "provider": provider})

    # ---------- embeddings ----------
    def get_embedding(self, text: str):
        return self.embeddings.get(_text_key(text))

    def set_embedding(self, text: str, vector: list) -> None:
        self.embeddings.set(_text_key(text), vector)

    # ---------- company secondary scores ----------
    # Keyed by company AND the text the score was judged from, so a new or
    # changed company profile gets a fresh partnership score instead of the
    # one judged from the old description.
    @staticmethod
    def _score_key(company: str, text: str = None) -> str:
        key = _norm_company(company)
        return f"{key}|{_text_key(text)[:16]}" if text else key

    def get_company_scores(self, company: str, text: str = None):
        return self.company_scores.get(self._score_key(company, text))

    def set_company_scores(self, company: str, partnership: dict, funding: dict,
                           text: str = None) -> None:
        self.company_scores.set(
            self._score_key(company, text),
            {"company": company, "partnership": partnership, "funding": funding},
        )

    # ---------- AI company profiles (keyed by their source text) ----------
    def get_profile(self, key: str):
        return self.profiles.get(_text_key(key))

    def set_profile(self, key: str, profile: str) -> None:
        self.profiles.set(_text_key(key), profile)

    # ---------- alignment scores (faculty profile x company text) ----------
    def get_alignment(self, key: str):
        return self.alignment.get(_text_key(key))

    def set_alignment(self, key: str, value: dict) -> None:
        self.alignment.set(_text_key(key), value)

    def save_all(self) -> None:
        self.geocode.save()
        self.embeddings.save()
        self.company_scores.save()
        self.profiles.save()
        self.alignment.save()
