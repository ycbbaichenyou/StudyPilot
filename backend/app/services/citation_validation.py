from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.services.context_assembly import AssembledContext


_CITATION_PATTERN = re.compile(r"\[(\d+)\]")


class CitationStatus(StrEnum):
    VALID = "valid"
    INVALID_REFERENCE = "invalid_reference"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class CitationValidationResult:
    referenced_ids: tuple[int, ...]
    valid: bool
    invalid_ids: tuple[int, ...]
    has_citation: bool

    @property
    def status(self) -> CitationStatus:
        if not self.has_citation:
            return CitationStatus.MISSING
        if self.invalid_ids:
            return CitationStatus.INVALID_REFERENCE
        return CitationStatus.VALID


def extract_citation_numbers(answer: str) -> tuple[int, ...]:
    return tuple(
        sorted({int(number) for number in _CITATION_PATTERN.findall(answer)})
    )


def validate_citations(
    answer: str,
    assembled_context: AssembledContext,
) -> CitationValidationResult:
    referenced_ids = extract_citation_numbers(answer)
    available_ids = {
        citation.citation_number for citation in assembled_context.citations
    }
    invalid_ids = tuple(
        citation_id
        for citation_id in referenced_ids
        if citation_id not in available_ids
    )
    has_citation = bool(referenced_ids)
    return CitationValidationResult(
        referenced_ids=referenced_ids,
        valid=has_citation and not invalid_ids,
        invalid_ids=invalid_ids,
        has_citation=has_citation,
    )
