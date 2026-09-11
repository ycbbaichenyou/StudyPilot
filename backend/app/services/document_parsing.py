from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.document_processing.exceptions import (
    DocumentParsingError,
    DocumentParsingPersistenceError,
)
from app.document_processing.parsers import get_parser
from app.document_processing.schemas import ParsedTextUnit
from app.models import Document, DocumentContent, DocumentStatus
from app.services.documents import get_stored_file_path


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_parse_error(exc: Exception) -> str:
    if isinstance(exc, DocumentParsingError):
        return str(exc)
    return f"Document parsing failed ({type(exc).__name__})"


def _mark_as_parsing(session: Session, document: Document) -> None:
    document.status = DocumentStatus.PARSING.value
    document.parse_error = None
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise DocumentParsingPersistenceError(
            "The document parsing status could not be saved"
        ) from exc


def _replace_document_contents(
    session: Session,
    document: Document,
    parsed_units: list[ParsedTextUnit],
) -> None:
    session.execute(
        delete(DocumentContent).where(DocumentContent.document_id == document.id)
    )
    session.add_all(
        [
            DocumentContent(
                document_id=document.id,
                text=unit.text,
                sequence=unit.sequence,
                source_type=unit.source_type,
                source_start=unit.source_start,
                source_end=unit.source_end,
            )
            for unit in parsed_units
        ]
    )
    document.status = DocumentStatus.PARSED.value
    document.parsed_at = _naive_utc_now()
    document.parse_error = None
    session.commit()


def _mark_as_failed(
    session: Session,
    document_id: int,
    parse_error: str,
) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise DocumentParsingPersistenceError(
            "The document disappeared while its parsing status was being updated"
        )

    document.status = DocumentStatus.PARSE_FAILED.value
    document.parse_error = parse_error
    try:
        session.commit()
        return document
    except Exception as exc:
        session.rollback()
        raise DocumentParsingPersistenceError(
            "The document parsing failure status could not be saved"
        ) from exc


def parse_document(
    session: Session,
    document_id: int,
    *,
    upload_directory: Path,
) -> Document | None:
    document = session.get(Document, document_id)
    if document is None:
        return None

    _mark_as_parsing(session, document)

    try:
        parser = get_parser(document.file_type)
        stored_path = get_stored_file_path(upload_directory, document.filename)
        parsed_units = parser(stored_path)
    except Exception as exc:
        session.rollback()
        return _mark_as_failed(session, document_id, _safe_parse_error(exc))

    try:
        _replace_document_contents(session, document, parsed_units)
        return document
    except Exception:
        session.rollback()
        return _mark_as_failed(
            session,
            document_id,
            "Parsed document content could not be saved",
        )
