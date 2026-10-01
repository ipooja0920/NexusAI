"""Load config.yaml and resolve paths relative to the project root."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


DEFAULT_FUNDING = {
    "signals": {"formd_recent": {"lo": 0.5, "hi": 20}, "cash_total": {"lo": 5, "hi": 100},
                "rd_expense": {"lo": 1, "hi": 50}, "sbir_active": {"lo": 0.1, "hi": 2}},
    "oa_funded_from": 2025,
    "recency": {"fresh_year": 2025, "fresh": 1.0, "recent": 0.6, "stale": 0.25},
    "missing_score": 0.5, "formd_raised_bonus": 0.05, "formd_raised_revenue_max": 100,
}

DEFAULT_ACADEMIC = {
    "bonus": {"oa_funded_university": 0.10, "sbir_university_past": 0.05,
              "ct_university_past": 0.05, "oa_coauthored_university": 0.05,
              "sbir_awarded": 0.03},
    "bonus_cap": 0.15,
    "penalty": {"sbir_university_current": -0.05, "ct_university_current": -0.05},
    "penalty_cap": -0.10,
    "ct_active_penalty": 0.0, "ct_active_above": 10,
}


class Settings:
    def __init__(self, config_path=None):
        # Resolved at call time (not import time) so the config location can
        # be redirected -- by tests, or via the NEXUSAI_CONFIG env var when
        # running the same code against a different project folder.
        if config_path is None:
            env_path = os.getenv("NEXUSAI_CONFIG", "").strip()
            config_path = Path(env_path) if env_path else CONFIG_PATH
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(
                f"config.yaml not found at {config_path}. "
                "It must sit next to enrich_companies.py / match_faculty.py."
            )
        self.config_path = config_path
        with open(config_path, "r", encoding="utf-8") as fh:
            self.raw = yaml.safe_load(fh)

        # Load .env (OPENAI_API_KEY) from the project root if present
        load_dotenv(PROJECT_ROOT / ".env")

    # ---------- paths ----------
    def path(self, key: str, default: str = None) -> Path:
        """Resolve a configured path. `default` covers keys added after a
        config file was written, so an older config.yaml keeps working
        instead of dying with a KeyError."""
        raw = self.raw.get("paths", {}).get(key, default)
        if raw is None:
            raise KeyError(
                f"config.yaml has no paths.{key} entry, and there is no "
                f"default for it.")
        p = Path(raw)
        # Relative paths resolve against the folder holding config.yaml
        return p if p.is_absolute() else self.config_path.parent / p

    @property
    def companies_raw(self) -> Path: return self.path("companies_raw")

    @property
    def companies_enriched(self) -> Path: return self.path("companies_enriched")

    @property
    def faculty_database(self) -> Path: return self.path("faculty_database")

    @property
    def output_dir(self) -> Path:
        p = self.path("output_dir"); p.mkdir(parents=True, exist_ok=True); return p

    @property
    def cache_dir(self) -> Path:
        p = self.path("cache_dir"); p.mkdir(parents=True, exist_ok=True); return p

    @property
    def sources_dir(self) -> Path:
        """Reference data downloaded from outside (SEC Form D, SBIR,
        OpenAlex). Re-downloadable, never edited, never written by us."""
        p = self.path("sources_dir", "data/sources")
        p.mkdir(parents=True, exist_ok=True); return p

    @property
    def review_dir(self) -> Path:
        """Workbooks that exist so a human can check name-matching quality
        before trusting the columns it produced."""
        p = self.path("review_dir", "data/review")
        p.mkdir(parents=True, exist_ok=True); return p

    # ---------- sections ----------
    @property
    def capitaliq(self) -> dict: return self.raw["capitaliq"]

    @property
    def faculty(self) -> dict: return self.raw["faculty"]

    @property
    def openai(self) -> dict: return self.raw["openai"]

    @property
    def prefilter(self) -> dict: return self.raw["prefilter"]

    @property
    def funnel(self) -> dict: return self.raw["funnel"]

    @property
    def weights(self) -> dict: return self.raw["weights"]

    @property
    def components(self) -> dict: return self.raw["components"]

    @property
    def funding(self) -> dict:
        """Funding-score thresholds. Defaults kept here so an older
        config.yaml without a `funding:` block still scores rather than
        raising KeyError halfway through a run."""
        f = self.raw.get("funding") or {}
        return f if "signals" in f else DEFAULT_FUNDING

    @property
    def academic(self) -> dict:
        """Academic track-record adjustment to the partnership score."""
        return self.raw.get("academic") or DEFAULT_ACADEMIC

    @property
    def geocoding(self) -> dict: return self.raw["geocoding"]

    def require_openai_key(self) -> str:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and add "
                "your key, or export it in your shell."
            )
        return key
