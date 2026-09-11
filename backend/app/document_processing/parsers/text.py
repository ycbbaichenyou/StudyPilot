from pathlib import Path

from app.document_processing.exceptions import DocumentParsingError
from app.document_processing.schemas import ParsedTextUnit


def parse_text(path: Path) -> list[ParsedTextUnit]:
    """Read UTF-8 text, accepting an optional BOM, and retain line positions."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentParsingError("TXT file must use UTF-8 encoding") from exc
    except OSError as exc:
        raise DocumentParsingError("TXT file could not be read") from exc

    return [
        ParsedTextUnit(
            text=line,
            sequence=line_index,
            source_type="line",
            source_start=line_index + 1,
            source_end=line_index + 1,
        )
        for line_index, line in enumerate(text.splitlines())
    ]
