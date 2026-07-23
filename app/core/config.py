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
    MAX_UPLOAD_BYTES: int = Field(default=50 * 1024 * 1024, gt=0)

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

settings = Settings()
