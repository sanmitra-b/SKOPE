"""Hybrid document retrieval with exact-identifier resolution and reranking."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from qdrant_client import QdrantClient, models

from .config import PROJECT_ROOT, get_settings
from .db import connection
from .models import Evidence

if TYPE_CHECKING:
    from fastembed import SparseTextEmbedding, TextEmbedding
    from fastembed.rerank.cross_encoder import TextCrossEncoder


# Deliberately require an alpha prefix or a standard container format. Searching
# every number as an identifier creates large numbers of false exact matches.
IDENTIFIER_PATTERNS = (
    re.compile(r"\b[A-Z]{4}\d{7}\b", re.IGNORECASE),
    re.compile(r"\b[A-Z][A-Z0-9]{1,15}(?:[-_/][A-Z0-9.]{1,24})+\b", re.IGNORECASE),
)
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
_ACTIVE_MODEL_PROVIDERS: dict[str, list[str]] = {}


@lru_cache(maxsize=1)
def _prepare_onnx_runtime() -> str:
    """Load packaged GPU DLLs and verify the requested ONNX provider exists."""
    settings = get_settings()
    requested = settings.embedding_execution_provider

    import onnxruntime as ort

    if requested == "CUDAExecutionProvider":
        preload = getattr(ort, "preload_dlls", None)
        if preload is None:
            raise RuntimeError(
                "CUDA requires ONNX Runtime 1.21 or newer so packaged CUDA/cuDNN DLLs can be preloaded."
            )
        preload(directory="")

    available = ort.get_available_providers()
    if requested not in available:
        raise RuntimeError(
            f"Requested ONNX provider {requested!r} is unavailable; installed providers: {available}."
        )
    return requested


def _verify_model_provider(model: object, requested: str, *, label: str) -> None:
    """Reject silent provider fallback after FastEmbed creates its ONNX session."""
    fastembed_model = getattr(model, "model", None)
    session = getattr(fastembed_model, "model", None)
    get_providers = getattr(session, "get_providers", None)
    active = list(get_providers()) if callable(get_providers) else []
    if requested not in active:
        raise RuntimeError(
            f"{label} requested {requested!r} but its ONNX session uses {active or ['unknown']}."
        )
    _ACTIVE_MODEL_PROVIDERS[label] = active


def onnx_runtime_status() -> dict[str, Any]:
    """Return requested, available, and verified model-session providers."""
    import onnxruntime as ort

    settings = get_settings()
    return {
        "requested_provider": settings.embedding_execution_provider,
        "available_providers": ort.get_available_providers(),
        "active_model_providers": dict(_ACTIVE_MODEL_PROVIDERS),
        "models_verified": bool(_ACTIVE_MODEL_PROVIDERS),
        "onnxruntime_version": ort.__version__,
    }


def prewarm_models() -> dict[str, Any]:
    """Create and exercise local model sessions before the API accepts traffic."""
    dense = dense_model()
    list(dense.embed([QUERY_PREFIX + "SKOPE readiness check"]))
    if get_settings().reranker_enabled:
        cross_encoder = reranker_model()
        list(cross_encoder.rerank("readiness", ["readiness"], batch_size=1))
    return onnx_runtime_status()


def extract_identifiers(query: str) -> list[str]:
    identifiers: list[str] = []
    seen: set[str] = set()
    for pattern in IDENTIFIER_PATTERNS:
        for match in pattern.finditer(query):
            value = match.group(0).upper()
            if value not in seen:
                identifiers.append(value)
                seen.add(value)
    return identifiers[:10]


@lru_cache(maxsize=1)
def dense_model() -> TextEmbedding:
    # FastEmbed imports ONNX Runtime. Loading it lazily keeps lightweight API
    # commands (health checks, schema work, and route tests) independent from
    # the host's CPU/GPU inference runtime.
    from fastembed import TextEmbedding

    settings = get_settings()
    requested = _prepare_onnx_runtime()
    model = TextEmbedding(
        model_name=settings.embedding_model,
        cache_dir=str(PROJECT_ROOT / ".model-cache"),
        threads=8,
        providers=[requested],
    )
    _verify_model_provider(model, requested, label="Dense embedding model")
    return model


@lru_cache(maxsize=1)
def sparse_model() -> SparseTextEmbedding:
    from fastembed import SparseTextEmbedding

    settings = get_settings()
    return SparseTextEmbedding(
        model_name=settings.sparse_embedding_model,
        cache_dir=str(PROJECT_ROOT / ".model-cache"),
        threads=8,
    )


@lru_cache(maxsize=1)
def reranker_model() -> TextCrossEncoder:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    settings = get_settings()
    requested = _prepare_onnx_runtime()
    model = TextCrossEncoder(
        model_name=settings.reranker_model,
        cache_dir=str(PROJECT_ROOT / ".model-cache"),
        threads=8,
        providers=[requested],
    )
    _verify_model_provider(model, requested, label="Reranker model")
    return model


@lru_cache(maxsize=1)
def qdrant_client() -> QdrantClient:
    settings = get_settings()
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)


def _evidence_from_payload(
    payload: dict,
    *,
    score: float,
    retrieval_method: str,
) -> Evidence:
    chunk_id = str(payload.get("chunk_id", "unknown"))
    document_type = str(payload.get("document_type", "DOCUMENT"))
    entity = payload.get("primary_entity_id")
    title = f"{document_type}: {entity}" if entity else document_type
    return Evidence(
        evidence_id=f"DOC-{chunk_id}",
        evidence_type="document",
        title=title,
        excerpt=str(payload.get("content", "")),
        document_id=str(payload.get("document_id")) if payload.get("document_id") else None,
        source_path=str(payload.get("source_path")) if payload.get("source_path") else None,
        page=int(payload["page"]) if payload.get("page") is not None else None,
        section=str(payload.get("section")) if payload.get("section") else None,
        data_as_of=str(payload.get("dataset_as_of")) if payload.get("dataset_as_of") else None,
        score=round(float(score), 6),
        metadata={
            key: value
            for key, value in payload.items()
            if key not in {"content", "chunk_id", "document_id", "source_path", "page", "section"}
        }
        | {"retrieval_method": retrieval_method},
    )


def exact_identifier_evidence(query: str, *, limit: int = 12) -> list[Evidence]:
    identifiers = extract_identifiers(query)
    if not identifiers:
        return []

    clauses: list[str] = []
    parameters: list[str | int] = []
    for identifier in identifiers:
        clauses.append("(upper(c.content) LIKE %s OR upper(coalesce(c.metadata->>'primary_entity_id', '')) = %s)")
        parameters.extend((f"%{identifier}%", identifier))
    parameters.append(limit)

    with connection(readonly=True) as conn:
        rows = conn.execute(
            f"""
            SELECT c.chunk_id, c.document_id, c.page_start, c.section_label, c.content,
                   c.metadata, d.document_type, d.source_path, d.primary_entity_id,
                   d.dataset_as_of
            FROM rag.document_chunk c
            JOIN skope.document_index d ON d.document_id = c.document_id
            WHERE {' OR '.join(clauses)}
            ORDER BY
                CASE WHEN upper(coalesce(c.metadata->>'primary_entity_id', '')) = ANY(%s)
                     THEN 0 ELSE 1 END,
                c.document_id, c.chunk_index
            LIMIT %s
            """,
            (*parameters[:-1], identifiers, parameters[-1]),
        ).fetchall()

    evidence: list[Evidence] = []
    for rank, row in enumerate(rows):
        payload = dict(row["metadata"] or {}) | {
            "chunk_id": str(row["chunk_id"]),
            "document_id": row["document_id"],
            "document_type": row["document_type"],
            "source_path": row["source_path"],
            "primary_entity_id": row["primary_entity_id"],
            "dataset_as_of": row["dataset_as_of"],
            "page": row["page_start"],
            "section": row["section_label"],
            "content": row["content"],
        }
        evidence.append(
            _evidence_from_payload(payload, score=2.0 - rank * 0.01, retrieval_method="exact_identifier")
        )
    return evidence


def exact_identifiers_satisfied(query: str, evidence: Sequence[Evidence]) -> bool:
    """Return true when every explicit identifier has an exact evidence hit."""
    identifiers = extract_identifiers(query)
    if not identifiers or not evidence:
        return False
    searchable = [
        " ".join(
            (
                item.title,
                item.excerpt or "",
                str(item.metadata.get("primary_entity_id", "")),
            )
        ).upper()
        for item in evidence
    ]
    return all(any(identifier in text for text in searchable) for identifier in identifiers)


def _qdrant_filter(document_types: Sequence[str] | None) -> models.Filter | None:
    if not document_types:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(
                key="document_type",
                match=models.MatchAny(any=[value.upper() for value in document_types]),
            )
        ]
    )


def hybrid_candidates(
    query: str,
    *,
    collection: str,
    candidate_limit: int = 30,
    document_types: Sequence[str] | None = None,
) -> list[Evidence]:
    dense = next(iter(dense_model().embed([QUERY_PREFIX + query])))
    sparse = next(iter(sparse_model().query_embed(query)))
    query_filter = _qdrant_filter(document_types)
    response = qdrant_client().query_points(
        collection_name=collection,
        prefetch=[
            models.Prefetch(
                query=dense.tolist(),
                using="dense",
                filter=query_filter,
                limit=candidate_limit,
            ),
            models.Prefetch(
                query=models.SparseVector(
                    indices=sparse.indices.tolist(),
                    values=sparse.values.tolist(),
                ),
                using="sparse",
                filter=query_filter,
                limit=candidate_limit,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=candidate_limit,
        with_payload=True,
    )
    return [
        _evidence_from_payload(dict(point.payload or {}), score=point.score, retrieval_method="hybrid_rrf")
        for point in response.points
    ]


def rerank(query: str, candidates: Sequence[Evidence]) -> list[Evidence]:
    if not candidates:
        return []
    scores = list(reranker_model().rerank(query, [item.excerpt or "" for item in candidates], batch_size=16))
    ranked: list[Evidence] = []
    for item, score in zip(candidates, scores, strict=True):
        updated = item.model_copy(deep=True)
        updated.score = round(float(score), 6)
        updated.metadata["retrieval_method"] = "hybrid_rrf_cross_encoder"
        ranked.append(updated)
    return sorted(ranked, key=lambda item: item.score or float("-inf"), reverse=True)


def _deduplicate(
    exact: Iterable[Evidence],
    semantic: Iterable[Evidence],
    *,
    limit: int,
    max_per_document: int = 2,
) -> list[Evidence]:
    output: list[Evidence] = []
    seen_evidence: set[str] = set()
    per_document: defaultdict[str, int] = defaultdict(int)
    exact_ids = {item.evidence_id for item in exact}
    for item in [*exact, *semantic]:
        if item.evidence_id in seen_evidence:
            continue
        document_key = item.document_id or item.evidence_id
        if item.evidence_id not in exact_ids and per_document[document_key] >= max_per_document:
            continue
        seen_evidence.add(item.evidence_id)
        per_document[document_key] += 1
        output.append(item)
        if len(output) >= limit:
            break
    return output


def retrieve(
    query: str,
    *,
    top_k: int | None = None,
    collection: str | None = None,
    document_types: Sequence[str] | None = None,
    use_reranker: bool | None = None,
    timings_ms: dict[str, int] | None = None,
) -> list[Evidence]:
    settings = get_settings()
    result_limit = top_k or settings.max_retrieval_results
    target_collection = collection or settings.qdrant_collection

    total_started = time.perf_counter()
    started = time.perf_counter()
    exact = exact_identifier_evidence(query, limit=min(12, result_limit))
    if timings_ms is not None:
        timings_ms["exact_identifier"] = int((time.perf_counter() - started) * 1_000)

    if exact_identifiers_satisfied(query, exact) and not document_types:
        if timings_ms is not None:
            timings_ms["retrieval_total"] = int((time.perf_counter() - total_started) * 1_000)
        return _deduplicate(exact, [], limit=result_limit)

    candidate_limit = max(
        result_limit,
        min(settings.reranker_candidate_limit, max(result_limit, result_limit * 2)),
    )
    started = time.perf_counter()
    candidates = hybrid_candidates(
        query,
        collection=target_collection,
        candidate_limit=candidate_limit,
        document_types=document_types,
    )
    if timings_ms is not None:
        timings_ms["hybrid_retrieval"] = int((time.perf_counter() - started) * 1_000)

    should_rerank = settings.reranker_enabled if use_reranker is None else use_reranker
    if should_rerank and len(candidates) > result_limit:
        started = time.perf_counter()
        ranked = rerank(query, candidates)
        if timings_ms is not None:
            timings_ms["reranking"] = int((time.perf_counter() - started) * 1_000)
    else:
        ranked = candidates
        if timings_ms is not None:
            timings_ms["reranking"] = 0

    if timings_ms is not None:
        timings_ms["retrieval_total"] = int((time.perf_counter() - total_started) * 1_000)
    return _deduplicate(exact, ranked, limit=result_limit)
