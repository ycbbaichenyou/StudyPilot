from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from app.api.knowledge_bases import get_existing_knowledge_base
from app.database import get_db
from app.models import Document
from app.services import documents as document_service


router = APIRouter(tags=["documents"])
DatabaseSession = Annotated[Session, Depends(get_db)]
UploadDirectory = Annotated[Path, Depends(document_service.get_upload_directory)]


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    knowledge_base_id: int
    filename: str
    original_filename: str
    file_type: str
    file_size: int
    status: str
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def mark_timestamp_as_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


@router.post(
    "/api/knowledge-bases/{knowledge_base_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    knowledge_base_id: int,
    session: DatabaseSession,
    upload_directory: UploadDirectory,
    file: Annotated[UploadFile, File()],
) -> Document:
    get_existing_knowledge_base(knowledge_base_id, session)

    try:
        return document_service.create_document(
            session,
            knowledge_base_id=knowledge_base_id,
            source=file.file,
            original_filename=file.filename or "",
            upload_directory=upload_directory,
        )
    except document_service.DocumentTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except document_service.DocumentValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "/api/knowledge-bases/{knowledge_base_id}/documents",
    response_model=list[DocumentResponse],
)
def list_documents(
    knowledge_base_id: int,
    session: DatabaseSession,
) -> list[Document]:
    get_existing_knowledge_base(knowledge_base_id, session)
    return document_service.list_documents(session, knowledge_base_id)


@router.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    session: DatabaseSession,
    upload_directory: UploadDirectory,
) -> Response:
    deleted = document_service.delete_document(
        session,
        document_id,
        upload_directory=upload_directory,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
