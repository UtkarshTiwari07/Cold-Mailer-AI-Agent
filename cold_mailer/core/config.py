"""Typed configuration, validated at process start.

Everything is a `pydantic-settings` model so a bad `.env` fails immediately
with a clear message instead of surfacing as a mysterious runtime error three
stages into a pipeline run. Nested settings use the `CM_SECTION__FIELD`
env-var convention (see `.env.example`).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CM_LLM__")

    # Live DeepSeek V4 model IDs. `deepseek-chat` / `deepseek-reasoner` were
    # retired 2026-07-24 15:59 UTC and now return HTTP 400 — do not revert to
    # them even though most tutorials still reference the old names.
    flash_model: str = "deepseek/deepseek-v4-flash"
    pro_model: str = "deepseek/deepseek-v4-pro"
    api_base: str = "https://api.deepseek.com"
    api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")

    max_spend_usd: float = 25.0
    request_timeout_s: float = 60.0
    max_retries: int = 3

    # Deterministic canned responses, no network call. Powers `make demo`
    # and the test suite so the whole pipeline runs with zero credentials.
    stub: bool = False


class DBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CM_DB__")

    dsn: str = "postgresql://coldmailer:coldmailer@localhost:5432/coldmailer"
    pool_min: int = 2
    pool_max: int = 10


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CM_REDIS__")

    url: str = "redis://localhost:6379/0"
    llm_cache_ttl_s: int = 60 * 60 * 24 * 30  # 30 days — research doesn't go stale fast


class SearchSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CM_SEARCH__")

    searxng_url: str = "http://localhost:8888"
    serper_api_key: str = Field(default="", alias="SERPER_API_KEY")
    max_results: int = 8


class DeliverySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CM_DELIVERY__")

    transport: str = "console"  # console | gmail | smtp
    from_email: str = ""
    from_name: str = ""
    gmail_client_secrets: str = "secrets/credentials.json"
    gmail_token_path: str = "secrets/gmail_token.json"

    # --- Deliverability safety rails. Deliberately hardcoded defaults, not
    # just documentation: A8 refuses to exceed these regardless of config
    # unless CM_DELIVERY__ALLOW_UNSAFE_OVERRIDE is set (still requires an
    # explicit flag, so raising the cap always leaves a paper trail).
    warmup_daily_caps: list[int] = Field(
        default_factory=lambda: [10, 10, 10, 10, 10, 10, 10,  # week 1
                                  20, 20, 20, 20, 20, 20, 20,  # week 2
                                  30, 30, 30, 30, 30, 30, 30]  # week 3+
    )
    steady_state_cap: int = 40
    bounce_rate_halt_threshold: float = 0.03  # 3% rolling hard-bounce rate
    bounce_rate_window: int = 50  # over the trailing N sends
    business_hours: tuple[int, int] = (9, 18)
    send_weekdays_only: bool = True
    min_jitter_s: int = 45
    max_jitter_s: int = 240
    allow_unsafe_override: bool = False


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CM_OBS__")

    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="http://localhost:3000", alias="LANGFUSE_HOST")
    log_level: str = "INFO"
    log_json: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    db: DBSettings = Field(default_factory=DBSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    delivery: DeliverySettings = Field(default_factory=DeliverySettings)
    obs: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    profile_path: Path = REPO_ROOT / "profile" / "utkarsh.yaml"
    prompts_dir: Path = REPO_ROOT / "prompts"
    migrations_dir: Path = REPO_ROOT / "migrations"


@lru_cache
def get_settings() -> Settings:
    return Settings()
