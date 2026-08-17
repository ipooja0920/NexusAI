"""Persistent JSON caches: geocodes, embeddings, company-level AI scores.

Everything lives in data/cache/ as human-readable JSON. Caches make
datasource refreshes and repeated faculty runs cheap:
  - geocode.json     keyed by cleaned address string
  - embeddings.json  keyed by sha256 of the embedded text (so a changed
                     description automatically re-embeds)
  - company_scores.json  keyed by normalized company name; holds
                     faculty-independent partnership/funding scores
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
    def __init__(self, path: Path):
        self.path = path
        self._data: dict = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                # Corrupt cache -> start fresh rather than crash
                self._data = {}

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
        tmp.replace(self.path)


class Caches:
    def __init__(self, cache_dir: Path):
        self.geocode = JsonCache(cache_dir / "geocode.json")
        self.embeddings = JsonCache(cache_dir / "embeddings.json")
        self.company_scores = JsonCache(cache_dir / "company_scores.json")

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
    def get_company_scores(self, company: str):
        return self.company_scores.get(_norm_company(company))

    def set_company_scores(self, company: str, partnership: dict, funding: dict) -> None:
        self.company_scores.set(
            _norm_company(company),
            {"company": company, "partnership": partnership, "funding": funding},
        )

    def save_all(self) -> None:
        self.geocode.save()
        self.embeddings.save()
        self.company_scores.save()
