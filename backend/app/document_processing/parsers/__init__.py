from collections.abc import Callable
from pathlib import Path

from app.document_processing.exceptions import UnsupportedDocumentTypeError
from app.document_processing.parsers.docx import parse_docx
from app.document_processing.parsers.markdown import parse_markdown
from app.document_processing.parsers.pdf import parse_pdf
from app.document_processing.parsers.text import parse_text
from app.document_processing.schemas import ParsedTextUnit


DocumentParser = Callable[[Path], list[ParsedTextUnit]]

PARSERS_BY_FILE_TYPE: dict[str, DocumentParser] = {
    "pdf": parse_pdf,
    "docx": parse_docx,
    "txt": parse_text,
    "md": parse_markdown,
}


def get_parser(file_type: str) -> DocumentParser:
    try:
        return PARSERS_BY_FILE_TYPE[file_type.lower()]
    except KeyError as exc:
        raise UnsupportedDocumentTypeError("Unsupported document type") from exc


__all__ = [
    "DocumentParser",
    "PARSERS_BY_FILE_TYPE",
    "get_parser",
    "parse_docx",
    "parse_markdown",
    "parse_pdf",
    "parse_text",
]
