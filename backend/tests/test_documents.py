from collections.abc import Callable, Generator
from datetime import datetime
from io import BytesIO
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database import get_db
from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    DocumentStatus,
    KnowledgeBase,
)
from app.services import documents as document_service
from app.services.documents import MAX_FILE_SIZE
from app.services.embedding_cleanup import SAFE_EMBEDDING_CLEANUP_ERROR
from app.stores import get_vector_store_factory


class ApiDocumentCleanupVectorStore:
    def __init__(
        self,
        *,
        record_count: int = 0,
        fail_delete: bool = False,
        remaining_after_delete: int = 0,
        on_delete: Callable[[int], None] | None = None,
    ) -> None:
        self.record_count = record_count
        self.fail_delete = fail_delete
        self.remaining_after_delete = remaining_after_delete
        self.on_delete = on_delete
        self.events: list[tuple[str, int]] = []

    def delete_document_records(self, document_id: int) -> None:
        self.events.append(("delete", document_id))
        if self.on_delete is not None:
            self.on_delete(document_id)
        if self.fail_delete:
            raise RuntimeError(
                "raw Chroma failure /private/chroma/path sk-secret-test"
            )
        self.record_count = self.remaining_after_delete

    def get_document_record_count(self, document_id: int) -> int:
        self.events.append(("count", document_id))
        return self.record_count


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


def add_document_graph(
    session: Session,
    document_id: int,
    *,
    embedding_status: DocumentEmbeddingStatus = DocumentEmbeddingStatus.EMBEDDED,
    generation_id: str | None = "a" * 32,
) -> tuple[int, int]:
    document = session.get(Document, document_id)
    assert document is not None
    document.status = DocumentStatus.PARSED.value
    document.embedding_status = embedding_status.value
    document.embedding_generation_id = generation_id
    document.embedded_at = (
        datetime(2026, 9, 13, 12, 0) if generation_id is not None else None
    )
    document.embedding_error = (
        "Previous embedding cleanup failed"
        if embedding_status == DocumentEmbeddingStatus.STALE
        else None
    )
    content = DocumentContent(
        document_id=document_id,
        sequence=0,
        text="Indexed content",
        source_type="line",
        source_start=1,
        source_end=1,
    )
    chunk = Chunk(
        document_content=content,
        sequence=0,
        text="Indexed content",
        start_offset=0,
        end_offset=len("Indexed content"),
    )
    session.add(content)
    session.commit()
    return content.id, chunk.id


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
    assert body["embedding_status"] == "pending"
    assert body["embedding_error"] is None
    assert body["embedded_at"] is None
    assert body["embedding_generation_id"] is None

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


def test_get_document_returns_current_document(client: TestClient) -> None:
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()

    response = client.get(f"/api/documents/{uploaded['id']}")

    assert response.status_code == 200
    assert response.json() == uploaded


