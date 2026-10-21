from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator, field_validator, AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

class AIRoutingCandidate(BaseModel):
    """Operator-approved provider/model route and its text-token price band."""

    model: str = Field(min_length=1, max_length=255)
    provider: str = "vsellm"
    roles: list[str] = Field(min_length=1)
    approved: bool = False
    tier: int = Field(default=0, ge=0, le=10)
    input_rub_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_rub_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    context_window: int = Field(gt=0)
    json_mode: bool = True
    independent_review: bool = False
    vision: bool = False
    extra_body: dict = Field(default_factory=dict)


class AIProvider(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    base_url: AnyHttpUrl
    api_key_env: str = Field(default="", pattern=r"^$|^[A-Z][A-Z0-9_]*$")
    local: bool = False
    enabled: bool = True
    timeout_seconds: int = Field(default=60, ge=1, le=300)

    @model_validator(mode="after")
    def endpoint_policy(self):
        if not self.local and self.base_url.scheme != "https":
            raise ValueError("Remote AI providers require HTTPS")
        if self.base_url.username or self.base_url.password or self.base_url.query:
            raise ValueError("Provider URLs must not contain credentials or query parameters")
        return self


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
    REGISTRATION_ENABLED: bool = True
    CONTACT_REQUESTS_ENABLED: bool = True
    PUBLIC_APP_URL: str = "http://localhost:5173"
    AUTH_RATE_LIMIT_PER_MINUTE: int = Field(default=20, ge=1, le=1000)
    AUTH_RATE_LIMIT_ENABLED: bool = True
    SMTP_FROM: str = ""
    SMTP_STARTTLS: bool = True

    CORS_ORIGINS: str = "http://localhost"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    UPLOAD_DIR: Path = Path("uploads")
    MAX_DOCUMENT_SIZE_MB: int = Field(default=25, gt=0)
    # Backward-compatible override for existing deployments. New environments
    # should use MAX_DOCUMENT_SIZE_MB.
    MAX_UPLOAD_BYTES: int | None = Field(default=None, gt=0)
    DOCUMENT_MAX_UNPACKED_BYTES: int = Field(default=100 * 1024 * 1024, gt=0)
    DOCUMENT_MAX_ARCHIVE_ENTRIES: int = Field(default=5000, gt=0)
    DOCUMENT_MAX_PAGES: int = Field(default=1000, gt=0)
    DOCUMENT_PROCESSING_TIMEOUT_SECONDS: int = Field(default=120, ge=1, le=600)
    DOCUMENT_MAX_EXTRACTED_CHARS: int = Field(default=2_000_000, gt=0)
    IMAGE_MAX_BYTES: int = Field(default=10 * 1024 * 1024, gt=0)
    IMAGE_MAX_PIXELS: int = Field(default=20_000_000, gt=0)
    IMAGE_PROVIDER: str = ""
    IMAGE_MODEL: str = ""
    IMAGE_MONTHLY_LIMIT: int = Field(default=50, ge=0)
    AI_LOCAL_ONLY: bool = False
    EMBEDDING_PROVIDER: str = "vsellm"
    AI_CHAT_RETENTION_DAYS: int = Field(default=90, ge=1, le=3650)
    AI_CHAT_MONTHLY_LIMIT: int = Field(default=1000, ge=0)
    AI_ORG_QUEUED_LIMIT: int = Field(default=8, ge=0)
    AI_INSTALL_QUEUED_LIMIT: int = Field(default=64, ge=0)
    AI_ORG_BACKGROUND_LIMIT: int = Field(default=2, ge=1)
    AI_INSTALL_BACKGROUND_LIMIT: int = Field(default=4, ge=1)
    AI_ORG_INTERACTIVE_LIMIT: int = Field(default=4, ge=1)
    AI_INSTALL_INTERACTIVE_LIMIT: int = Field(default=16, ge=1)
    AI_ORG_MONTHLY_TOKENS: int = Field(default=20_000_000, ge=0)
    AI_INSTALL_MONTHLY_TOKENS: int = Field(default=200_000_000, ge=0)
    AI_ORG_MONTHLY_OCR_PAGES: int = Field(default=10_000, ge=0)
    AI_INSTALL_MONTHLY_OCR_PAGES: int = Field(default=100_000, ge=0)
    AI_ORG_MONTHLY_RUB: Decimal = Field(default=Decimal("10000"), ge=0, allow_inf_nan=False)
    AI_INSTALL_MONTHLY_RUB: Decimal = Field(default=Decimal("100000"), ge=0, allow_inf_nan=False)
    AI_MAX_RUB_PER_RUN: Decimal = Field(default=Decimal("1000"), ge=0, allow_inf_nan=False)
    # Remote images/embeddings require operator-approved prices. Local endpoints
    # have zero external provider spend but still consume concurrency/token caps.
    IMAGE_PRICE_RUB: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    EMBEDDING_RUB_PER_MILLION: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    MAX_COURSE_MODULES: int = Field(default=100, ge=1)
    MAX_COURSE_LESSONS: int = Field(default=500, ge=1)
    MAX_LESSON_CONTENT_CHARS: int = Field(default=200_000, ge=1000)
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
    AI_ROUTING_MODE: Literal["off", "shadow", "adaptive"] = "adaptive"
    AI_ROUTING_CANDIDATES: list[AIRoutingCandidate] = Field(default_factory=list)
    AI_PROVIDERS: list[AIProvider] = Field(default_factory=list)
    AI_PROVIDER_COOLDOWN_SECONDS: int = Field(default=60, ge=1, le=3600)
    AI_TOKEN_FORECAST_PATH: Path = Path("app/ai/data/token_forecast.json")
    AI_ROUTING_MIN_SAMPLES: int = Field(default=10, ge=3, le=1000)
    AI_ROUTING_MIN_PASS_RATE: float = Field(default=0.85, gt=0, le=1)
    AI_ROUTING_HISTORY_LIMIT: int = Field(default=200, ge=10, le=1000)
    AI_ROUTING_COOLDOWN_SECONDS: int = Field(default=300, ge=0, le=3600)
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

    @model_validator(mode="after")
    def unique_routing_models(self):
        names = [(candidate.provider, candidate.model) for candidate in self.AI_ROUTING_CANDIDATES]
        if len(names) != len(set(names)):
            raise ValueError("AI_ROUTING_CANDIDATES must have unique provider/model pairs")
        providers = [provider.id for provider in self.AI_PROVIDERS]
        if len(providers) != len(set(providers)):
            raise ValueError("AI_PROVIDERS must have unique IDs")
        return self

    @field_validator("IMAGE_PRICE_RUB", "EMBEDDING_RUB_PER_MILLION", mode="before")
    @classmethod
    def blank_optional_price(cls, value):
        # Compose passes absent optional values as an empty string. Missing
        # pricing must disable paid dispatch, never turn into a zero price.
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def max_document_bytes(self) -> int:
        if self.MAX_UPLOAD_BYTES is not None:
            return self.MAX_UPLOAD_BYTES
        return self.MAX_DOCUMENT_SIZE_MB * 1024 * 1024

settings = Settings()
