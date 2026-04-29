"""Configuration management via pydantic-settings."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


class Settings(BaseSettings):
    """Central configuration loaded from env vars / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AFRI_",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Environment ──────────────────────────────────────────────
    env: Environment = Environment.DEV
    debug: bool = False
    log_level: str = "INFO"
    secret_key: str = "change-me-in-production"

    # ── API Server ───────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    cors_origins: list[str] = ["*"]

    # ── Redis (Session State — Tier 1) ───────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    redis_ttl_seconds: int = 86400  # 24h

    # ── Postgres (Episodic Memory — Tier 2) ──────────────────────
    database_url: str = "postgresql+asyncpg://afriagent:afriagent@localhost:5432/afriagent"
    db_pool_size: int = 20
    db_pool_overflow: int = 10

    # ── Qdrant (Semantic Memory — Tier 3) ────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "afriagent_vectors"
    qdrant_vector_size: int = 1536  # text-embedding-3-small

    # ── LLM ──────────────────────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.OPENAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-20241022"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.3

    # ── Circuit Breaker ──────────────────────────────────────────
    circuit_breaker_fail_threshold: int = 5
    circuit_breaker_reset_seconds: int = 60

    # ── WhatsApp (Twilio) ────────────────────────────────────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = "whatsapp:+14155238886"

    # ── Telegram ─────────────────────────────────────────────────
    telegram_bot_token: str = ""

    # ── WHMCS ────────────────────────────────────────────────────
    whmcs_url: str = ""
    whmcs_identifier: str = ""
    whmcs_secret: str = ""

    # ── M-Pesa ───────────────────────────────────────────────────
    mpesa_consumer_key: str = ""
    mpesa_consumer_secret: str = ""
    mpesa_shortcode: str = ""
    mpesa_passkey: str = ""
    mpesa_callback_url: str = ""

    # ── Observability ────────────────────────────────────────────
    otel_exporter_endpoint: str = "http://localhost:4317"
    metrics_port: int = 9090

    # ── Response Validation ──────────────────────────────────────
    max_response_tokens: int = 500
    toxicity_threshold: float = 0.7
    confidence_threshold: float = 0.5

    # ── Self-Improvement ─────────────────────────────────────────
    learning_enabled: bool = True
    min_confidence_for_learning: float = 0.8
    few_shot_examples_limit: int = 5

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",")]
        return v  # type: ignore[return-value]

    @property
    def is_production(self) -> bool:
        return self.env == Environment.PROD


# Global singleton
settings = Settings()
