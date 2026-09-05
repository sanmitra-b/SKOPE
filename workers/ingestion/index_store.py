"""Persist chunks, generate local vectors, and upsert them into Qdrant."""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING

from psycopg.types.json import Jsonb
from qdrant_client import QdrantClient, models

from backend.app.config import PROJECT_ROOT, get_settings
from backend.app.db import connection

from .domain import CHUNKER_VERSION, PARSER_VERSION, Chunk

if TYPE_CHECKING:
    from fastembed import SparseTextEmbedding, TextEmbedding


def _batches(items: Sequence[Chunk], size: int) -> Iterator[Sequence[Chunk]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def persist_chunks(chunks: Sequence[Chunk]) -> None:
    rows = [
        (
            chunk.chunk_id,
            chunk.document_id,
            chunk.chunk_index,
            chunk.page_start,
            chunk.page_end,
            chunk.section_label,
            chunk.content,
            chunk.content_sha256,
            chunk.token_estimate,
            Jsonb(chunk.metadata),
            "INTERNAL",
            PARSER_VERSION,
            CHUNKER_VERSION,
        )
        for chunk in chunks
    ]
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO rag.document_chunk(
                    chunk_id, document_id, chunk_index, page_start, page_end,
                    section_label, content, content_sha256, token_estimate,
                    metadata, access_classification, parser_version, chunker_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    metadata = EXCLUDED.metadata,
                    indexed_at = NULL,
                    parser_version = EXCLUDED.parser_version,
                    chunker_version = EXCLUDED.chunker_version
                """,
                rows,
            )


def qdrant_client() -> QdrantClient:
    settings = get_settings()
    return QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )


def ensure_collection(
    client: QdrantClient,
    collection: str,
    vector_size: int,
    *,
    rebuild: bool,
) -> None:
    exists = client.collection_exists(collection)
    if exists and rebuild:
        client.delete_collection(collection)
        exists = False
    if exists:
        return

    client.create_collection(
        collection_name=collection,
        vectors_config={
            "dense": models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            )
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(
                index=models.SparseIndexParams(on_disk=False),
                modifier=models.Modifier.IDF,
            )
        },
        optimizers_config=models.OptimizersConfigDiff(indexing_threshold=10_000),
    )
    payload_indexes = (
        "document_type",
        "document_id",
        "primary_entity_id",
        "business_domain",
        "phase",
        "mode",
    )
    for field_name in payload_indexes:
        client.create_payload_index(
            collection,
            field_name,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


def build_embedding_models() -> tuple[TextEmbedding, SparseTextEmbedding]:
    """Construct local dense and sparse models only when indexing starts."""
    from fastembed import SparseTextEmbedding, TextEmbedding

    settings = get_settings()
    cache_dir = PROJECT_ROOT / ".model-cache"
    dense = TextEmbedding(
        model_name=settings.embedding_model,
        cache_dir=str(cache_dir),
        threads=8,
        providers=[settings.embedding_execution_provider],
    )
    sparse = SparseTextEmbedding(
        model_name=settings.sparse_embedding_model,
        cache_dir=str(cache_dir),
        threads=8,
    )
    return dense, sparse


def _qdrant_point(
    chunk: Chunk,
    dense_vector,
    sparse_vector,
) -> models.PointStruct:
    payload = {
        **chunk.metadata,
        "chunk_id": str(chunk.chunk_id),
        "document_id": chunk.document_id,
        "document_type": chunk.document_type,
        "source_path": chunk.source_path,
        "page": chunk.page_start,
        "section": chunk.section_label,
        "content": chunk.content,
    }
    return models.PointStruct(
        id=str(chunk.chunk_id),
        vector={
            "dense": dense_vector.tolist(),
            "sparse": models.SparseVector(
                indices=sparse_vector.indices.tolist(),
                values=sparse_vector.values.tolist(),
            ),
        },
        payload=payload,
    )


def embed_and_upsert(
    chunks: Sequence[Chunk],
    *,
    dense_model: TextEmbedding,
    sparse_model: SparseTextEmbedding,
    client: QdrantClient,
    collection: str,
    batch_size: int,
) -> None:
    total_chunks = len(chunks)
    indexed_chunks = 0
    started = time.perf_counter()

    for batch in _batches(chunks, batch_size):
        dense_vectors = list(dense_model.embed([chunk.content for chunk in batch]))
        sparse_vectors = list(sparse_model.embed([chunk.content for chunk in batch]))
        points = [
            _qdrant_point(chunk, dense_vector, sparse_vector)
            for chunk, dense_vector, sparse_vector in zip(
                batch,
                dense_vectors,
                sparse_vectors,
                strict=True,
            )
        ]
        client.upsert(collection_name=collection, points=points, wait=True)
        with connection() as conn:
            conn.execute(
                """
                UPDATE rag.document_chunk
                SET indexed_at = CURRENT_TIMESTAMP
                WHERE chunk_id = ANY(%s)
                """,
                ([chunk.chunk_id for chunk in batch],),
            )

        indexed_chunks += len(batch)
        if indexed_chunks % 512 == 0 or indexed_chunks == total_chunks:
            elapsed = max(time.perf_counter() - started, 0.001)
            rate = indexed_chunks / elapsed
            remaining_seconds = max(total_chunks - indexed_chunks, 0) / rate
            print(
                f"Indexed {indexed_chunks:,}/{total_chunks:,} chunks "
                f"({rate:.1f} chunks/s; ~{remaining_seconds / 60:.1f} min remaining)",
                flush=True,
            )
