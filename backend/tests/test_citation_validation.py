from __future__ import annotations

import pytest

from app.services.citation_validation import (
    CitationStatus,
    extract_citation_numbers,
    validate_citations,
)
from app.services.context_assembly import AssembledContext, ContextCitation


def citation(number: int) -> ContextCitation:
    return ContextCitation(
        citation_number=number,
        chunk_id=number,
        document_content_id=number,
        document_id=number,
        knowledge_base_id=1,
        original_filename=f"source-{number}.txt",
        content_sequence=number - 1,
        chunk_sequence=0,
        source_type="line",
        source_start=number,
        source_end=number,
        start_offset=0,
        end_offset=10,
        distance=number / 10,
    )


def assembled_context(*citation_numbers: int) -> AssembledContext:
    citations = tuple(citation(number) for number in citation_numbers)
    return AssembledContext(
        context="context" if citations else "",
        blocks=(),
        citations=citations,
        used_characters=7 if citations else 0,
        truncated=False,
    )


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("结论见 [3]，基础见 [1]。", (1, 3)),
        ("重复引用 [2]、[2] 和 [2]。", (2,)),
        ("非连续引用 [5] [1]。", (1, 5)),
        ("空答案没有编号。", ()),
        ("[1, 2] 和 [source] 不是支持的引用格式。", ()),
    ],
)
def test_extract_citation_numbers_returns_sorted_unique_numbers(
    answer: str,
    expected: tuple[int, ...],
) -> None:
    assert extract_citation_numbers(answer) == expected


def test_validate_citations_accepts_existing_non_contiguous_references() -> None:
    result = validate_citations(
        "第二个来源 [3]，第一个来源 [1]，再次引用 [3]。",
        assembled_context(1, 2, 3),
    )

    assert result.referenced_ids == (1, 3)
    assert result.valid is True
    assert result.invalid_ids == ()
    assert result.has_citation is True
    assert result.status == CitationStatus.VALID


def test_validate_citations_marks_unknown_numbers_invalid() -> None:
    result = validate_citations(
        "一个有效来源 [2]，一个不存在的来源 [9]。",
        assembled_context(1, 2, 3),
    )

    assert result.referenced_ids == (2, 9)
    assert result.valid is False
    assert result.invalid_ids == (9,)
    assert result.has_citation is True
    assert result.status == CitationStatus.INVALID_REFERENCE


@pytest.mark.parametrize("answer", ["", "没有引用的回答。"])
def test_validate_citations_marks_empty_or_uncited_answers_missing(
    answer: str,
) -> None:
    result = validate_citations(answer, assembled_context(1))

    assert result.referenced_ids == ()
    assert result.valid is False
    assert result.invalid_ids == ()
    assert result.has_citation is False
    assert result.status == CitationStatus.MISSING
