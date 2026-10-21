"""Operator-only settings for outbound integrations; never exposed by settings APIs."""
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class IntegrationSettings(BaseSettings):
    DATA_ENCRYPTION_KEY: SecretStr = SecretStr("")
    OUTBOX_MAX_ATTEMPTS: int = Field(default=8, ge=1, le=30)
    OUTBOX_LEASE_SECONDS: int = Field(default=180, ge=60, le=600)
    INTEGRATION_PRIVATE_HOSTS: str = ""
    OIDC_HTTP_TIMEOUT: int = Field(default=15, ge=1, le=60)
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: SecretStr = SecretStr("")
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


integration_settings = IntegrationSettings()
