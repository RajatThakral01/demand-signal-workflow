"""Application configuration (pydantic-settings)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Reads environment variables (optionally from a local `.env` file).

    Field names map to the matching env vars case-insensitively, e.g. the
    `database_url` attribute reads `DATABASE_URL`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Required, no default -> fail fast if missing in-process. --------------
    database_url: str = ""

    # --- Credentials / integration (empty until the relevant phase uses them). -
    openrouter_api_key: str = ""
    admin_api_key: str = ""

    # --- Policy file selection -------------------------------------------------
    scoring_policy_version: str = "scoring_policy_v1.json"
    identity_policy_version: str = "identity_policy_v1.json"

    # --- Classification --------------------------------------------------------
    classification_model: str = "anthropic/claude-haiku-4.5"

    # --- Runtime ----------------------------------------------------------------
    app_env: str = Field(default="local")
    log_level: str = Field(default="INFO")

    # --- Retry / backoff (FR-11) ------------------------------------------------
    retry_max_attempts: int = Field(default=3, ge=1)
    retry_base_delay_ms: int = Field(default=500, ge=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()