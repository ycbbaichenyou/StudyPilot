from pathlib import Path

import pymupdf

from app.document_processing.exceptions import DocumentParsingError
from app.document_processing.schemas import ParsedTextUnit


def parse_pdf(path: Path) -> list[ParsedTextUnit]:
    """Extract one text unit per PDF page."""
    try:
        with pymupdf.open(path) as document:
            return [
                ParsedTextUnit(
                    text=page.get_text(),
                    sequence=page_index,
                    source_type="page",
                    source_start=page_index + 1,
                    source_end=page_index + 1,
                )
                for page_index, page in enumerate(document)
            ]
    except Exception as exc:
        raise DocumentParsingError("PDF text extraction failed") from exc
