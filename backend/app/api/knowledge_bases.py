from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import KnowledgeBase
from app.services import documents as document_service
from app.services import knowledge_bases as knowledge_base_service


router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])
DatabaseSession = Annotated[Session, Depends(get_db)]
UploadDirectory = Annotated[Path, Depends(document_service.get_upload_directory)]


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(max_length=255)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned_value = value.strip()
        if not cleaned_value:
            raise ValueError("name must not be empty")
        return cleaned_value


class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def mark_timestamp_as_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def get_existing_knowledge_base(
    knowledge_base_id: int,
    session: Session,
) -> KnowledgeBase:
    knowledge_base = knowledge_base_service.get_knowledge_base(
        session,
        knowledge_base_id,
    )
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    return knowledge_base


@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    session: DatabaseSession,
) -> KnowledgeBase:
    return knowledge_base_service.create_knowledge_base(
        session,
        name=payload.name,
        description=payload.description,
    )


@router.get("", response_model=list[KnowledgeBaseResponse])
def list_knowledge_bases(session: DatabaseSession) -> list[KnowledgeBase]:
    return knowledge_base_service.list_knowledge_bases(session)


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
def get_knowledge_base(
    knowledge_base_id: int,
    session: DatabaseSession,
) -> KnowledgeBase:
    return get_existing_knowledge_base(knowledge_base_id, session)


@router.delete("/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_base(
    knowledge_base_id: int,
    session: DatabaseSession,
    upload_directory: UploadDirectory,
) -> Response:
    deleted = knowledge_base_service.delete_knowledge_base(
        session,
        knowledge_base_id,
        upload_directory=upload_directory,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
