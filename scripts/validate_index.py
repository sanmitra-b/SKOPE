"""Validate that the production SKOPE corpus was completely indexed."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings
from backend.app.db import connection
from qdrant_client import QdrantClient


EXPECTED_DOCUMENTS = 16_200


def main() -> None:
    settings = get_settings()
    with connection(readonly=True) as conn:
        run = conn.execute(
            """
            SELECT run_id, status, source_document_count, extracted_document_count,
                   chunk_count, failed_document_count, completed_at, summary
            FROM rag.ingestion_run ORDER BY started_at DESC LIMIT 1
            """
        ).fetchone()
        postgres_chunks = conn.execute("SELECT count(*) AS count FROM rag.document_chunk").fetchone()["count"]
        documents_with_chunks = conn.execute(
            """
            SELECT count(DISTINCT c.document_id) AS count
            FROM rag.document_chunk c
            JOIN skope.document_index d ON d.document_id=c.document_id
            WHERE d.rag_index_allowed
              AND (d.document_type='EMAIL_THREAD_JSON'
                   OR (d.metadata->>'phase'='PHASE2' AND d.metadata->>'mode'='full'))
            """
        ).fetchone()["count"]
        unindexed = conn.execute(
            "SELECT count(*) AS count FROM rag.document_chunk WHERE indexed_at IS NULL"
        ).fetchone()["count"]
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    qdrant_points = client.count(settings.qdrant_collection, exact=True).count
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "collection": settings.qdrant_collection,
        "expected_documents": EXPECTED_DOCUMENTS,
        "run": dict(run) if run else None,
        "documents_with_chunks": documents_with_chunks,
        "postgres_chunks": postgres_chunks,
        "qdrant_points": qdrant_points,
        "postgres_unindexed_chunks": unindexed,
    }
    report["valid"] = bool(
        run
        and run["status"] == "SUCCEEDED"
        and run["source_document_count"] == EXPECTED_DOCUMENTS
        and run["extracted_document_count"] == EXPECTED_DOCUMENTS
        and run["failed_document_count"] == 0
        and documents_with_chunks == EXPECTED_DOCUMENTS
        and postgres_chunks == qdrant_points == run["chunk_count"]
        and unindexed == 0
    )
    output_dir = ROOT / "artifacts" / "ingestion"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "latest_index_validation.json"
    output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"valid": report["valid"], "report": str(output), **report}, indent=2, default=str))
    if not report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
