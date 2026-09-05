"""CLI entrypoint for extracting, chunking, embedding, and indexing SKOPE."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from psycopg.types.json import Jsonb  # noqa: E402

from backend.app.config import get_settings  # noqa: E402
from backend.app.db import connection  # noqa: E402
from workers.ingestion.domain import (  # noqa: E402
    CHUNKER_VERSION,
    DEFAULT_CHUNK_CHARS,
    DEFAULT_OVERLAP_CHARS,
    PARSER_VERSION,
    Chunk,
    SourceDocument,
)
from workers.ingestion.extraction import extract_source, verify_source  # noqa: E402
from workers.ingestion.index_store import (  # noqa: E402
    build_embedding_models,
    embed_and_upsert,
    ensure_collection,
    persist_chunks,
    qdrant_client,
)
from workers.ingestion.registry import load_registry  # noqa: E402


FULL_CORPUS_DOCUMENTS = 16_200


def create_run(
    run_id: UUID,
    source_count: int,
    collection: str,
    configuration: dict[str, Any],
) -> None:
    settings = get_settings()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO rag.ingestion_run(
                run_id, status, corpus_version, embedding_model,
                collection_name, source_document_count, configuration
            ) VALUES (%s, 'RUNNING', %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                settings.corpus_version,
                settings.embedding_model,
                collection,
                source_count,
                Jsonb(configuration),
            ),
        )


def finish_run(
    run_id: UUID,
    *,
    status: str,
    extracted_documents: int,
    chunk_count: int,
    failures: list[dict[str, str]],
    summary: dict[str, Any],
) -> None:
    with connection() as conn:
        conn.execute(
            """
            UPDATE rag.ingestion_run
            SET completed_at = CURRENT_TIMESTAMP,
                status = %s,
                extracted_document_count = %s,
                chunk_count = %s,
                failed_document_count = %s,
                summary = %s
            WHERE run_id = %s
            """,
            (
                status,
                extracted_documents,
                chunk_count,
                len(failures),
                Jsonb({**summary, "failures": failures}),
                run_id,
            ),
        )


def _configuration(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "limit": args.limit,
        "per_type": args.per_type,
        "document_types": sorted(args.document_type or []),
        "full": args.full,
        "rebuild": args.rebuild,
        "max_chars": args.max_chars,
        "overlap": args.overlap,
        "parser_version": PARSER_VERSION,
        "chunker_version": CHUNKER_VERSION,
    }


def _extract_sources(
    sources: Sequence[SourceDocument],
    *,
    max_chars: int,
    overlap: int,
) -> tuple[list[Chunk], int, list[dict[str, str]]]:
    chunks: list[Chunk] = []
    failures: list[dict[str, str]] = []
    extracted_documents = 0

    for number, source in enumerate(sources, start=1):
        try:
            verify_source(source)
            chunks.extend(extract_source(source, max_chars, overlap))
            extracted_documents += 1
        except Exception as exc:
            failures.append({"source_path": source.source_path, "error": str(exc)})

        if number % 250 == 0 or number == len(sources):
            print(
                f"Extracted {number:,}/{len(sources):,} documents; "
                f"{len(chunks):,} chunks; {len(failures)} failures",
                flush=True,
            )

    return chunks, extracted_documents, failures


def _too_many_extraction_failures(
    failures: Sequence[dict[str, str]],
    source_count: int,
    *,
    full_run: bool,
) -> bool:
    return bool(failures) and (full_run or len(failures) / source_count > 0.01)


def _index_chunks(
    chunks: Sequence[Chunk],
    *,
    collection: str,
    batch_size: int,
    rebuild: bool,
) -> int:
    persist_chunks(chunks)
    dense_model, sparse_model = build_embedding_models()
    probe = next(iter(dense_model.embed(["SKOPE embedding dimension probe"])))
    client = qdrant_client()
    ensure_collection(client, collection, len(probe), rebuild=rebuild)
    embed_and_upsert(
        chunks,
        dense_model=dense_model,
        sparse_model=sparse_model,
        client=client,
        collection=collection,
        batch_size=batch_size,
    )
    return client.count(collection_name=collection, exact=True).count


def _write_report(report: dict[str, Any], run_id: UUID) -> Path:
    report_dir = PROJECT_ROOT / "artifacts" / "ingestion"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"ingestion_{run_id}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report_path


def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    selected_types = set(args.document_type) if args.document_type else None
    sources = load_registry(
        limit=args.limit,
        document_types=selected_types,
        per_type=args.per_type,
    )
    if not sources:
        raise SystemExit("No approved production documents matched the requested scope.")
    if args.full and len(sources) != FULL_CORPUS_DOCUMENTS:
        raise SystemExit(
            f"Full ingestion requires {FULL_CORPUS_DOCUMENTS:,} documents; "
            f"registry returned {len(sources):,}."
        )

    run_id = uuid4()
    collection = args.collection or settings.qdrant_collection
    create_run(run_id, len(sources), collection, _configuration(args))
    if args.rebuild:
        with connection() as conn:
            conn.execute("TRUNCATE rag.document_chunk")

    started = time.perf_counter()
    chunks, extracted_documents, failures = _extract_sources(
        sources,
        max_chars=args.max_chars,
        overlap=args.overlap,
    )
    if _too_many_extraction_failures(
        failures,
        len(sources),
        full_run=args.full,
    ):
        summary = {"elapsed_seconds": round(time.perf_counter() - started, 2)}
        finish_run(
            run_id,
            status="FAILED",
            extracted_documents=extracted_documents,
            chunk_count=len(chunks),
            failures=failures,
            summary=summary,
        )
        raise SystemExit(
            f"Extraction failed for {len(failures)} documents; no index was promoted."
        )

    try:
        qdrant_points = _index_chunks(
            chunks,
            collection=collection,
            batch_size=args.batch_size,
            rebuild=args.rebuild,
        )
    except Exception as exc:
        indexing_failures = [
            *failures,
            {"source_path": "INDEXING", "error": str(exc)},
        ]
        finish_run(
            run_id,
            status="FAILED",
            extracted_documents=extracted_documents,
            chunk_count=len(chunks),
            failures=indexing_failures,
            summary={
                "elapsed_seconds": round(time.perf_counter() - started, 2),
                "collection": collection,
                "indexing_error": str(exc),
            },
        )
        raise

    elapsed = round(time.perf_counter() - started, 2)
    status = "SUCCEEDED" if not failures else "PARTIAL"
    summary = {
        "elapsed_seconds": elapsed,
        "qdrant_points": qdrant_points,
        "type_counts": dict(
            sorted(Counter(chunk.document_type for chunk in chunks).items())
        ),
        "collection": collection,
    }
    finish_run(
        run_id,
        status=status,
        extracted_documents=extracted_documents,
        chunk_count=len(chunks),
        failures=failures,
        summary=summary,
    )

    report = {
        "status": status,
        "run_id": str(run_id),
        "source_documents": len(sources),
        "extracted_documents": extracted_documents,
        "chunks": len(chunks),
        "qdrant_points": qdrant_points,
        "failures": failures,
        **summary,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    report_path = _write_report(report, run_id)
    print(json.dumps({**report, "report": str(report_path)}, indent=2))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the approved SKOPE document index.",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--per-type", type=int)
    parser.add_argument("--document-type", action="append")
    parser.add_argument("--collection")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP_CHARS)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--full", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
