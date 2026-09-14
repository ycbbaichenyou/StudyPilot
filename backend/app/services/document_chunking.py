from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.document_processing import chunking
from app.models import Chunk, Document, DocumentContent, DocumentStatus
from app.services import embedding_cleanup
from app.stores import get_vector_store


class DocumentNotReadyForChunkingError(ValueError):
    pass


class DocumentChunkingPersistenceError(RuntimeError):
    pass


def get_document_chunks(
    session: Session,
    document_id: int,
) -> tuple[Document, list[Chunk]] | None:
    document = session.get(Document, document_id)
    if document is None:
        return None

    statement = (
        select(Chunk)
        .join(DocumentContent)
        .options(joinedload(Chunk.document_content))
        .where(DocumentContent.document_id == document_id)
        .order_by(DocumentContent.sequence.asc(), Chunk.sequence.asc())
    )
    chunks = list(session.scalars(statement).all())
    return document, chunks


def rebuild_document_chunks(
    session: Session,
    document_id: int,
    *,
    chunk_size: int = chunking.DEFAULT_CHUNK_SIZE,
    overlap: int = chunking.DEFAULT_CHUNK_OVERLAP,
    vector_store_factory: embedding_cleanup.VectorStoreFactory = get_vector_store,
) -> tuple[Document, list[Chunk]] | None:
    document = session.get(Document, document_id)
    if document is None:
        return None
    if document.status != DocumentStatus.PARSED.value:
        raise DocumentNotReadyForChunkingError(
            "Document must be parsed before chunks can be built"
        )

    contents_statement = (
        select(DocumentContent)
        .where(DocumentContent.document_id == document_id)
        .order_by(DocumentContent.sequence.asc())
    )
    contents = list(session.scalars(contents_statement).all())

    try:
        chunk_drafts: list[tuple[DocumentContent, chunking.ChunkDraft]] = []
        for content in contents:
            drafts = chunking.split_text(
                content.text,
                chunk_size=chunk_size,
                overlap=overlap,
            )
            chunk_drafts.extend((content, draft) for draft in drafts)

        content_ids = [content.id for content in contents]
        chunks = [
            Chunk(
                document_content=content,
                sequence=draft.sequence,
                text=draft.text,
                start_offset=draft.start_offset,
                end_offset=draft.end_offset,
            )
            for content, draft in chunk_drafts
        ]
        if content_ids:
            session.execute(
                delete(Chunk).where(Chunk.document_content_id.in_(content_ids))
            )
        session.add_all(chunks)
        cleanup_required = embedding_cleanup.document_needs_embedding_cleanup(
            document
        )
        if cleanup_required:
            embedding_cleanup.mark_document_embedding_stale(document)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise DocumentChunkingPersistenceError(
            "Document chunks could not be saved"
        ) from exc

    if cleanup_required:
        document = embedding_cleanup.cleanup_stale_document_embedding(
            session,
            document_id,
            vector_store_factory=vector_store_factory,
        )
    return document, chunks
