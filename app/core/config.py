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
    REDIS_URL: str = "redis://localhost:6379/0"
    GENERATION_QUEUE_NAME: str = "generation"
    JOB_EAGER: bool = False
    VSELLM_API_KEY: SecretStr = SecretStr("")
    VSELLM_BASE_URL: str = "https://api.vsellm.ru/v1"
    AI_MODEL_FAST: str = "openai/gpt-5.4-mini"
    AI_MODEL_REASONING: str = "openai/gpt-5.4-mini"
    AI_MODEL_WRITER: str = "openai/gpt-5.4-mini"
    AI_MODEL_CRITIC: str = "anthropic/claude-sonnet-4.6"
    AI_MODEL_FALLBACK: str = "google/gemini-2.5-flash"
    AI_MODEL_EMBEDDING: str = "openai/text-embedding-3-small"
    AI_REQUEST_TIMEOUT_SECONDS: int = Field(default=90, ge=5, le=300)
    AI_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=5)
    AI_MAX_CALLS_PER_RUN: int = Field(default=180, ge=1, le=1000)
    AI_MAX_TOKENS_PER_RUN: int = Field(default=600000, ge=1000)
    AI_MAX_INPUT_TOKENS: int = Field(default=64000, ge=1000)
    AI_CACHE_TTL_SECONDS: int = Field(default=86400, ge=0)
    AI_INGESTION_BATCH_CHARS: int = Field(default=10000, ge=2000, le=40000)
    AI_MAX_SOURCE_CHARS: int = Field(default=500000, ge=10000)
    AI_MAX_REVISIONS: int = Field(default=2, ge=0, le=4)
    AI_QA_MIN_SCORE: float = Field(default=0.8, ge=0, le=1)
    AI_CHECKPOINT_SQLITE_PATH: Path = Path("uploads/ai-checkpoints.sqlite")
    AI_JOB_TIMEOUT_SECONDS: int = Field(default=3600, ge=300)
    AI_OCR_ENABLED: bool = True
    AI_OCR_MAX_PAGES: int = Field(default=30, ge=1, le=200)
    AI_CHAT_CONTEXT_TOKENS: int = Field(default=12000, ge=2000)

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
