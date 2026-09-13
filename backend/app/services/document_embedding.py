from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.embeddings import DashScopeEmbeddingError
from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    DocumentStatus,
)
from app.stores import ChromaGenerationSummary, ChromaRecord, ChromaVectorStoreError


logger = logging.getLogger(__name__)


class EmbeddingModel(Protocol):
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...


class VectorStore(Protocol):
    def add_records(self, records: list[ChromaRecord]) -> None: ...

    def get_generation_summary(
        self,
        generation_id: str,
    ) -> ChromaGenerationSummary: ...

    def delete_generation(self, generation_id: str) -> None: ...


class VectorStoreFactory(Protocol):
    def __call__(self) -> VectorStore: ...


class DocumentNotReadyForEmbeddingError(ValueError):
    pass


class DocumentEmbeddingPersistenceError(RuntimeError):
    pass


class DocumentEmbeddingResultError(RuntimeError):
    pass


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_document_embedding_status(
    session: Session,
    document_id: int,
) -> Document | None:
    return session.get(Document, document_id)


def _get_chunks(session: Session, document_id: int) -> list[Chunk]:
    statement = (
        select(Chunk)
        .join(DocumentContent)
        .options(joinedload(Chunk.document_content))
        .where(DocumentContent.document_id == document_id)
        .order_by(DocumentContent.sequence.asc(), Chunk.sequence.asc())
    )
    return list(session.scalars(statement).all())


def _mark_as_embedding(session: Session, document: Document) -> None:
    document.embedding_status = DocumentEmbeddingStatus.EMBEDDING.value
    document.embedding_error = None
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise DocumentEmbeddingPersistenceError(
            "The document embedding status could not be saved"
        ) from exc


def _mark_as_failed(
    session: Session,
    document_id: int,
    error: str,
) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise DocumentEmbeddingPersistenceError(
            "The document disappeared while its embedding status was being updated"
        )
    document.embedding_status = DocumentEmbeddingStatus.EMBEDDING_FAILED.value
    document.embedding_error = error
    try:
        session.commit()
        return document
    except Exception as exc:
        session.rollback()
        raise DocumentEmbeddingPersistenceError(
            "The document embedding failure status could not be saved"
        ) from exc


def _safe_embedding_error(exc: Exception) -> str:
    if isinstance(exc, DashScopeEmbeddingError):
        return str(exc)
    if isinstance(exc, ChromaVectorStoreError):
        return "Document embeddings could not be saved"
    if isinstance(exc, DocumentEmbeddingResultError):
        return str(exc)
    return f"Document embedding failed ({type(exc).__name__})"


def _discard_generation(vector_store: VectorStore, generation_id: str) -> None:
    try:
        vector_store.delete_generation(generation_id)
    except Exception:
        logger.warning(
            "Could not clean up inactive embedding generation %s",
            generation_id,
        )


def _build_records(
    document: Document,
    chunks: list[Chunk],
    vectors: list[list[float]],
    generation_id: str,
) -> list[ChromaRecord]:
    if len(vectors) != len(chunks):
        raise DocumentEmbeddingResultError(
            "Embedding provider returned an unexpected number of vectors"
        )

    return [
        ChromaRecord(
            id=f"{generation_id}:{chunk.id}",
            text=chunk.text,
            embedding=vector,
            metadata={
                "generation_id": generation_id,
                "document_id": document.id,
                "chunk_id": chunk.id,
                "document_content_id": chunk.document_content_id,
                "content_sequence": chunk.document_content.sequence,
                "chunk_sequence": chunk.sequence,
                "source_type": chunk.document_content.source_type,
                "source_start": chunk.document_content.source_start,
                "source_end": chunk.document_content.source_end,
                "start_offset": chunk.start_offset,
                "end_offset": chunk.end_offset,
                "original_filename": document.original_filename,
            },
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]


def _validate_candidate_generation(
    chunks: list[Chunk],
    summary: ChromaGenerationSummary,
) -> None:
    expected_chunk_ids = frozenset(chunk.id for chunk in chunks)
    if (
        summary.record_count != len(chunks)
        or summary.chunk_ids != expected_chunk_ids
    ):
        raise DocumentEmbeddingResultError(
            "Chroma candidate embedding generation is incomplete"
        )


def embed_document(
    session: Session,
    document_id: int,
    *,
    embedding_model: EmbeddingModel,
    vector_store_factory: VectorStoreFactory,
) -> Document | None:
    document = session.get(Document, document_id)
    if document is None:
        return None
    if document.status != DocumentStatus.PARSED.value:
        raise DocumentNotReadyForEmbeddingError(
            "Document must be parsed before embeddings can be built"
        )

    chunks = _get_chunks(session, document_id)
    if not chunks:
        raise DocumentNotReadyForEmbeddingError(
            "Document must have chunks before embeddings can be built"
        )

    previous_generation_id = document.embedding_generation_id
    generation_id = uuid4().hex
    _mark_as_embedding(session, document)

    vector_store: VectorStore | None = None
    try:
        vector_store = vector_store_factory()
        vectors = embedding_model.embed_texts([chunk.text for chunk in chunks])
        records = _build_records(document, chunks, vectors, generation_id)
        vector_store.add_records(records)
        candidate_summary = vector_store.get_generation_summary(generation_id)
        _validate_candidate_generation(chunks, candidate_summary)
    except Exception as exc:
        session.rollback()
        if vector_store is not None:
            _discard_generation(vector_store, generation_id)
        return _mark_as_failed(session, document_id, _safe_embedding_error(exc))

    try:
        document.embedding_status = DocumentEmbeddingStatus.EMBEDDED.value
        document.embedding_error = None
        document.embedded_at = _naive_utc_now()
        document.embedding_generation_id = generation_id
        session.commit()
    except Exception:
        session.rollback()
        _discard_generation(vector_store, generation_id)
        return _mark_as_failed(
            session,
            document_id,
            "Document embedding completion status could not be saved",
        )

    if previous_generation_id is not None:
        _discard_generation(vector_store, previous_generation_id)
    return document
