"""Authorized document metadata and source-file endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..auth import current_user
from ..config import get_settings
from ..db import connection
from ..models import UserContext


router = APIRouter(prefix="/api/documents")


def _resolve_source_file(dataset_root: Path, source_path: str) -> Path | None:
    """Resolve Phase 1 harmonized files and Phase 2 files within the dataset boundary."""
    resolved_root = dataset_root.resolve()
    candidate_roots = (resolved_root, resolved_root / "harmonized_data")
    for candidate_root in candidate_roots:
        target = (candidate_root / source_path).resolve()
        if resolved_root not in target.parents:
            continue
        if target.is_file():
            return target
    return None


def _document_row(document_id: str, columns: str):
    with connection(readonly=True) as conn:
        return conn.execute(
            f"""
            SELECT {columns}
            FROM skope.document_index
            WHERE document_id = %s AND rag_index_allowed
            """,
            (document_id,),
        ).fetchone()


@router.get("/{document_id}")
def document_metadata(
    document_id: str,
    _: UserContext = Depends(current_user),
) -> dict:
    row = _document_row(
        document_id,
        """document_id, document_type, source_path, source_system,
           primary_entity_type, primary_entity_id, byte_size, metadata, dataset_as_of""",
    )
    if not row:
        raise HTTPException(status_code=404, detail="Document not found.")
    return dict(row)


@router.get("/{document_id}/file")
def document_file(
    document_id: str,
    _: UserContext = Depends(current_user),
) -> FileResponse:
    row = _document_row(document_id, "source_path")
    if not row:
        raise HTTPException(status_code=404, detail="Document not found.")

    target = _resolve_source_file(get_settings().dataset_root, row["source_path"])
    if target is None:
        raise HTTPException(status_code=404, detail="Source file unavailable.")

    media_type = "application/pdf" if target.suffix.lower() == ".pdf" else "application/json"
    return FileResponse(target, media_type=media_type, filename=target.name)
