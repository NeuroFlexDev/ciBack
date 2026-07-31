from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Основные секции конфига
    ENV: str = "dev" # dev/stage/prod
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str
    JWT_SECRET: SecretStr = Field(min_length=32)
    JWT_ALG: Literal["HS256"] = "HS256"
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_MINUTES: int = 1440

    CORS_ORIGINS: str = "http://localhost"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    UPLOAD_DIR: Path = Path("uploads")
    MAX_DOCUMENT_SIZE_MB: int = Field(default=25, gt=0)
    # Backward-compatible override for existing deployments. New environments
    # should use MAX_DOCUMENT_SIZE_MB.
    MAX_UPLOAD_BYTES: int | None = Field(default=None, gt=0)
    CHAT_HISTORY_MESSAGES: int = Field(default=20, gt=0, le=200)
    DOCUMENT_CHUNK_CHARS: int = Field(default=2000, ge=200, le=20000)
    DOCUMENT_CHUNK_OVERLAP_CHARS: int = Field(default=200, ge=0, le=5000)
    GRAPH_CONTEXT_MAX_CHARS: int = Field(default=60000, ge=1000, le=500000)
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    SMTP_HOST: str = ""
    SMTP_PORT: int = 25
    SMTP_USER: str = ""
    SMTP_PASS: str = ""

    HUGGINGCHAT_PROXY_URL: str = "http://ml-proxy:8001"
    HF_TOKEN: str = ""
    HF_MODEL: str = ""
    # Локальная разработка использует один неотслеживаемый .env. В CI и
    # deployment переменные окружения имеют приоритет над файлом.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def max_document_bytes(self) -> int:
        if self.MAX_UPLOAD_BYTES is not None:
            return self.MAX_UPLOAD_BYTES
        return self.MAX_DOCUMENT_SIZE_MB * 1024 * 1024

settings = Settings()
