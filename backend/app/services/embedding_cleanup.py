from typing import Protocol

from sqlalchemy.orm import Session

from app.models import Document, DocumentEmbeddingStatus


SAFE_EMBEDDING_CLEANUP_ERROR = "Document embeddings could not be cleaned up"


class DocumentRecordStore(Protocol):
    def delete_document_records(self, document_id: int) -> None: ...

    def get_document_record_count(self, document_id: int) -> int: ...


class VectorStoreFactory(Protocol):
    def __call__(self) -> DocumentRecordStore: ...


class DocumentEmbeddingCleanupError(RuntimeError):
    pass


class DocumentEmbeddingCleanupPersistenceError(RuntimeError):
    pass


def document_needs_embedding_cleanup(document: Document) -> bool:
    """Return false only when no embedding cleanup is pending or required."""
    return not (
        document.embedding_status == DocumentEmbeddingStatus.PENDING.value
        and document.embedding_generation_id is None
    )


def mark_document_embedding_stale(document: Document) -> None:
    document.embedding_status = DocumentEmbeddingStatus.STALE.value
    document.embedding_generation_id = None
    document.embedded_at = None
    document.embedding_error = None


def _save_cleanup_result(
    session: Session,
    document_id: int,
    *,
    status: DocumentEmbeddingStatus,
    error: str | None,
) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise DocumentEmbeddingCleanupPersistenceError(
            "The document disappeared while embedding cleanup was being saved"
        )

    document.embedding_status = status.value
    document.embedding_generation_id = None
    document.embedded_at = None
    document.embedding_error = error
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise DocumentEmbeddingCleanupPersistenceError(
            "The document embedding cleanup status could not be saved"
        ) from exc
    return document


def _delete_document_records(
    document_id: int,
    *,
    vector_store_factory: VectorStoreFactory,
    verify_empty: bool,
) -> None:
    try:
        vector_store = vector_store_factory()
        vector_store.delete_document_records(document_id)
        if verify_empty and (
            vector_store.get_document_record_count(document_id) != 0
        ):
            raise RuntimeError("Chroma document records remain after cleanup")
    except Exception as exc:
        raise DocumentEmbeddingCleanupError(
            SAFE_EMBEDDING_CLEANUP_ERROR
        ) from exc


def cleanup_stale_document_embedding(
    session: Session,
    document_id: int,
    *,
    vector_store_factory: VectorStoreFactory,
) -> Document:
    """Delete Chroma records after stale state is committed, then save the result."""
    try:
        _delete_document_records(
            document_id,
            vector_store_factory=vector_store_factory,
            verify_empty=False,
        )
    except DocumentEmbeddingCleanupError:
        _save_cleanup_result(
            session,
            document_id,
            status=DocumentEmbeddingStatus.STALE,
            error=SAFE_EMBEDDING_CLEANUP_ERROR,
        )
        raise

    return _save_cleanup_result(
        session,
        document_id,
        status=DocumentEmbeddingStatus.PENDING,
        error=None,
    )


def cleanup_document_embedding(
    session: Session,
    document_id: int,
    *,
    vector_store_factory: VectorStoreFactory,
) -> None:
    """Delete and verify Chroma records while leaving SQLite state stale."""
    try:
        _delete_document_records(
            document_id,
            vector_store_factory=vector_store_factory,
            verify_empty=True,
        )
    except DocumentEmbeddingCleanupError:
        _save_cleanup_result(
            session,
            document_id,
            status=DocumentEmbeddingStatus.STALE,
            error=SAFE_EMBEDDING_CLEANUP_ERROR,
        )
        raise