def test_get_missing_document_returns_404(client: TestClient) -> None:
    response = client.get("/api/documents/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


def test_get_document_contents_returns_contents_in_sequence_order(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()
    document_id = uploaded["id"]

    with test_session_factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        document.status = DocumentStatus.PARSED.value
        contents = [
            DocumentContent(
                document_id=document_id,
                sequence=2,
                text="Third line",
                source_type="line_range",
                source_start=3,
                source_end=3,
            ),
            DocumentContent(
                document_id=document_id,
                sequence=0,
                text="First line",
                source_type="line_range",
                source_start=1,
                source_end=1,
            ),
            DocumentContent(
                document_id=document_id,
                sequence=1,
                text="Second line",
                source_type="line_range",
                source_start=2,
                source_end=2,
            ),
        ]
        session.add_all(contents)
        session.commit()
        content_ids = {content.sequence: content.id for content in contents}

    response = client.get(f"/api/documents/{document_id}/contents")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == document_id
    assert body["status"] == DocumentStatus.PARSED.value
    assert [content["sequence"] for content in body["contents"]] == [0, 1, 2]
    assert body["contents"] == [
        {
            "id": content_ids[0],
            "sequence": 0,
            "text": "First line",
            "source_type": "line_range",
            "source_start": 1,
            "source_end": 1,
        },
        {
            "id": content_ids[1],
            "sequence": 1,
            "text": "Second line",
            "source_type": "line_range",
            "source_start": 2,
            "source_end": 2,
        },
        {
            "id": content_ids[2],
            "sequence": 2,
            "text": "Third line",
            "source_type": "line_range",
            "source_start": 3,
            "source_end": 3,
        },
    ]


def test_get_pending_document_contents_returns_empty_list(
    client: TestClient,
) -> None:
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()

    response = client.get(f"/api/documents/{uploaded['id']}/contents")

    assert response.status_code == 200
    assert response.json() == {
        "document_id": uploaded["id"],
        "status": DocumentStatus.PENDING.value,
        "contents": [],
    }


def test_get_parse_failed_document_contents_returns_existing_contents(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()
    document_id = uploaded["id"]

    with test_session_factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        document.status = DocumentStatus.PARSE_FAILED.value
        document.parse_error = "Latest parse failed"
        session.add(
            DocumentContent(
                document_id=document_id,
                sequence=0,
                text="Last complete parse",
                source_type="line_range",
                source_start=1,
                source_end=1,
            )
        )
        session.commit()

    response = client.get(f"/api/documents/{document_id}/contents")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == document_id
    assert body["status"] == DocumentStatus.PARSE_FAILED.value
    assert [content["text"] for content in body["contents"]] == [
        "Last complete parse"
    ]


def test_get_contents_for_missing_document_returns_404(client: TestClient) -> None:
    response = client.get("/api/documents/999/contents")

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


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
    def fail_if_chroma_opens() -> ApiDocumentCleanupVectorStore:
        raise AssertionError("Chroma must not open without an old embedding")

    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )
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


def test_delete_embedded_document_cleans_chroma_before_database_and_file(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    stored_path = upload_directory / uploaded["filename"]
    with test_session_factory() as session:
        content_id, chunk_id = add_document_graph(session, document_id)

    observed_stale_state: list[tuple[str, str | None, bool]] = []

    def observe_stale_before_chroma(cleaned_document_id: int) -> None:
        with test_session_factory() as inspection_session:
            document = inspection_session.get(Document, cleaned_document_id)
            assert document is not None
            observed_stale_state.append(
                (
                    document.embedding_status,
                    document.embedding_generation_id,
                    stored_path.is_file(),
                )
            )
            assert document.embedded_at is None
            assert document.embedding_error is None
            assert inspection_session.get(DocumentContent, content_id) is not None
            assert inspection_session.get(Chunk, chunk_id) is not None

    vector_store = ApiDocumentCleanupVectorStore(
        record_count=2,
        on_delete=observe_stale_before_chroma,
    )
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )

    response = client.delete(f"/api/documents/{document_id}")

    assert response.status_code == 204
    assert response.content == b""
    assert observed_stale_state == [
        (DocumentEmbeddingStatus.STALE.value, None, True)
    ]
    assert vector_store.events == [("delete", document_id), ("count", document_id)]
    assert not stored_path.exists()
    with test_session_factory() as session:
        assert session.get(Document, document_id) is None
        assert session.get(DocumentContent, content_id) is None
        assert session.get(Chunk, chunk_id) is None


def test_delete_document_cleanup_failure_returns_503_and_preserves_everything(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = ApiDocumentCleanupVectorStore(
        record_count=2,
        fail_delete=True,
    )
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    stored_path = upload_directory / uploaded["filename"]
    with test_session_factory() as session:
        content_id, chunk_id = add_document_graph(session, document_id)

    response = client.delete(f"/api/documents/{document_id}")

    assert response.status_code == 503
    assert response.json() == {"detail": SAFE_EMBEDDING_CLEANUP_ERROR}
    assert "raw Chroma failure" not in response.text
    assert "/private/chroma/path" not in response.text
    assert "sk-secret-test" not in response.text
    assert stored_path.is_file()
    with test_session_factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.embedding_status == DocumentEmbeddingStatus.STALE.value
        assert document.embedding_generation_id is None
        assert document.embedded_at is None
        assert document.embedding_error == SAFE_EMBEDDING_CLEANUP_ERROR
        assert session.get(DocumentContent, content_id) is not None
        assert session.get(Chunk, chunk_id) is not None


def test_delete_document_verification_failure_does_not_delete_sqlite_or_file(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = ApiDocumentCleanupVectorStore(
        record_count=2,
        remaining_after_delete=1,
    )
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    stored_path = upload_directory / uploaded["filename"]
    with test_session_factory() as session:
        content_id, chunk_id = add_document_graph(session, document_id)

    response = client.delete(f"/api/documents/{document_id}")

    assert response.status_code == 503
    assert vector_store.events == [("delete", document_id), ("count", document_id)]
    assert stored_path.is_file()
    with test_session_factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.embedding_status == DocumentEmbeddingStatus.STALE.value
        assert document.embedding_generation_id is None
        assert document.embedded_at is None
        assert document.embedding_error == SAFE_EMBEDDING_CLEANUP_ERROR
        assert session.get(DocumentContent, content_id) is not None
        assert session.get(Chunk, chunk_id) is not None


def test_delete_stale_document_succeeds_when_chroma_is_already_empty(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = ApiDocumentCleanupVectorStore(record_count=0)
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    stored_path = upload_directory / uploaded["filename"]
    with test_session_factory() as session:
        add_document_graph(
            session,
            document_id,
            embedding_status=DocumentEmbeddingStatus.STALE,
            generation_id=None,
        )

    response = client.delete(f"/api/documents/{document_id}")

    assert response.status_code == 204
    assert vector_store.events == [("delete", document_id), ("count", document_id)]
    assert not stored_path.exists()
    with test_session_factory() as session:
        assert session.get(Document, document_id) is None


def test_delete_database_commit_failure_keeps_stale_document_and_can_retry(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = ApiDocumentCleanupVectorStore(record_count=2)
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    stored_path = upload_directory / uploaded["filename"]
    with test_session_factory() as session:
        content_id, chunk_id = add_document_graph(session, document_id)

    original_db_override = client.app.dependency_overrides[get_db]

    def override_failed_delete_db() -> Generator[Session, None, None]:
        with test_session_factory() as session:
            real_commit = session.commit
            commit_count = 0

            def fail_delete_commit() -> None:
                nonlocal commit_count
                commit_count += 1
                if commit_count == 2:
                    session.flush()
                    raise RuntimeError("database delete failed")
                real_commit()

            session.commit = fail_delete_commit  # type: ignore[method-assign]
            yield session

    client.app.dependency_overrides[get_db] = override_failed_delete_db
    try:
        failed_response = client.delete(f"/api/documents/{document_id}")
    finally:
        client.app.dependency_overrides[get_db] = original_db_override

    assert failed_response.status_code == 500
    assert failed_response.json() == {"detail": "Document could not be deleted"}
    assert vector_store.record_count == 0
    assert stored_path.is_file()
    with test_session_factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.embedding_status == DocumentEmbeddingStatus.STALE.value
        assert document.embedding_generation_id is None
        assert document.embedded_at is None
        assert document.embedding_error is None
        assert session.get(DocumentContent, content_id) is not None
        assert session.get(Chunk, chunk_id) is not None

    retry_response = client.delete(f"/api/documents/{document_id}")

    assert retry_response.status_code == 204
    assert vector_store.events == [
        ("delete", document_id),
        ("count", document_id),
        ("delete", document_id),
        ("count", document_id),
    ]
    assert not stored_path.exists()
    with test_session_factory() as session:
        assert session.get(Document, document_id) is None


def test_delete_embedding_failed_document_with_generation_runs_cleanup(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = ApiDocumentCleanupVectorStore(record_count=1)
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    knowledge_base_id = create_knowledge_base(client)
    uploaded = upload_file(client, knowledge_base_id).json()
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    with test_session_factory() as session:
        add_document_graph(
            session,
            document_id,
            embedding_status=DocumentEmbeddingStatus.EMBEDDING_FAILED,
        )

    response = client.delete(f"/api/documents/{document_id}")

    assert response.status_code == 204
    assert vector_store.events == [("delete", document_id), ("count", document_id)]
    with test_session_factory() as session:
        assert session.get(Document, document_id) is None


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
