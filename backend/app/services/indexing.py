"""Read-only corpus and index status queries."""

from __future__ import annotations

import logging

from qdrant_client import QdrantClient

from ..config import get_settings
from ..db import connection
from ..models import IndexStatus


logger = logging.getLogger(__name__)


PRODUCTION_DOCUMENT_COUNT_SQL = """
    SELECT count(*) AS count
    FROM skope.document_index
    WHERE rag_index_allowed
      AND (
        document_type = 'EMAIL_THREAD_JSON'
        OR (metadata->>'phase' = 'PHASE2' AND metadata->>'mode' = 'full')
      )
"""

LATEST_RUN_SQL = """
    SELECT run_id, started_at, completed_at, status, source_document_count,
           extracted_document_count, chunk_count, failed_document_count, summary
    FROM rag.ingestion_run
    ORDER BY started_at DESC
    LIMIT 1
"""


def _total_source_records() -> int:
    """Count records in user-facing source schemas for the dashboard."""
    with connection(readonly=True) as conn:
        tables = conn.execute(
            """
            SELECT schemaname, tablename
            FROM pg_tables
            WHERE schemaname IN ('skope', 'restricted')
            """
        ).fetchall()

        total = 0
        for table in tables:
            # Names come from PostgreSQL's catalog, not from a request. Quoting
            # keeps unusual identifiers safe and readable.
            schema = table["schemaname"].replace('"', '""')
            name = table["tablename"].replace('"', '""')
            try:
                # A nested psycopg transaction creates a savepoint. If one
                # table is inaccessible, rolling back to it keeps the outer
                # read-only transaction usable for the remaining tables.
                with conn.transaction():
                    row = conn.execute(
                        f'SELECT count(*) AS count FROM "{schema}"."{name}"'
                    ).fetchone()
            except Exception as exc:
                # Some deployments intentionally deny the application role
                # access to restricted tables. A dashboard count should not
                # make the otherwise healthy status endpoint fail.
                logger.debug("Skipping inaccessible table %s.%s: %s", schema, name, exc)
                continue
            total += row["count"]
    return total


def get_index_status() -> IndexStatus:
    settings = get_settings()
    with connection(readonly=True) as conn:
        postgres_chunks = conn.execute(
            "SELECT count(*) AS count FROM rag.document_chunk"
        ).fetchone()["count"]
        production_documents = conn.execute(
            PRODUCTION_DOCUMENT_COUNT_SQL
        ).fetchone()["count"]
        last_run = conn.execute(LATEST_RUN_SQL).fetchone()

    try:
        client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )
        qdrant_points = client.count(
            collection_name=settings.qdrant_collection,
            exact=True,
        ).count
    except Exception:
        # Index status remains useful while Qdrant is starting or unavailable.
        qdrant_points = 0

    return IndexStatus(
        collection=settings.qdrant_collection,
        corpus_version=settings.corpus_version,
        postgres_chunks=postgres_chunks,
        qdrant_points=qdrant_points,
        production_documents=production_documents,
        total_source_records=_total_source_records(),
        last_run=dict(last_run) if last_run else None,
    )
