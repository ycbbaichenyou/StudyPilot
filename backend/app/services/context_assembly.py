from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.services.retrieval import RetrievalResult


DEFAULT_MAX_CONTEXT_CHARACTERS = 6000
CONTEXT_BLOCK_SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True, slots=True)
class ContextCitation:
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


@dataclass(frozen=True, slots=True)
class ContextBlock:
    citation_number: int
    header: str
    text: str


@dataclass(frozen=True, slots=True)
class AssembledContext:
    context: str
    blocks: tuple[ContextBlock, ...]
    citations: tuple[ContextCitation, ...]
    used_characters: int
    truncated: bool


def _format_source_information(result: RetrievalResult) -> str:
    if result.source_type == "line_range":
        return f"line_range {result.source_start}-{result.source_end}"
    if result.source_start == result.source_end:
        return f"{result.source_type} {result.source_start}"
    return f"{result.source_type} {result.source_start}-{result.source_end}"


def _build_citation(
    result: RetrievalResult,
    *,
    citation_number: int,
) -> ContextCitation:
    return ContextCitation(
        citation_number=citation_number,
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
    )


def assemble_context(
    results: Sequence[RetrievalResult],
    max_context_characters: int = DEFAULT_MAX_CONTEXT_CHARACTERS,
) -> AssembledContext:
    if (
        not isinstance(max_context_characters, int)
        or isinstance(max_context_characters, bool)
        or max_context_characters <= 0
    ):
        raise ValueError("max_context_characters must be a positive integer")

    rendered_blocks: list[str] = []
    blocks: list[ContextBlock] = []
    citations: list[ContextCitation] = []
    used_characters = 0
    truncated = False

    for result in results:
        citation_number = len(blocks) + 1
        header = (
            f"[{citation_number}] {result.original_filename} | "
            f"{_format_source_information(result)}"
        )
        rendered_block = f"{header}\n\n{result.text}"
        prefix = CONTEXT_BLOCK_SEPARATOR if rendered_blocks else ""
        added_characters = len(prefix) + len(rendered_block)
        if used_characters + added_characters > max_context_characters:
            truncated = True
            break

        rendered_blocks.append(rendered_block)
        blocks.append(
            ContextBlock(
                citation_number=citation_number,
                header=header,
                text=result.text,
            )
        )
        citations.append(
            _build_citation(result, citation_number=citation_number)
        )
        used_characters += added_characters

    context = CONTEXT_BLOCK_SEPARATOR.join(rendered_blocks)
    return AssembledContext(
        context=context,
        blocks=tuple(blocks),
        citations=tuple(citations),
        used_characters=used_characters,
        truncated=truncated,
    )
