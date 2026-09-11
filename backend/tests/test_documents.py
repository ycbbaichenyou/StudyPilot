from io import BytesIO
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Document, KnowledgeBase
from app.services import documents as document_service
from app.services.documents import MAX_FILE_SIZE


def create_knowledge_base(client: TestClient) -> int:
    response = client.post(
        "/api/knowledge-bases",
        json={"name": "Computer Science", "description": None},
    )
    assert response.status_code == 201
    knowledge_base_id = response.json()["id"]
    assert isinstance(knowledge_base_id, int)
    return knowledge_base_id


def upload_file(
    client: TestClient,
    knowledge_base_id: int,
    *,
    filename: str = "lecture.pdf",
    content: bytes = b"%PDF-1.7 test content",
    content_type: str = "application/pdf",
):
    return client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": (filename, BytesIO(content), content_type)},
    )


def test_pdf_upload_creates_record_and_saved_file(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    knowledge_base_id = create_knowledge_base(client)

    response = upload_file(client, knowledge_base_id)

    assert response.status_code == 201
    body = response.json()
    assert body["knowledge_base_id"] == knowledge_base_id
    assert body["original_filename"] == "lecture.pdf"
    assert body["file_type"] == "pdf"
    assert body["file_size"] == len(b"%PDF-1.7 test content")
    assert body["status"] == "pending"

    stored_path = upload_directory / body["filename"]
    assert stored_path.is_file()
    assert stored_path.read_bytes() == b"%PDF-1.7 test content"

    with test_session_factory() as session:
        document = session.get(Document, body["id"])
        assert document is not None
        assert document.knowledge_base_id == knowledge_base_id
        assert document.filename == body["filename"]
        assert document.original_filename == "lecture.pdf"
        assert document.file_type == "pdf"
        assert document.file_size == len(b"%PDF-1.7 test content")


def test_list_documents_returns_only_requested_knowledge_base(
    client: TestClient,
) -> None:
    first_knowledge_base_id = create_knowledge_base(client)
    second_knowledge_base_id = create_knowledge_base(client)
    first = upload_file(client, first_knowledge_base_id, filename="first.pdf").json()
    second = upload_file(
        client,
        first_knowledge_base_id,
        filename="second.md",
        content=b"# Notes",
        content_type="text/markdown",
    ).json()
    upload_file(
        client,
        second_knowledge_base_id,
        filename="other.txt",
        content=b"Other",
    )

    response = client.get(
        f"/api/knowledge-bases/{first_knowledge_base_id}/documents"
    )

    assert response.status_code == 200
    assert response.json() == [first, second]


def test_upload_to_missing_knowledge_base_returns_404(
    client: TestClient,
    upload_directory: Path,
) -> None:
    response = upload_file(client, 999)

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}
    assert not upload_directory.exists()


@pytest.mark.parametrize("filename", ["notes.exe", "notes.pdf.exe", "no-extension"])
def test_unsupported_file_type_is_rejected(
    client: TestClient,
    upload_directory: Path,
    filename: str,
) -> None:
    knowledge_base_id = create_knowledge_base(client)

    response = upload_file(client, knowledge_base_id, filename=filename)

    assert response.status_code == 400
    assert response.json() == {"detail": "Unsupported file type"}
    assert not upload_directory.exists()


@pytest.mark.parametrize(
    ("filename", "expected_file_type"),
    [
        ("notes.DOCX", "docx"),
        ("notes.txt", "txt"),
        ("notes.md", "md"),
    ],
)
def test_other_supported_file_types_are_accepted(
    client: TestClient,
    filename: str,
    expected_file_type: str,
) -> None:
    knowledge_base_id = create_knowledge_base(client)

    response = upload_file(client, knowledge_base_id, filename=filename)

    assert response.status_code == 201
    assert response.json()["file_type"] == expected_file_type


def test_empty_file_is_rejected(
    client: TestClient,
    upload_directory: Path,
) -> None:
    knowledge_base_id = create_knowledge_base(client)

    response = upload_file(client, knowledge_base_id, content=b"")

    assert response.status_code == 400
    assert response.json() == {"detail": "File must not be empty"}
    assert list(upload_directory.iterdir()) == []


