import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ROOT = Path(__file__).resolve().parents[2]
VALID_ENV_PROFILES = {"dev", "stage", "prod"}


def resolve_env_profile(raw_value: str | None = None) -> str:
    profile = (raw_value or "dev").strip().lower()
    if profile not in VALID_ENV_PROFILES:
        allowed = ", ".join(sorted(VALID_ENV_PROFILES))
        raise ValueError(f"Unsupported ENV profile '{profile}'. Expected one of: {allowed}.")
    return profile


def resolve_env_file(profile: str, *, base_dir: Path | None = None) -> Path:
    env_file = (base_dir or APP_ROOT) / f".env.{profile}"
    return env_file


def normalize_database_url(raw_url: str) -> str:
    url = raw_url.strip()
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    return url


class Settings(BaseSettings):
    ENV: Literal["dev", "stage", "prod"] = "dev"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str

    JWT_SECRET: str
    JWT_ALG: str = "HS256"
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_MINUTES: int = 1440

    CORS_ORIGINS: str = "http://localhost"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    SMTP_HOST: str = ""
    SMTP_PORT: int = 25
    SMTP_USER: str = ""
    SMTP_PASS: str = ""

    HUGGINGCHAT_PROXY_URL: str = "http://ml-proxy:8001"

    HF_TOKEN: str = ""
    HF_MODEL: str = ""
    HF_API_URL: str = ""
    HF_MODEL_CANDIDATES: str = ""
    HF_DISCOVERY_LIMIT: int = 60
    HF_DISCOVERY_TIMEOUT_SECONDS: float = 6.0

    GIGA_CLIENT_ID: str = ""
    GIGA_CLIENT_SECRET: str = ""
    GIGA_SCOPE: str = "GIGACHAT_API_PERS"
    GIGA_OAUTH_TIMEOUT_SECONDS: float = 15.0
    GIGA_MODELS_TIMEOUT_SECONDS: float = 15.0

    LLM_REQUEST_TIMEOUT_SECONDS: float = 40.0
    LLM_RETRY_ATTEMPTS: int = 2
    LLM_RETRY_BACKOFF_SECONDS: float = 0.5

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return (value or "INFO").strip().upper()

    @field_validator(
        "HF_DISCOVERY_LIMIT",
        "LLM_RETRY_ATTEMPTS",
        mode="before",
    )
    @classmethod
    def normalize_int_settings(cls, value: int) -> int:
        normalized = int(value)
        if normalized < 0:
            raise ValueError("Integer settings must be non-negative.")
        return normalized

    @field_validator(
        "HF_DISCOVERY_TIMEOUT_SECONDS",
        "GIGA_OAUTH_TIMEOUT_SECONDS",
        "GIGA_MODELS_TIMEOUT_SECONDS",
        "LLM_REQUEST_TIMEOUT_SECONDS",
        "LLM_RETRY_BACKOFF_SECONDS",
        mode="before",
    )
    @classmethod
    def normalize_float_settings(cls, value: float) -> float:
        normalized = float(value)
        if normalized < 0:
            raise ValueError("Timeout and backoff settings must be non-negative.")
        return normalized

    @property
    def sync_database_url(self) -> str:
        return normalize_database_url(self.DATABASE_URL)


@lru_cache(maxsize=8)
def get_settings(env_profile: str | None = None, *, env_file: str | Path | None = None) -> Settings:
    profile = resolve_env_profile(env_profile or os.getenv("ENV"))
    selected_env_file = env_file or resolve_env_file(profile)
    return Settings(_env_file=selected_env_file)


settings = get_settings()
