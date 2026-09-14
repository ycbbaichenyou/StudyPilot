from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.embeddings import (
    DashScopeEmbeddingError,
    DashScopeTextEmbedding,
    get_embedding_model,
)
from app.llm import DashScopeLLM, DashScopeLLMError, get_llm_model
from app.services import answer_generation as answer_generation_service
from app.services import context_assembly as context_assembly_service
from app.services import retrieval as retrieval_service
from app.services.citation_validation import CitationStatus
from app.stores import (
    ChromaVectorStore,
    ChromaVectorStoreError,
    get_search_vector_store_factory,
)


router = APIRouter(prefix="/api/knowledge-bases", tags=["answers"])
DatabaseSession = Annotated[Session, Depends(get_db)]
QueryEmbeddingModel = Annotated[
    DashScopeTextEmbedding,
    Depends(get_embedding_model),
]
SearchVectorStoreFactory = Annotated[
    Callable[[], ChromaVectorStore],
    Depends(get_search_vector_store_factory),
]
AnswerLLM = Annotated[DashScopeLLM, Depends(get_llm_model)]


class KnowledgeBaseAnswerRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20, strict=True)
    max_context_characters: int = Field(
        default=context_assembly_service.DEFAULT_MAX_CONTEXT_CHARACTERS,
        ge=1,
        strict=True,
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        cleaned_value = value.strip()
        if not cleaned_value:
            raise ValueError("query must not be empty")
        return cleaned_value


class AnswerCitationResponse(BaseModel):
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


class KnowledgeBaseAnswerResponse(BaseModel):
    query: str
    status: answer_generation_service.AnswerStatus
    answer: str | None
    citations: list[AnswerCitationResponse]
    citation_status: CitationStatus
    used_context_characters: int
    context_truncated: bool


@router.post(
    "/{knowledge_base_id}/answer",
    response_model=KnowledgeBaseAnswerResponse,
)
def answer_knowledge_base_question(
    knowledge_base_id: int,
    payload: KnowledgeBaseAnswerRequest,
    session: DatabaseSession,
    embedding_model: QueryEmbeddingModel,
    vector_store_factory: SearchVectorStoreFactory,
    llm_model: AnswerLLM,
) -> KnowledgeBaseAnswerResponse:
    try:
        result = answer_generation_service.generate_answer(
            session,
            knowledge_base_id,
            query=payload.query,
            top_k=payload.top_k,
            max_context_characters=payload.max_context_characters,
            embedding_model=embedding_model,
            vector_store_factory=vector_store_factory,
            llm_model=llm_model,
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
    except DashScopeLLMError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Answer could not be generated",
        ) from exc

    return KnowledgeBaseAnswerResponse(
        query=payload.query,
        status=result.status,
        answer=result.answer,
        citations=list(result.citations),
        citation_status=result.citation_status,
        used_context_characters=result.used_context_characters,
        context_truncated=result.context_truncated,
    )
