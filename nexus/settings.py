"""Load config.yaml and resolve paths relative to the project root."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


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
    def path(self, key: str) -> Path:
        p = Path(self.raw["paths"][key])
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
    def geocoding(self) -> dict: return self.raw["geocoding"]

    def require_openai_key(self) -> str:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and add "
                "your key, or export it in your shell."
            )
        return key
