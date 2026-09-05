"""Checksum validation and layout-aware PDF/email extraction."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pypdf import PdfReader

from .domain import Chunk, SourceDocument


def normalize_text(value: str) -> str:
    value = value.replace("\x00", " ").replace("\u00a0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def split_text(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split normalized text near semantic boundaries with bounded overlap."""
    text = normalize_text(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = max(
                text.rfind("\n\n", start + max_chars // 2, end),
                text.rfind(". ", start + max_chars // 2, end),
                text.rfind("\n", start + max_chars // 2, end),
            )
            if boundary > start:
                end = boundary + (2 if text[boundary : boundary + 2] == ". " else 0)
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return parts


def _section_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        candidate = line.strip(" :-|\t")
        if 3 <= len(candidate) <= 100 and any(character.isalpha() for character in candidate):
            return candidate
    return fallback


def _make_chunk(
    source: SourceDocument,
    index: int,
    content: str,
    *,
    page: int | None,
    section: str | None,
    extra_metadata: dict[str, Any] | None = None,
) -> Chunk:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    metadata = {
        **source.metadata,
        **(extra_metadata or {}),
        "document_type": source.document_type,
        "source_system": source.source_system,
        "primary_entity_type": source.primary_entity_type,
        "primary_entity_id": source.primary_entity_id,
        "dataset_as_of": source.dataset_as_of,
    }
    chunk_id = uuid5(NAMESPACE_URL, f"skope:{source.document_id}:{index}:{digest}")
    return Chunk(
        chunk_id=chunk_id,
        document_id=source.document_id,
        document_type=source.document_type,
        chunk_index=index,
        page_start=page,
        page_end=page,
        section_label=section,
        content=content,
        content_sha256=digest,
        token_estimate=max(1, (len(content) + 3) // 4),
        source_path=source.source_path,
        metadata={key: value for key, value in metadata.items() if value is not None},
    )


def extract_pdf(
    source: SourceDocument,
    max_chars: int,
    overlap: int,
) -> list[Chunk]:
    reader = PdfReader(str(source.absolute_path))
    chunks: list[Chunk] = []
    chunk_index = 0
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = normalize_text(page.extract_text() or "")
        for part_number, part in enumerate(
            split_text(page_text, max_chars, overlap),
            start=1,
        ):
            chunks.append(
                _make_chunk(
                    source,
                    chunk_index,
                    part,
                    page=page_number,
                    section=_section_from_text(part, f"Page {page_number}"),
                    extra_metadata={"page_part": part_number},
                )
            )
            chunk_index += 1
    if not chunks:
        raise ValueError("PDF contains no extractable text")
    return chunks


def extract_email(
    source: SourceDocument,
    max_chars: int,
    overlap: int,
) -> list[Chunk]:
    payload = json.loads(source.absolute_path.read_text(encoding="utf-8"))
    lines = [
        f"Email thread: {payload.get('ticket_id', source.primary_entity_id)}",
        f"Business domain: {payload.get('business_domain', 'UNKNOWN')}",
        f"Event type: {payload.get('event_type', 'UNKNOWN')}",
        f"Primary entity: {json.dumps(payload.get('primary_entity', {}), sort_keys=True)}",
        f"Related entities: {json.dumps(payload.get('related_entities', {}), sort_keys=True)}",
        f"Recorded facts: {json.dumps(payload.get('facts', {}), sort_keys=True)}",
    ]
    messages = payload.get("emails", [])
    for sequence, email in enumerate(messages, start=1):
        lines.extend(
            [
                "",
                f"Message {sequence} — {email.get('sent_at', '')}",
                f"From: {email.get('from', '')}",
                f"To: {email.get('to', '')}",
                f"Subject: {email.get('subject', '')}",
                normalize_text(email.get("body", "")),
            ]
        )

    thread_id = payload.get("ticket_id", "")
    chunks = [
        _make_chunk(
            source,
            index,
            part,
            page=None,
            section=f"Email thread {thread_id}",
            extra_metadata={
                "business_domain": payload.get("business_domain"),
                "event_type": payload.get("event_type"),
                "message_count": len(messages),
            },
        )
        for index, part in enumerate(
            split_text(normalize_text("\n".join(lines)), max_chars, overlap)
        )
    ]
    if not chunks:
        raise ValueError("Email JSON contains no indexable content")
    return chunks


def extract_source(
    source: SourceDocument,
    max_chars: int,
    overlap: int,
) -> list[Chunk]:
    suffix = source.absolute_path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(source, max_chars, overlap)
    if suffix == ".json":
        return extract_email(source, max_chars, overlap)
    raise ValueError(f"Unsupported source suffix {suffix}")


def verify_source(source: SourceDocument) -> None:
    """Verify that a source still matches its authoritative registry entry."""
    if source.absolute_path.stat().st_size != source.byte_size:
        raise ValueError(f"Byte-size mismatch for {source.source_path}")
    digest = hashlib.sha256(source.absolute_path.read_bytes()).hexdigest()
    if digest != source.sha256:
        raise ValueError(f"Checksum mismatch for {source.source_path}")
