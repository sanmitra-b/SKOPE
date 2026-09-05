from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class UserContext(BaseModel):
    uid: str
    email: str
    display_name: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["OPERATIONS_USER"])
    auth_mode: str


class Evidence(BaseModel):
    evidence_id: str
    evidence_type: Literal["document", "sql", "derived"]
    title: str
    excerpt: str | None = None
    document_id: str | None = None
    source_path: str | None = None
    page: int | None = None
    section: str | None = None
    query_id: str | None = None
    sql: str | None = None
    rows: list[dict[str, Any]] | None = None
    data_as_of: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Claim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    supported: bool = False


class AgentResult(BaseModel):
    agent: str
    status: Literal["completed", "failed", "skipped", "partial"]
    answer_fragment: str = ""
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timing_ms: int = 0


class ChatRequest(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    conversation_id: UUID | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)


class RoutingDecision(BaseModel):
    intent: str
    agents: list[str]
    requires_documents: bool
    requires_sql: bool
    reason: str


class ChatResponse(BaseModel):
    request_id: UUID
    conversation_id: UUID
    answer: str
    routing: RoutingDecision
    agents: list[AgentResult]
    claims: list[Claim]
    evidence: list[Evidence]
    warnings: list[str]
    supported_claim_rate: float
    data_as_of: str | None = None
    latency_ms: int
    timings_ms: dict[str, int] = Field(default_factory=dict)
    provider_metrics: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class IndexStatus(BaseModel):
    collection: str
    corpus_version: str
    postgres_chunks: int
    qdrant_points: int
    production_documents: int
    total_source_records: int = 0
    last_run: dict[str, Any] | None = None


class FlagStatusUpdate(BaseModel):
    status: Literal["OPEN", "REVIEWED", "DISMISSED", "RESOLVED"]
