from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.llm import LLMMessage
from app.services import answer_generation as answer_generation_service
from app.services.citation_validation import extract_citation_numbers
from app.services.retrieval import RetrievalResult
from evals.rag_baseline import RAG_EVALUATION_BASELINE, RAGEvaluationCase


class BaselineMockLLM:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[tuple[LLMMessage, ...]] = []

    def generate(self, messages: Sequence[LLMMessage]) -> str:
        self.calls.append(tuple(messages))
        return self.answer


def test_rag_evaluation_baseline_has_required_fixed_cases() -> None:
    assert {case.name for case in RAG_EVALUATION_BASELINE} == {
        "answerable_question",
        "unanswerable_question",
        "multiple_document_sources",
        "context_truncation",
        "invalid_reference",
        "answer_without_citation",
    }


@pytest.mark.parametrize(
    "case",
    RAG_EVALUATION_BASELINE,
    ids=lambda case: case.name,
)
def test_rag_evaluation_baseline_uses_mock_answers(
    case: RAGEvaluationCase,
    test_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fixed_retrieval(
        *args: object,
        **kwargs: object,
    ) -> list[RetrievalResult]:
        return list(case.retrieval_results)

    monkeypatch.setattr(
        answer_generation_service.retrieval_service,
        "search_knowledge_base",
        fixed_retrieval,
    )
    llm_model = BaselineMockLLM(case.mock_answer)

    with test_session_factory() as session:
        result = answer_generation_service.generate_answer(
            session,
            1,
            query=case.query,
            embedding_model=object(),  # Retrieval is replaced by fixed inputs.
            vector_store_factory=object(),
            llm_model=llm_model,
            max_context_characters=case.max_context_characters,
        )

    assert result.status == case.expected_answer_status
    assert result.citation_status == case.expected_citation_status
    assert extract_citation_numbers(result.answer or "") == (
        case.expected_referenced_ids
    )
    assert tuple(item.citation_number for item in result.citations) == (
        case.expected_returned_citation_numbers
    )
    assert result.context_truncated is case.expected_context_truncated
    expected_llm_calls = 0 if not case.retrieval_results else 1
    assert len(llm_model.calls) == expected_llm_calls