def test_file_over_20_mb_is_rejected(
    client: TestClient,
    upload_directory: Path,
) -> None:
    knowledge_base_id = create_knowledge_base(client)

    response = upload_file(
        client,
        knowledge_base_id,
        content=b"x" * (MAX_FILE_SIZE + 1),
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "File exceeds the 20 MB limit"}
    assert list(upload_directory.iterdir()) == []


def test_delete_document_removes_record_and_file(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()
    stored_path = upload_directory / uploaded["filename"]
    assert stored_path.exists()

    response = client.delete(f"/api/documents/{uploaded['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert not stored_path.exists()
    assert list(upload_directory.iterdir()) == []
    with test_session_factory() as session:
        assert session.get(Document, uploaded["id"]) is None


def test_delete_missing_document_returns_404(client: TestClient) -> None:
    response = client.delete("/api/documents/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


def test_user_filename_is_not_used_as_storage_path(
    client: TestClient,
    upload_directory: Path,
) -> None:
    knowledge_base_id = create_knowledge_base(client)
    original_filename = "../../unsafe name.PDF"

    response = upload_file(
        client,
        knowledge_base_id,
        filename=original_filename,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["original_filename"] == original_filename
    assert body["filename"].endswith(".pdf")
    assert body["filename"] != original_filename
    assert Path(body["filename"]).name == body["filename"]
    assert (upload_directory / body["filename"]).is_file()
    assert not (upload_directory.parent / "unsafe name.PDF").exists()


def test_file_save_failure_does_not_create_document(
    tmp_path: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    blocked_upload_directory = tmp_path / "not-a-directory"
    blocked_upload_directory.write_text("blocking file")

    with test_session_factory() as session:
        knowledge_base = KnowledgeBase(name="Computer Science")
        session.add(knowledge_base)
        session.commit()
        knowledge_base_id = knowledge_base.id

        with pytest.raises(FileExistsError):
            document_service.create_document(
                session,
                knowledge_base_id=knowledge_base_id,
                source=BytesIO(b"content"),
                original_filename="notes.txt",
                upload_directory=blocked_upload_directory,
            )

    with test_session_factory() as session:
        assert list(session.scalars(select(Document)).all()) == []


def test_database_failure_removes_saved_file(
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        knowledge_base = KnowledgeBase(name="Computer Science")
        session.add(knowledge_base)
        session.commit()
        knowledge_base_id = knowledge_base.id

        def fail_commit_after_flush() -> None:
            session.flush()
            raise RuntimeError("database write failed")

        session.rollback = Mock(wraps=session.rollback)
        session.commit = Mock(side_effect=fail_commit_after_flush)
        with pytest.raises(RuntimeError, match="database write failed"):
            document_service.create_document(
                session,
                knowledge_base_id=knowledge_base_id,
                source=BytesIO(b"content"),
                original_filename="notes.txt",
                upload_directory=upload_directory,
            )
        session.rollback.assert_called_once()

    assert list(upload_directory.iterdir()) == []
    with test_session_factory() as session:
        assert list(session.scalars(select(Document)).all()) == []


def test_delete_document_succeeds_when_stored_file_is_missing(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()
    stored_path = upload_directory / uploaded["filename"]
    stored_path.unlink()

    response = client.delete(f"/api/documents/{uploaded['id']}")

    assert response.status_code == 204
    assert response.content == b""
    with test_session_factory() as session:
        assert session.get(Document, uploaded["id"]) is None


def test_delete_document_logs_warning_when_stored_file_deletion_fails(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()
    stored_path = upload_directory / uploaded["filename"]
    original_unlink = Path.unlink

    def fail_stored_file_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == stored_path:
            raise PermissionError("file is in use")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_stored_file_unlink)

    with caplog.at_level(logging.WARNING):
        response = client.delete(f"/api/documents/{uploaded['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert "Failed to delete the stored file" in caplog.text
    assert "PermissionError" in caplog.text
    assert stored_path.is_file()
    with test_session_factory() as session:
        assert session.get(Document, uploaded["id"]) is None
