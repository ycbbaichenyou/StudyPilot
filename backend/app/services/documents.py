import logging
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, DocumentContent
from app.services import embedding_cleanup
from app.stores import get_vector_store


logger = logging.getLogger(__name__)

UPLOAD_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "uploads"
ALLOWED_FILE_TYPES = {"pdf", "docx", "txt", "md"}
MAX_FILE_SIZE = 20 * 1024 * 1024
READ_CHUNK_SIZE = 1024 * 1024


class DocumentValidationError(ValueError):
    pass


class DocumentTooLargeError(DocumentValidationError):
    pass


class DocumentDeletionPersistenceError(RuntimeError):
    pass


def get_upload_directory() -> Path:
    return UPLOAD_DIRECTORY


def _get_file_type(original_filename: str) -> str:
    if not original_filename or len(original_filename) > 255:
        raise DocumentValidationError("A valid filename is required")

    file_type = Path(original_filename).suffix.lower().removeprefix(".")
    if file_type not in ALLOWED_FILE_TYPES:
        raise DocumentValidationError("Unsupported file type")
    return file_type


def get_stored_file_path(upload_directory: Path, filename: str) -> Path:
    if Path(filename).name != filename:
        raise ValueError("Stored filename must not contain a path")
    return upload_directory / filename


def _save_uploaded_file(
    source: BinaryIO,
    *,
    upload_directory: Path,
    file_type: str,
) -> tuple[str, int]:
    upload_directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}.{file_type}"
    stored_path = get_stored_file_path(upload_directory, filename)
    temporary_path = upload_directory / f".{filename}.part"
    file_size = 0

    try:
        with temporary_path.open("xb") as destination:
            while chunk := source.read(READ_CHUNK_SIZE):
                file_size += len(chunk)
                if file_size > MAX_FILE_SIZE:
                    raise DocumentTooLargeError("File exceeds the 20 MB limit")
                destination.write(chunk)

        if file_size == 0:
            raise DocumentValidationError("File must not be empty")

        temporary_path.replace(stored_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return filename, file_size


def create_document(
    session: Session,
    *,
    knowledge_base_id: int,
    source: BinaryIO,
    original_filename: str,
    upload_directory: Path,
) -> Document:
    file_type = _get_file_type(original_filename)
    stored_path: Path | None = None
    database_committed = False

    try:
        filename, file_size = _save_uploaded_file(
            source,
            upload_directory=upload_directory,
            file_type=file_type,
        )
        stored_path = get_stored_file_path(upload_directory, filename)
        document = Document(
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            original_filename=original_filename,
            file_type=file_type,
            file_size=file_size,
        )
        session.add(document)
        session.commit()
        database_committed = True
        session.refresh(document)
        return document
    except Exception:
        session.rollback()
        raise
    finally:
        if stored_path is not None and not database_committed:
            stored_path.unlink(missing_ok=True)


def list_documents(session: Session, knowledge_base_id: int) -> list[Document]:
    statement = (
        select(Document)
        .where(Document.knowledge_base_id == knowledge_base_id)
        .order_by(Document.id)
    )
    return list(session.scalars(statement).all())


def get_document(session: Session, document_id: int) -> Document | None:
    return session.get(Document, document_id)


def get_document_contents(
    session: Session,
    document_id: int,
) -> tuple[Document, list[DocumentContent]] | None:
    document = get_document(session, document_id)
    if document is None:
        return None

    statement = (
        select(DocumentContent)
        .where(DocumentContent.document_id == document_id)
        .order_by(DocumentContent.sequence.asc())
    )
    contents = list(session.scalars(statement).all())
    return document, contents


def delete_stored_file(
    *,
    upload_directory: Path,
    document_id: int,
    filename: str,
) -> None:
    try:
        stored_path = get_stored_file_path(upload_directory, filename)
        stored_path.unlink()
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        logger.warning(
            "Failed to delete the stored file for document %s (%s)",
            document_id,
            type(exc).__name__,
        )


def delete_document(
    session: Session,
    document_id: int,
    *,
    upload_directory: Path,
    vector_store_factory: embedding_cleanup.VectorStoreFactory = get_vector_store,
) -> bool:
    document = session.get(Document, document_id)
    if document is None:
        return False

    filename = document.filename
    cleanup_required = embedding_cleanup.document_needs_embedding_cleanup(document)

    if cleanup_required:
        embedding_cleanup.mark_document_embedding_stale(document)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise DocumentDeletionPersistenceError(
                "The document embedding stale status could not be saved"
            ) from exc

        embedding_cleanup.cleanup_document_embedding(
            session,
            document_id,
            vector_store_factory=vector_store_factory,
        )

    try:
        session.delete(document)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise DocumentDeletionPersistenceError(
            "The document could not be deleted"
        ) from exc

    delete_stored_file(
        upload_directory=upload_directory,
        document_id=document_id,
        filename=filename,
    )
    return True
