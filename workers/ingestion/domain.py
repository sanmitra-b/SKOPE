"""Immutable source and chunk records used by the ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID


PARSER_VERSION = "1.0.0"
CHUNKER_VERSION = "1.0.0-layout-aware"
DEFAULT_CHUNK_CHARS = 1_800
DEFAULT_OVERLAP_CHARS = 220


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    document_type: str
    source_path: str
    absolute_path: Path
    source_system: str | None
    primary_entity_type: str | None
    primary_entity_id: str | None
    sha256: str
    byte_size: int
    dataset_as_of: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Chunk:
    chunk_id: UUID
    document_id: str
    document_type: str
    chunk_index: int
    page_start: int | None
    page_end: int | None
    section_label: str | None
    content: str
    content_sha256: str
    token_estimate: int
    source_path: str
    metadata: dict[str, Any]
