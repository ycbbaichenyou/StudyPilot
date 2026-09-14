from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, KnowledgeBase
from app.services import embedding_cleanup
from app.services import documents as document_service
from app.stores import get_vector_store


class KnowledgeBaseDeletionPersistenceError(RuntimeError):
    pass


def create_knowledge_base(
    session: Session,
    *,
    name: str,
    description: str | None,
) -> KnowledgeBase:
    knowledge_base = KnowledgeBase(name=name, description=description)
    session.add(knowledge_base)
    session.commit()
    session.refresh(knowledge_base)
    return knowledge_base


def list_knowledge_bases(session: Session) -> list[KnowledgeBase]:
    statement = select(KnowledgeBase).order_by(KnowledgeBase.id)
    return list(session.scalars(statement).all())


def get_knowledge_base(
    session: Session,
    knowledge_base_id: int,
) -> KnowledgeBase | None:
    return session.get(KnowledgeBase, knowledge_base_id)


def delete_knowledge_base(
    session: Session,
    knowledge_base_id: int,
    *,
    upload_directory: Path,
    vector_store_factory: embedding_cleanup.VectorStoreFactory = get_vector_store,
) -> bool:
    knowledge_base = get_knowledge_base(session, knowledge_base_id)
    if knowledge_base is None:
        return False

    documents = list(
        session.scalars(
            select(Document)
            .where(Document.knowledge_base_id == knowledge_base_id)
            .order_by(Document.id)
        ).all()
    )
    stored_files = [(document.id, document.filename) for document in documents]
    documents_to_clean = [
        document
        for document in documents
        if embedding_cleanup.document_needs_embedding_cleanup(document)
    ]

    if documents_to_clean:
        for document in documents_to_clean:
            embedding_cleanup.mark_document_embedding_stale(document)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise KnowledgeBaseDeletionPersistenceError(
                "Knowledge base document stale states could not be saved"
            ) from exc

        for document in documents_to_clean:
            embedding_cleanup.cleanup_document_embedding(
                session,
                document.id,
                vector_store_factory=vector_store_factory,
            )

    try:
        session.delete(knowledge_base)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise KnowledgeBaseDeletionPersistenceError(
            "The knowledge base could not be deleted"
        ) from exc

    for document_id, filename in stored_files:
        document_service.delete_stored_file(
            upload_directory=upload_directory,
            document_id=document_id,
            filename=filename,
        )
    return True
