from __future__ import annotations

from dataclasses import replace

import pytest

from app.services.context_assembly import (
    CONTEXT_BLOCK_SEPARATOR,
    ContextBlock,
    ContextCitation,
    assemble_context,
)
from app.services.retrieval import RetrievalResult


def retrieval_result(
    *,
    chunk_id: int = 1,
    text: str = "Growth rate compares change with the original value.",
    original_filename: str = "growth.txt",
    source_type: str = "line",
    source_start: int = 1,
    source_end: int = 1,
    distance: float = 0.125,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_content_id=chunk_id + 10,
        document_id=chunk_id + 20,
        knowledge_base_id=30,
        text=text,
        distance=distance,
        original_filename=original_filename,
        content_sequence=chunk_id + 40,
        chunk_sequence=chunk_id + 50,
        source_type=source_type,
        source_start=source_start,
        source_end=source_end,
        start_offset=chunk_id + 60,
        end_offset=chunk_id + 60 + len(text),
    )


def test_assemble_context_handles_empty_results() -> None:
    assembled = assemble_context([])

    assert assembled.context == ""
    assert assembled.blocks == ()
    assert assembled.citations == ()
    assert assembled.used_characters == 0
    assert assembled.truncated is False


def test_assemble_context_renders_one_chunk_and_complete_citation() -> None:
    result = retrieval_result()

    assembled = assemble_context([result])

    expected_context = (
        "[1] growth.txt | line 1\n\n"
        "Growth rate compares change with the original value."
    )
    assert assembled.context == expected_context
    assert assembled.used_characters == len(expected_context)
    assert assembled.truncated is False
    assert assembled.blocks == (
        ContextBlock(
            citation_number=1,
            header="[1] growth.txt | line 1",
            text=result.text,
        ),
    )
    assert assembled.citations == (
        ContextCitation(
            citation_number=1,
            chunk_id=result.chunk_id,
            document_content_id=result.document_content_id,
            document_id=result.document_id,
            knowledge_base_id=result.knowledge_base_id,
            original_filename=result.original_filename,
            content_sequence=result.content_sequence,
            chunk_sequence=result.chunk_sequence,
            source_type=result.source_type,
            source_start=result.source_start,
            source_end=result.source_end,
            start_offset=result.start_offset,
            end_offset=result.end_offset,
            distance=result.distance,
        ),
    )


def test_assemble_context_preserves_retrieval_order_and_numbering() -> None:
    results = [
        retrieval_result(
            chunk_id=3,
            text="Third chunk selected first.",
            original_filename="third.pdf",
            source_type="page",
            source_start=3,
            source_end=3,
            distance=0.3,
        ),
        retrieval_result(
            chunk_id=1,
            text="First chunk selected second.",
            original_filename="first.docx",
            source_type="paragraph",
            source_start=8,
            source_end=8,
            distance=0.1,
        ),
        retrieval_result(
            chunk_id=2,
            text="Second chunk selected third.",
            original_filename="second.md",
            source_type="line_range",
            source_start=10,
            source_end=12,
            distance=0.2,
        ),
    ]

    assembled = assemble_context(results)

    expected_blocks = [
        "[1] third.pdf | page 3\n\nThird chunk selected first.",
        "[2] first.docx | paragraph 8\n\nFirst chunk selected second.",
        "[3] second.md | line_range 10-12\n\nSecond chunk selected third.",
    ]
    assert assembled.context == CONTEXT_BLOCK_SEPARATOR.join(expected_blocks)
    assert [block.text for block in assembled.blocks] == [
        result.text for result in results
    ]
    assert [citation.chunk_id for citation in assembled.citations] == [3, 1, 2]
    assert [citation.citation_number for citation in assembled.citations] == [
        1,
        2,
        3,
    ]


def test_assemble_context_accepts_exact_character_budget() -> None:
    result = retrieval_result(text="exact")
    complete_context = "[1] growth.txt | line 1\n\nexact"

    assembled = assemble_context(
        [result],
        max_context_characters=len(complete_context),
    )

    assert assembled.context == complete_context
    assert assembled.used_characters == len(complete_context)
    assert assembled.truncated is False


def test_assemble_context_stops_before_over_budget_block() -> None:
    first = retrieval_result(chunk_id=1, text="first")
    second = retrieval_result(chunk_id=2, text="second")
    first_block = "[1] growth.txt | line 1\n\nfirst"
    second_block = "[2] growth.txt | line 1\n\nsecond"
    complete_context = CONTEXT_BLOCK_SEPARATOR.join(
        [first_block, second_block]
    )

    assembled = assemble_context(
        [first, second],
        max_context_characters=len(complete_context) - 1,
    )

    assert assembled.context == first_block
    assert assembled.used_characters == len(first_block)
    assert [block.citation_number for block in assembled.blocks] == [1]
    assert [citation.citation_number for citation in assembled.citations] == [1]
    assert assembled.truncated is True


def test_assemble_context_does_not_truncate_first_chunk() -> None:
    result = retrieval_result(text="a chunk that must remain whole")

    assembled = assemble_context([result], max_context_characters=1)

    assert assembled.context == ""
    assert assembled.blocks == ()
    assert assembled.citations == ()
    assert assembled.used_characters == 0
    assert assembled.truncated is True


def test_assemble_context_does_not_modify_retrieval_results() -> None:
    results = [retrieval_result(), retrieval_result(chunk_id=2, text="Second")]
    original_results = [replace(result) for result in results]

    assemble_context(results, max_context_characters=100)

    assert results == original_results


@pytest.mark.parametrize(
    ("filename", "source_type", "source_start", "source_end", "source_label"),
    [
        ("notes.pdf", "page", 3, 3, "page 3"),
        ("notes.docx", "paragraph", 4, 4, "paragraph 4"),
        ("notes.txt", "line", 5, 5, "line 5"),
        ("notes.md", "line_range", 6, 9, "line_range 6-9"),
    ],
)
def test_assemble_context_formats_each_supported_source_type(
    filename: str,
    source_type: str,
    source_start: int,
    source_end: int,
    source_label: str,
) -> None:
    result = retrieval_result(
        original_filename=filename,
        source_type=source_type,
        source_start=source_start,
        source_end=source_end,
        text="source text",
    )

    assembled = assemble_context([result])

    assert assembled.context == f"[1] {filename} | {source_label}\n\nsource text"
    assert assembled.citations[0].source_type == source_type
    assert assembled.citations[0].source_start == source_start
    assert assembled.citations[0].source_end == source_end


@pytest.mark.parametrize("invalid_budget", [0, -1, True, 1.5, "100"])
def test_assemble_context_rejects_invalid_character_budget(
    invalid_budget: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="max_context_characters must be a positive integer",
    ):
        assemble_context([], max_context_characters=invalid_budget)  # type: ignore[arg-type]
