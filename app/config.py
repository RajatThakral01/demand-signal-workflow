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

    # --- Credentials / integration (empty until the relevant phase uses them). --
    # ADMIN_API_KEY is REQUIRED (no default): it is the only auth gate, so the
    # app must fail fast at import rather than silently accept a fallback value.
    openrouter_api_key: str = ""
    admin_api_key: str

    # --- Policy file selection -------------------------------------------------
    scoring_policy_version: str = "scoring_policy_v1.json"
    identity_policy_version: str = "identity_policy_v1.json"

    # --- Classification --------------------------------------------------------
    # Pinned model accessed via OpenRouter. Dev default is DeepSeek V4 Flash
    # (cheaper than the PRD example string; see AI_USAGE.md for the deviation).
    # llm_provider selects the LIVE classification backend: "openrouter" (default,
    # via https://openrouter.ai/api/v1) or "groq" (via https://api.groq.com/openai/v1).
    # Both use the OpenAI-compatible chat.completions shape, so switching is a
    # provider-abstraction change, not a rewrite of classification logic.
    classification_model: str = "deepseek/deepseek-v4-flash"
    llm_provider: str = Field(default="openrouter")
    groq_api_key: str = ""
    # Minimum free-text length (in tokens) before the LLM is worth calling (FR-4).
    # Shorter text classifies as `unknown` WITHOUT calling the LLM. The gate is
    # intentionally narrow (pure noise like "hi"/"test", not short but real intent):
    # at ~$0.000026/call the saving from dropping "want a quote" is not worth the
    # recall loss.
    interpret_min_tokens: int = Field(default=2, ge=1)

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