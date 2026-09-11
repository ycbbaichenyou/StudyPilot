from pathlib import Path

from app.document_processing.exceptions import DocumentParsingError
from app.document_processing.schemas import ParsedTextUnit


def parse_markdown(path: Path) -> list[ParsedTextUnit]:
    """Read Markdown as one unchanged text unit with its complete line range."""
    try:
        with path.open("r", encoding="utf-8", newline="") as source:
            text = source.read()
    except UnicodeDecodeError as exc:
        raise DocumentParsingError("Markdown file must use UTF-8 encoding") from exc
    except OSError as exc:
        raise DocumentParsingError("Markdown file could not be read") from exc

    line_count = max(1, len(text.splitlines()))
    return [
        ParsedTextUnit(
            text=text,
            sequence=0,
            source_type="line_range",
            source_start=1,
            source_end=line_count,
        )
    ]
