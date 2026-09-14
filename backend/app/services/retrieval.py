from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    KnowledgeBase,
)
from app.stores import ChromaSearchHit


class QueryEmbeddingModel(Protocol):
    def embed_query(self, query: str) -> list[float]: ...


class VectorStore(Protocol):
    def search(
        self,
        query_embedding: list[float],
        *,
        allowed_record_ids: Collection[str],
        top_k: int,
    ) -> list[ChromaSearchHit]: ...


class VectorStoreFactory(Protocol):
    def __call__(self) -> VectorStore: ...


class KnowledgeBaseNotFoundError(LookupError):
    pass


class KnowledgeBaseNotSearchableError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    chunk_id: int
    document_content_id: int
    document_id: int
    knowledge_base_id: int
    text: str
    distance: float
    original_filename: str
    content_sequence: int
    chunk_sequence: int
    source_type: str
    source_start: int
    source_end: int
    start_offset: int
    end_offset: int


def _get_allowed_records(
    session: Session,
    knowledge_base_id: int,
) -> dict[str, tuple[int, int, str]]:
    statement = (
        select(
            Chunk.id,
            Document.id,
            Document.embedding_generation_id,
        )
        .join(DocumentContent, Chunk.document_content_id == DocumentContent.id)
        .join(Document, DocumentContent.document_id == Document.id)
        .where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.embedding_status == DocumentEmbeddingStatus.EMBEDDED.value,
            Document.embedding_generation_id.is_not(None),
        )
    )
    allowed_records: dict[str, tuple[int, int, str]] = {}
    for chunk_id, document_id, generation_id in session.execute(statement):
        if generation_id is None:
            continue
        record_id = f"{generation_id}:{chunk_id}"
        allowed_records[record_id] = (document_id, chunk_id, generation_id)
    return allowed_records


def _hydrate_hits(
    session: Session,
    hits: list[ChromaSearchHit],
) -> dict[int, tuple[Chunk, DocumentContent, Document]]:
    chunk_ids = {hit.chunk_id for hit in hits}
    if not chunk_ids:
        return {}

    statement = (
        select(Chunk, DocumentContent, Document)
        .join(DocumentContent, Chunk.document_content_id == DocumentContent.id)
        .join(Document, DocumentContent.document_id == Document.id)
        .where(Chunk.id.in_(chunk_ids))
    )
    return {
        chunk.id: (chunk, content, document)
        for chunk, content, document in session.execute(statement)
    }


def search_knowledge_base(
    session: Session,
    knowledge_base_id: int,
    *,
    query: str,
    top_k: int,
    embedding_model: QueryEmbeddingModel,
    vector_store_factory: VectorStoreFactory,
) -> list[RetrievalResult]:
    if session.get(KnowledgeBase, knowledge_base_id) is None:
        raise KnowledgeBaseNotFoundError("Knowledge base not found")

    allowed_records = _get_allowed_records(session, knowledge_base_id)
    if not allowed_records:
        raise KnowledgeBaseNotSearchableError(
            "Knowledge base has no searchable embedded chunks"
        )

    query_embedding = embedding_model.embed_query(query)
    vector_store = vector_store_factory()
    hits = vector_store.search(
        query_embedding,
        allowed_record_ids=allowed_records.keys(),
        top_k=top_k,
    )
    hydrated_hits = _hydrate_hits(session, hits)

    results: list[RetrievalResult] = []
    for hit in hits:
        expected_record = allowed_records.get(hit.record_id)
        if expected_record != (hit.document_id, hit.chunk_id, hit.generation_id):
            continue

        hydrated = hydrated_hits.get(hit.chunk_id)
        if hydrated is None:
            continue
        chunk, content, document = hydrated
        if (
            document.id != hit.document_id
            or document.knowledge_base_id != knowledge_base_id
            or document.embedding_status
            != DocumentEmbeddingStatus.EMBEDDED.value
            or document.embedding_generation_id != hit.generation_id
            or hit.record_id
            != f"{document.embedding_generation_id}:{chunk.id}"
        ):
            continue

        results.append(
            RetrievalResult(
                chunk_id=chunk.id,
                document_content_id=content.id,
                document_id=document.id,
                knowledge_base_id=document.knowledge_base_id,
                text=chunk.text,
                distance=hit.distance,
                original_filename=document.original_filename,
                content_sequence=content.sequence,
                chunk_sequence=chunk.sequence,
                source_type=content.source_type,
                source_start=content.source_start,
                source_end=content.source_end,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
            )
        )

    results.sort(
        key=lambda result: (
            result.distance,
            result.document_id,
            result.content_sequence,
            result.chunk_sequence,
            result.chunk_id,
        )
    )
    return results[:top_k]
