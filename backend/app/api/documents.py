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
from app.document_processing.exceptions import DocumentParsingPersistenceError
from app.models import Chunk, Document
from app.services import document_chunking as document_chunking_service
from app.services import document_parsing as document_parsing_service
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
    parse_error: str | None
    parsed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("parsed_at", "created_at", "updated_at", mode="after")
    @classmethod
    def mark_timestamp_as_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class DocumentContentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sequence: int
    text: str
    source_type: str
    source_start: int
    source_end: int


class DocumentContentsResponse(BaseModel):
    document_id: int
    status: str
    contents: list[DocumentContentResponse]


class ChunkResponse(BaseModel):
    id: int
    document_content_id: int
    content_sequence: int
    sequence: int
    text: str
    start_offset: int
    end_offset: int
    source_type: str
    source_start: int
    source_end: int


class DocumentChunksResponse(BaseModel):
    document_id: int
    status: str
    chunks: list[ChunkResponse]


def _build_document_chunks_response(
    document: Document,
    chunks: list[Chunk],
) -> DocumentChunksResponse:
    return DocumentChunksResponse(
        document_id=document.id,
        status=document.status,
        chunks=[
            ChunkResponse(
                id=chunk.id,
                document_content_id=chunk.document_content_id,
                content_sequence=chunk.document_content.sequence,
                sequence=chunk.sequence,
                text=chunk.text,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                source_type=chunk.document_content.source_type,
                source_start=chunk.document_content.source_start,
                source_end=chunk.document_content.source_end,
            )
            for chunk in chunks
        ],
    )


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


@router.get(
    "/api/documents/{document_id}",
    response_model=DocumentResponse,
)
def get_document(
    document_id: int,
    session: DatabaseSession,
) -> Document:
    document = document_service.get_document(session, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return document


@router.get(
    "/api/documents/{document_id}/contents",
    response_model=DocumentContentsResponse,
)
def get_document_contents(
    document_id: int,
    session: DatabaseSession,
) -> DocumentContentsResponse:
    result = document_service.get_document_contents(session, document_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    document, contents = result
    return DocumentContentsResponse(
        document_id=document.id,
        status=document.status,
        contents=[
            DocumentContentResponse.model_validate(content) for content in contents
        ],
    )


@router.get(
    "/api/documents/{document_id}/chunks",
    response_model=DocumentChunksResponse,
)
def get_document_chunks(
    document_id: int,
    session: DatabaseSession,
) -> DocumentChunksResponse:
    result = document_chunking_service.get_document_chunks(session, document_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    document, chunks = result
    return _build_document_chunks_response(document, chunks)


@router.post(
    "/api/documents/{document_id}/chunks",
    response_model=DocumentChunksResponse,
)
def rebuild_document_chunks(
    document_id: int,
    session: DatabaseSession,
) -> DocumentChunksResponse:
    try:
        result = document_chunking_service.rebuild_document_chunks(
            session,
            document_id,
        )
    except document_chunking_service.DocumentNotReadyForChunkingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except document_chunking_service.DocumentChunkingPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document chunks could not be saved",
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    document, chunks = result
    return _build_document_chunks_response(document, chunks)


@router.post(
    "/api/documents/{document_id}/parse",
    response_model=DocumentResponse,
)
def parse_document(
    document_id: int,
    session: DatabaseSession,
    upload_directory: UploadDirectory,
) -> Document:
    try:
        document = document_parsing_service.parse_document(
            session,
            document_id,
            upload_directory=upload_directory,
        )
    except DocumentParsingPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document parsing status could not be saved",
        ) from exc

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return document


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
