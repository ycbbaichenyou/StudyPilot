from pathlib import Path

from docx import Document

from app.document_processing.exceptions import DocumentParsingError
from app.document_processing.schemas import ParsedTextUnit


def parse_docx(path: Path) -> list[ParsedTextUnit]:
    """Extract Word body paragraphs in their original order."""
    try:
        document = Document(path)
        return [
            ParsedTextUnit(
                text=paragraph.text,
                sequence=paragraph_index,
                source_type="paragraph",
                source_start=paragraph_index + 1,
                source_end=paragraph_index + 1,
            )
            for paragraph_index, paragraph in enumerate(document.paragraphs)
        ]
    except Exception as exc:
        raise DocumentParsingError("DOCX text extraction failed") from exc
