from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeBase


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


def delete_knowledge_base(session: Session, knowledge_base_id: int) -> bool:
    knowledge_base = get_knowledge_base(session, knowledge_base_id)
    if knowledge_base is None:
        return False

    session.delete(knowledge_base)
    session.commit()
    return True
