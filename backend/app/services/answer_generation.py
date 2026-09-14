from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sqlalchemy.orm import Session

from app.llm import LLMMessage
from app.services import context_assembly as context_assembly_service
from app.services import retrieval as retrieval_service
from app.services.context_assembly import ContextCitation
from app.services.prompting import build_prompt


class LanguageModel(Protocol):
    def generate(self, messages: Sequence[LLMMessage]) -> str: ...


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_CONTEXT = "insufficient_context"


@dataclass(frozen=True, slots=True)
class AnswerGenerationResult:
    status: AnswerStatus
    answer: str | None
    citations: tuple[ContextCitation, ...]
    used_context_characters: int
    context_truncated: bool


def generate_answer(
    session: Session,
    knowledge_base_id: int,
    *,
    query: str,
    embedding_model: retrieval_service.QueryEmbeddingModel,
    vector_store_factory: retrieval_service.VectorStoreFactory,
    llm_model: LanguageModel,
    top_k: int = 5,
    max_context_characters: int = (
        context_assembly_service.DEFAULT_MAX_CONTEXT_CHARACTERS
    ),
) -> AnswerGenerationResult:
    retrieval_results = retrieval_service.search_knowledge_base(
        session,
        knowledge_base_id,
        query=query,
        top_k=top_k,
        embedding_model=embedding_model,
        vector_store_factory=vector_store_factory,
    )
    assembled_context = context_assembly_service.assemble_context(
        retrieval_results,
        max_context_characters=max_context_characters,
    )

    if not assembled_context.context:
        return AnswerGenerationResult(
            status=AnswerStatus.INSUFFICIENT_CONTEXT,
            answer=None,
            citations=assembled_context.citations,
            used_context_characters=assembled_context.used_characters,
            context_truncated=assembled_context.truncated,
        )

    messages = build_prompt(query, assembled_context)
    answer = llm_model.generate(messages)
    return AnswerGenerationResult(
        status=AnswerStatus.ANSWERED,
        answer=answer,
        citations=assembled_context.citations,
        used_context_characters=assembled_context.used_characters,
        context_truncated=assembled_context.truncated,
    )
