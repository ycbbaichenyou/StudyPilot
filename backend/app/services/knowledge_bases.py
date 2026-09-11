from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, KnowledgeBase
from app.services import documents as document_service


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

    try:
        for document in documents:
            session.delete(document)
        session.delete(knowledge_base)
        session.commit()
    except Exception:
        session.rollback()
        raise

    for document_id, filename in stored_files:
        document_service.delete_stored_file(
            upload_directory=upload_directory,
            document_id=document_id,
            filename=filename,
        )
    return True
