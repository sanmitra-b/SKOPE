"""Build and persist one evidence-grounded chat response."""

from __future__ import annotations

import time
from collections.abc import Callable
from uuid import uuid4

from fastapi import HTTPException

from ..config import get_settings
from ..models import ChatRequest, ChatResponse, Evidence, UserContext
from ..orchestrator import run_orchestrator
from ..persistence import get_or_create_conversation, now_utc, save_chat
from ..policy import PolicyViolation, check_query


def _unique_evidence(state: dict) -> list[Evidence]:
    """Return evidence once, preserving the orchestrator's source order."""
    evidence_by_id: dict[str, Evidence] = {}
    for item in [*state.get("document_evidence", []), *state.get("sql_evidence", [])]:
        evidence_by_id.setdefault(item.evidence_id, item)
    return list(evidence_by_id.values())


def build_chat_response(
    request: ChatRequest,
    user: UserContext,
    progress: Callable[[str, str], None] | None = None,
) -> ChatResponse:
    """Validate, execute, normalize, and persist a chat request."""
    settings = get_settings()
    started = time.perf_counter()

    try:
        check_query(request.query)
    except PolicyViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        conversation_id = get_or_create_conversation(
            user,
            request.conversation_id,
            request.query,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    state = run_orchestrator(
        request.query,
        top_k=request.top_k or settings.max_retrieval_results,
        progress=progress,
    )
    evidence = _unique_evidence(state)
    claims = state.get("verified_claims", [])
    supported_claims = sum(claim.supported for claim in claims)
    support_rate = supported_claims / len(claims) if claims else 0.0
    evidence_dates = [item.data_as_of for item in evidence if item.data_as_of]

    response = ChatResponse(
        request_id=uuid4(),
        conversation_id=conversation_id,
        answer=state["answer"],
        routing=state["routing"],
        agents=state.get("agent_results", []),
        claims=claims,
        evidence=evidence,
        warnings=state.get("warnings", []),
        supported_claim_rate=round(support_rate, 4),
        data_as_of=max(evidence_dates) if evidence_dates else None,
        latency_ms=int((time.perf_counter() - started) * 1_000),
        timings_ms=state.get("timings_ms", {}),
        provider_metrics=state.get("provider_metrics", []),
        created_at=now_utc(),
    )
    save_chat(user=user, query=request.query, response=response)
    return response
