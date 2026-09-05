"""Bounded LangGraph workflow for routing, parallel evidence, and one synthesis."""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from functools import lru_cache
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from .agent_reasoning import synthesize_answer
from .models import AgentResult, Claim, Evidence, RoutingDecision
from .query_planning import UnsupportedBusinessQuestion
from .retrieval import retrieve
from .routing import AGENTS, route_query
from .text_to_sql import answer_with_sql


ProgressCallback = Callable[[str, str], None]


@lru_cache(maxsize=1)
def _evidence_executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=4, thread_name_prefix="skope-evidence")


class OrchestratorState(TypedDict, total=False):
    query: str
    top_k: int
    collection: str | None
    progress: ProgressCallback | None
    routing: RoutingDecision
    document_evidence: list[Evidence]
    sql_evidence: list[Evidence]
    agent_results: list[AgentResult]
    verified_claims: list[Claim]
    warnings: list[str]
    answer: str
    timings_ms: dict[str, int]
    provider_metrics: list[dict[str, Any]]


def _emit(state: OrchestratorState, stage: str, message: str) -> None:
    callback = state.get("progress")
    if callback:
        callback(stage, message)


def _router_node(state: OrchestratorState) -> dict[str, Any]:
    started = time.perf_counter()
    routing = route_query(state["query"])
    _emit(state, "routing", f"Routed to {', '.join(routing.agents)}")
    return {
        "routing": routing,
        "document_evidence": [],
        "sql_evidence": [],
        "agent_results": [],
        "verified_claims": [],
        "warnings": [],
        "provider_metrics": [],
        "timings_ms": {"routing": int((time.perf_counter() - started) * 1_000)},
    }


def _document_lookup(state: OrchestratorState) -> tuple[list[Evidence], dict[str, int], str | None]:
    timings: dict[str, int] = {}
    try:
        evidence = retrieve(
            state["query"],
            top_k=state.get("top_k") or 8,
            collection=state.get("collection"),
            timings_ms=timings,
        )
    except Exception as exc:
        return [], timings, f"Document retrieval failed: {exc}"
    warning = None if evidence else "No document evidence was retrieved."
    return evidence, timings, warning


def _sql_lookup(state: OrchestratorState) -> tuple[list[Evidence], dict[str, int], str | None]:
    started = time.perf_counter()
    try:
        generated, result = answer_with_sql(
            state["query"],
            provider_deadline=state.get("provider_deadline"),
        )
    except UnsupportedBusinessQuestion as exc:
        warning = f"Structured lookup not applicable: {exc}"
        return [], {"sql_lookup": int((time.perf_counter() - started) * 1_000)}, warning
    except Exception as exc:
        warning = f"Structured lookup unavailable: {exc}"
        return [], {"sql_lookup": int((time.perf_counter() - started) * 1_000)}, warning

    evidence = result.evidence.model_copy(deep=True)
    evidence.metadata.update(
        {
            "generation_reason": generated.reason,
            "sql_source": generated.source,
        }
    )
    return [evidence], {"sql_lookup": int((time.perf_counter() - started) * 1_000)}, None


def _resolve(future: Future | None) -> tuple[list[Evidence], dict[str, int], str | None]:
    return future.result() if future is not None else ([], {}, None)


def _evidence_node(state: OrchestratorState) -> dict[str, Any]:
    _emit(state, "evidence", "Collecting document and structured evidence")
    started = time.perf_counter()
    routing = state["routing"]
    document_future = (
        _evidence_executor().submit(_document_lookup, state)
        if routing.requires_documents
        else None
    )
    sql_future = (
        _evidence_executor().submit(_sql_lookup, state) if routing.requires_sql else None
    )

    documents, document_timings, document_warning = _resolve(document_future)
    sql, sql_timings, sql_warning = _resolve(sql_future)
    timings = dict(state.get("timings_ms", {}))
    timings.update({f"document_{key}": value for key, value in document_timings.items()})
    timings.update(sql_timings)
    timings["evidence_collection"] = int((time.perf_counter() - started) * 1_000)
    warnings = [warning for warning in (document_warning, sql_warning) if warning]
    _emit(state, "evidence", f"Collected {len(documents) + len(sql)} evidence item(s)")
    return {
        "document_evidence": documents,
        "sql_evidence": sql,
        "warnings": warnings,
        "timings_ms": timings,
    }


def _synthesis_node(state: OrchestratorState) -> dict[str, Any]:
    _emit(state, "synthesis", "Synthesizing one grounded response")
    started = time.perf_counter()
    evidence = [*state.get("document_evidence", []), *state.get("sql_evidence", [])]
    result = synthesize_answer(
        state["query"],
        state["routing"].agents,
        evidence,
        provider_deadline=state.get("provider_deadline"),
    )
    timings = dict(state.get("timings_ms", {}))
    timings["synthesis"] = int((time.perf_counter() - started) * 1_000)
    _emit(state, "verification", "Applied deterministic citation and value checks")
    return {
        "answer": result.answer,
        "verified_claims": result.claims,
        "agent_results": result.agent_results,
        "warnings": [*state.get("warnings", []), *result.warnings],
        "provider_metrics": result.provider_metrics,
        "timings_ms": timings,
    }


@lru_cache(maxsize=1)
def graph():
    """Compile the small deterministic workflow once."""
    builder = StateGraph(OrchestratorState)
    builder.add_node("route", _router_node)
    builder.add_node("collect_evidence", _evidence_node)
    builder.add_node("synthesize", _synthesis_node)
    builder.add_edge(START, "route")
    builder.add_edge("route", "collect_evidence")
    builder.add_edge("collect_evidence", "synthesize")
    builder.add_edge("synthesize", END)
    return builder.compile()


def run_orchestrator(
    query: str,
    *,
    top_k: int = 8,
    collection: str | None = None,
    progress: ProgressCallback | None = None,
) -> OrchestratorState:
    started = time.perf_counter()
    result = graph().invoke(
        {
            "query": query,
            "top_k": top_k,
            "collection": collection,
            "progress": progress,
        }
    )
    timings = dict(result.get("timings_ms", {}))
    timings["orchestrator_total"] = int((time.perf_counter() - started) * 1_000)
    result["timings_ms"] = timings
    return result


def close_executor() -> None:
    """Release evidence worker threads when FastAPI shuts down."""
    if _evidence_executor.cache_info().currsize:
        _evidence_executor().shutdown(wait=False, cancel_futures=True)
        _evidence_executor.cache_clear()


__all__ = ["AGENTS", "OrchestratorState", "route_query", "run_orchestrator"]
