from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.embeddings import (
    DashScopeEmbeddingError,
    DashScopeTextEmbedding,
    get_embedding_model,
)
from app.models import KnowledgeBase
from app.services import documents as document_service
from app.services import embedding_cleanup as embedding_cleanup_service
from app.services import knowledge_bases as knowledge_base_service
from app.services import context_assembly as context_assembly_service
from app.services import retrieval as retrieval_service
from app.stores import (
    ChromaVectorStore,
    ChromaVectorStoreError,
    get_search_vector_store_factory,
    get_vector_store_factory,
)


router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])
DatabaseSession = Annotated[Session, Depends(get_db)]
UploadDirectory = Annotated[Path, Depends(document_service.get_upload_directory)]
QueryEmbeddingModel = Annotated[
    DashScopeTextEmbedding,
    Depends(get_embedding_model),
]
VectorStoreFactory = Annotated[
    Callable[[], ChromaVectorStore],
    Depends(get_vector_store_factory),
]
SearchVectorStoreFactory = Annotated[
    Callable[[], ChromaVectorStore],
    Depends(get_search_vector_store_factory),
]


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


class KnowledgeBaseSearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20, strict=True)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        cleaned_value = value.strip()
        if not cleaned_value:
            raise ValueError("query must not be empty")
        return cleaned_value


class KnowledgeBaseSearchResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class KnowledgeBaseSearchResponse(BaseModel):
    query: str
    results: list[KnowledgeBaseSearchResultResponse]


class KnowledgeBaseContextRequest(KnowledgeBaseSearchRequest):
    max_context_characters: int = Field(
        default=context_assembly_service.DEFAULT_MAX_CONTEXT_CHARACTERS,
        ge=1,
        strict=True,
    )


class ContextCitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    citation_number: int
    chunk_id: int
    document_content_id: int
    document_id: int
    knowledge_base_id: int
    original_filename: str
    content_sequence: int
    chunk_sequence: int
    source_type: str
    source_start: int
    source_end: int
    start_offset: int
    end_offset: int
    distance: float


class ContextBlockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    citation_number: int
    header: str
    text: str


class KnowledgeBaseContextResponse(BaseModel):
    query: str
    context: str
    blocks: list[ContextBlockResponse]
    citations: list[ContextCitationResponse]
    used_characters: int
    truncated: bool


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


@router.post(
    "/{knowledge_base_id}/search",
    response_model=KnowledgeBaseSearchResponse,
)
def search_knowledge_base(
    knowledge_base_id: int,
    payload: KnowledgeBaseSearchRequest,
    session: DatabaseSession,
    embedding_model: QueryEmbeddingModel,
    vector_store_factory: SearchVectorStoreFactory,
) -> KnowledgeBaseSearchResponse:
    try:
        results = retrieval_service.search_knowledge_base(
            session,
            knowledge_base_id,
            query=payload.query,
            top_k=payload.top_k,
            embedding_model=embedding_model,
            vector_store_factory=vector_store_factory,
        )
    except retrieval_service.KnowledgeBaseNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except retrieval_service.KnowledgeBaseNotSearchableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except DashScopeEmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Query embedding could not be generated",
        ) from exc
    except ChromaVectorStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge base search is temporarily unavailable",
        ) from exc

    return KnowledgeBaseSearchResponse(query=payload.query, results=results)


@router.post(
    "/{knowledge_base_id}/context",
    response_model=KnowledgeBaseContextResponse,
)
def assemble_knowledge_base_context(
    knowledge_base_id: int,
    payload: KnowledgeBaseContextRequest,
    session: DatabaseSession,
    embedding_model: QueryEmbeddingModel,
    vector_store_factory: SearchVectorStoreFactory,
) -> KnowledgeBaseContextResponse:
    try:
        results = retrieval_service.search_knowledge_base(
            session,
            knowledge_base_id,
            query=payload.query,
            top_k=payload.top_k,
            embedding_model=embedding_model,
            vector_store_factory=vector_store_factory,
        )
    except retrieval_service.KnowledgeBaseNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except retrieval_service.KnowledgeBaseNotSearchableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except DashScopeEmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Query embedding could not be generated",
        ) from exc
    except ChromaVectorStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge base search is temporarily unavailable",
        ) from exc

    assembled = context_assembly_service.assemble_context(
        results,
        max_context_characters=payload.max_context_characters,
    )
    return KnowledgeBaseContextResponse(
        query=payload.query,
        context=assembled.context,
        blocks=list(assembled.blocks),
        citations=list(assembled.citations),
        used_characters=assembled.used_characters,
        truncated=assembled.truncated,
    )


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
    vector_store_factory: VectorStoreFactory,
) -> Response:
    try:
        deleted = knowledge_base_service.delete_knowledge_base(
            session,
            knowledge_base_id,
            upload_directory=upload_directory,
            vector_store_factory=vector_store_factory,
        )
    except embedding_cleanup_service.DocumentEmbeddingCleanupError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=embedding_cleanup_service.SAFE_EMBEDDING_CLEANUP_ERROR,
        ) from exc
    except (
        embedding_cleanup_service.DocumentEmbeddingCleanupPersistenceError,
        knowledge_base_service.KnowledgeBaseDeletionPersistenceError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Knowledge base could not be deleted",
        ) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
