import os
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PROFILE = os.getenv("ENV", "dev")

class Settings(BaseSettings):
    # Основные секции конфига
    ENV: str = "dev" # dev/stage/prod
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
    # Откуда брать .env
    model_config = SettingsConfigDict(
        env_file=f".env.{ENV_PROFILE}",  # выбирает нужный файл профиля
        env_file_encoding='utf-8'
    )

settings = Settings()