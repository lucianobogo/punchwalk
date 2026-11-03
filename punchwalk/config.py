"""Runtime settings, loaded from `.env` and the environment.

Secrets are stored as `SecretStr`, so `repr()`, logging and `model_dump()` never show key values.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent

# SAM 3.1 concept for the inspector's pointing hand (PLAN 1.2). PROVISIONAL: `make record` compares
# "hand", "index finger" and "pointing hand" on real pointing frames and keeps the best (PLAN 0.6).
DEFAULT_HAND_CONCEPT = "hand"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Credentials
    MODEL_API_KEY: SecretStr = SecretStr("")
    BASETEN_API_KEY: SecretStr = SecretStr("")
    SLACK_WEBHOOK_URL: SecretStr = SecretStr("")
    RESEND_API_KEY: SecretStr = SecretStr("")
    FLY_API_TOKEN: SecretStr = SecretStr("")
    EMAIL_FROM: str = ""

    # Model routing
    REASONING_PROVIDER: Literal["baseten", "meta"] = "baseten"
    META_API_BASE: str = "https://api.meta.ai/v1"
    BASETEN_API_BASE: str = "https://inference.baseten.co/v1"
    SPARK_OPEN_MODEL: str = "meta-models/muse-spark-open-1026"
    # Tried in order when SPARK_OPEN_MODEL returns 404 (the -1026 hackathon slug 404'd on
    # Nov 3 while the public catalog slug worked; see fixtures/probe/summary.json).
    SPARK_OPEN_MODEL_ALTERNATES: list[str] = ["meta-models/muse-spark-open"]
    SPARK_FALLBACK_MODEL: str = "muse-spark-1.3"
    SAM_MODEL: str = "sam-3.1"
    ASR_MODEL: str = "muse-voice-transcribe-1.0"
    HTTP_TIMEOUT_S: float = 300.0

    # App
    DATA_DIR: Path = REPO_ROOT / "data"
    APP_BASE_URL: str = "http://localhost:8000"
    DEMO_MODE: bool = False

    # Cache
    PUNCHWALK_CACHE: Literal["readwrite", "readonly", "off"] = "readwrite"
    PUNCHWALK_CACHE_DIR: Path | None = None  # default: DATA_DIR/cache
    FIXTURE_CACHE_DIR: Path = REPO_ROOT / "fixtures" / "cache"

    # Pipeline
    # point+voice: pointed items use the fingertip; voice_only: every item takes the voice-only path
    GROUNDING_MODE: Literal["point+voice", "voice_only"] = "point+voice"
    SAM_HAND_CONCEPT: str = DEFAULT_HAND_CONCEPT
    COST_CAP_USD: float = Field(default=25.0, ge=0)

    @field_validator("GROUNDING_MODE", mode="before")
    @classmethod
    def _legacy_grounding_mode(cls, v):
        """A `.env` copied before the pointing change says `tape+voice`; it means `point+voice` now."""
        return "point+voice" if isinstance(v, str) and v.strip() == "tape+voice" else v

    @field_validator("PUNCHWALK_CACHE_DIR", mode="before")
    @classmethod
    def _empty_is_none(cls, v):
        return None if v is None or (isinstance(v, str) and not v.strip()) else v

    @field_validator("DATA_DIR", "PUNCHWALK_CACHE_DIR", "FIXTURE_CACHE_DIR", mode="after")
    @classmethod
    def _anchor_relative(cls, v: Path | None) -> Path | None:
        """Relative paths are relative to the repo root, not the current directory."""
        return None if v is None else (v if v.is_absolute() else REPO_ROOT / v)

    @property
    def cache_dir(self) -> Path:
        return Path(self.PUNCHWALK_CACHE_DIR or (Path(self.DATA_DIR) / "cache"))

    @property
    def ledger_path(self) -> Path:
        return Path(self.DATA_DIR) / "cost_ledger.jsonl"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    """Drop the cached Settings (tests and scripts that change the environment call this)."""
    get_settings.cache_clear()
