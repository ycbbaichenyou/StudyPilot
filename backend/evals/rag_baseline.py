from __future__ import annotations

from dataclasses import dataclass

from app.services.answer_generation import AnswerStatus
from app.services.citation_validation import CitationStatus
from app.services.context_assembly import DEFAULT_MAX_CONTEXT_CHARACTERS
from app.services.retrieval import RetrievalResult


@dataclass(frozen=True, slots=True)
class RAGEvaluationCase:
    name: str
    query: str
    retrieval_results: tuple[RetrievalResult, ...]
    mock_answer: str
    expected_answer_status: AnswerStatus
    expected_citation_status: CitationStatus
    expected_referenced_ids: tuple[int, ...]
    expected_returned_citation_numbers: tuple[int, ...]
    expected_context_truncated: bool = False
    max_context_characters: int = DEFAULT_MAX_CONTEXT_CHARACTERS


def _retrieval_result(
    *,
    chunk_id: int,
    document_id: int,
    filename: str,
    text: str,
    distance: float,
    line: int = 1,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_content_id=chunk_id,
        document_id=document_id,
        knowledge_base_id=1,
        text=text,
        distance=distance,
        original_filename=filename,
        content_sequence=0,
        chunk_sequence=0,
        source_type="line",
        source_start=line,
        source_end=line,
        start_offset=0,
        end_offset=len(text),
    )


_GROWTH_RESULT = _retrieval_result(
    chunk_id=1,
    document_id=1,
    filename="growth.txt",
    text="增长率表示某个量在一段时间内的相对变化。",
    distance=0.1,
)
_COMPARISON_RESULT = _retrieval_result(
    chunk_id=2,
    document_id=2,
    filename="comparison.txt",
    text="比较增长率时，应使用相同的时间区间和统计口径。",
    distance=0.2,
)
_TRUNCATION_FIRST_RESULT = _retrieval_result(
    chunk_id=3,
    document_id=3,
    filename="first.txt",
    text="第一段资料。",
    distance=0.1,
)
_TRUNCATION_SECOND_RESULT = _retrieval_result(
    chunk_id=4,
    document_id=4,
    filename="second.txt",
    text="第二段资料。",
    distance=0.2,
)
_FIRST_BLOCK_BUDGET = len(
    f"[1] {_TRUNCATION_FIRST_RESULT.original_filename} | line 1"
    f"\n\n{_TRUNCATION_FIRST_RESULT.text}"
)


RAG_EVALUATION_BASELINE: tuple[RAGEvaluationCase, ...] = (
    RAGEvaluationCase(
        name="answerable_question",
        query="什么是增长率？",
        retrieval_results=(_GROWTH_RESULT,),
        mock_answer="增长率描述相对变化。[1]",
        expected_answer_status=AnswerStatus.ANSWERED,
        expected_citation_status=CitationStatus.VALID,
        expected_referenced_ids=(1,),
        expected_returned_citation_numbers=(1,),
    ),
    RAGEvaluationCase(
        name="unanswerable_question",
        query="资料中没有的问题是什么？",
        retrieval_results=(),
        mock_answer="这个 mock 回答不应被调用。",
        expected_answer_status=AnswerStatus.INSUFFICIENT_CONTEXT,
        expected_citation_status=CitationStatus.MISSING,
        expected_referenced_ids=(),
        expected_returned_citation_numbers=(),
    ),
    RAGEvaluationCase(
        name="multiple_document_sources",
        query="增长率是什么，比较时要注意什么？",
        retrieval_results=(_GROWTH_RESULT, _COMPARISON_RESULT),
        mock_answer="比较时要统一口径 [2]；增长率描述相对变化 [1]。",
        expected_answer_status=AnswerStatus.ANSWERED,
        expected_citation_status=CitationStatus.VALID,
        expected_referenced_ids=(1, 2),
        expected_returned_citation_numbers=(1, 2),
    ),
    RAGEvaluationCase(
        name="context_truncation",
        query="概括两段资料。",
        retrieval_results=(
            _TRUNCATION_FIRST_RESULT,
            _TRUNCATION_SECOND_RESULT,
        ),
        mock_answer="当前预算只纳入第一段资料。[1]",
        expected_answer_status=AnswerStatus.ANSWERED,
        expected_citation_status=CitationStatus.VALID,
        expected_referenced_ids=(1,),
        expected_returned_citation_numbers=(1,),
        expected_context_truncated=True,
        max_context_characters=_FIRST_BLOCK_BUDGET,
    ),
    RAGEvaluationCase(
        name="invalid_reference",
        query="什么是增长率？",
        retrieval_results=(_GROWTH_RESULT,),
        mock_answer="增长率描述相对变化，但这个编号不存在。[9]",
        expected_answer_status=AnswerStatus.ANSWERED,
        expected_citation_status=CitationStatus.INVALID_REFERENCE,
        expected_referenced_ids=(9,),
        expected_returned_citation_numbers=(),
    ),
    RAGEvaluationCase(
        name="answer_without_citation",
        query="什么是增长率？",
        retrieval_results=(_GROWTH_RESULT,),
        mock_answer="增长率描述相对变化。",
        expected_answer_status=AnswerStatus.ANSWERED,
        expected_citation_status=CitationStatus.MISSING,
        expected_referenced_ids=(),
        expected_returned_citation_numbers=(),
    ),
)
