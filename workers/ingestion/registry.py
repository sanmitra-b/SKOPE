"""Load the approved production corpus from PostgreSQL's document registry."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from backend.app.config import get_settings
from backend.app.db import connection

from .domain import SourceDocument


PRODUCTION_REGISTRY_SQL = """
    SELECT document_id, document_type, source_path, source_system,
           primary_entity_type, primary_entity_id, sha256, byte_size,
           dataset_as_of, metadata
    FROM skope.document_index
    WHERE rag_index_allowed
      AND (
        document_type = 'EMAIL_THREAD_JSON'
        OR (metadata->>'phase' = 'PHASE2' AND metadata->>'mode' = 'full')
      )
    ORDER BY document_type, source_path
"""


def resolve_source_path(dataset_root: Path, source_path: str) -> Path:
    normalized = source_path.replace("/", "\\")
    candidates = (
        dataset_root / normalized,
        dataset_root / "harmonized_data" / normalized,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Source file not found for {source_path}")


def load_registry(
    *,
    limit: int | None = None,
    document_types: set[str] | None = None,
    per_type: int | None = None,
) -> list[SourceDocument]:
    """Return the deterministic production subset requested by the CLI."""
    settings = get_settings()
    with connection(readonly=True) as conn:
        rows = conn.execute(PRODUCTION_REGISTRY_SQL).fetchall()

    sources: list[SourceDocument] = []
    selected_counts: Counter[str] = Counter()
    for row in rows:
        document_type = row["document_type"]
        if document_types and document_type not in document_types:
            continue
        if per_type and selected_counts[document_type] >= per_type:
            continue

        sources.append(
            SourceDocument(
                document_id=row["document_id"],
                document_type=document_type,
                source_path=row["source_path"],
                absolute_path=resolve_source_path(settings.dataset_root, row["source_path"]),
                source_system=row["source_system"],
                primary_entity_type=row["primary_entity_type"],
                primary_entity_id=row["primary_entity_id"],
                sha256=row["sha256"],
                byte_size=row["byte_size"],
                dataset_as_of=str(row["dataset_as_of"]),
                metadata=dict(row["metadata"] or {}),
            )
        )
        selected_counts[document_type] += 1
        if limit and len(sources) >= limit:
            break
    return sources
