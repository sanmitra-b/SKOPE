from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", PROJECT_ROOT / "frontend" / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "SKOPE API"
    app_version: str = "0.1.0-local"
    environment: str = "local"
    database_url: str = "postgresql://skope_admin:skope_pass_secure@localhost:5432/skope_db"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "skope_chunks_v1"
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    embedding_execution_provider: str = "CPUExecutionProvider"
    sparse_embedding_model: str = "Qdrant/bm25"
    reranker_model: str = "BAAI/bge-reranker-base"
    corpus_version: str = "skope-corpus-v1"
    auth_mode: str = "dev"
    dev_auth_token: str = "skope-local-dev-token"
    firebase_project_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FIREBASE_PROJECT_ID", "VITE_FIREBASE_PROJECT_ID"),
    )
    firebase_service_account_json: str | None = None
    firebase_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FIREBASE_API_KEY", "VITE_FIREBASE_API_KEY"),
    )
    firebase_auth_domain: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FIREBASE_AUTH_DOMAIN", "VITE_FIREBASE_AUTH_DOMAIN"),
    )
    firebase_app_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FIREBASE_APP_ID", "VITE_FIREBASE_APP_ID"),
    )
    llm_provider: str = "gemini"
    gemini_api_key: str | None = None
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    llm_model: str = "gemini-3.5-flash-lite"
    llm_request_timeout_seconds: float = Field(default=8.0, ge=1.0, le=60.0)
    llm_total_budget_seconds: float = Field(default=10.0, ge=1.0, le=120.0)
    llm_max_retries: int = Field(default=1, ge=0, le=2)
    llm_circuit_breaker_failures: int = Field(default=2, ge=1, le=10)
    llm_circuit_breaker_cooldown_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    max_retrieval_results: int = Field(default=8, ge=1, le=30)
    reranker_candidate_limit: int = Field(default=12, ge=4, le=50)
    reranker_enabled: bool = True
    prewarm_models: bool = True
    sql_timeout_ms: int = Field(default=5000, ge=500, le=30000)
    sql_row_limit: int = Field(default=200, ge=1, le=2000)
    chat_rate_limit_per_minute: int = Field(default=20, ge=1, le=600)
    dataset_root: Path = PROJECT_ROOT / "RAG Project Dataset"

@lru_cache
def get_settings() -> Settings:
    return Settings()
