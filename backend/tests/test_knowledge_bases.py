from collections.abc import Callable, Collection, Generator
from datetime import datetime, timedelta
from io import BytesIO
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database import get_db
from app.embeddings import DashScopeEmbeddingError, get_embedding_model
from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    KnowledgeBase,
)
from app.services.embedding_cleanup import SAFE_EMBEDDING_CLEANUP_ERROR
from app.services import knowledge_bases as knowledge_base_service
from app.stores import (
    ChromaRecord,
    ChromaSearchHit,
    ChromaVectorStore,
    ChromaVectorStoreError,
    get_search_vector_store_factory,
    get_vector_store_factory,
)


class ApiKnowledgeBaseCleanupVectorStore:
    def __init__(
        self,
        record_counts: dict[int, int],
        *,
        fail_document_id: int | None = None,
        on_delete: Callable[[int], None] | None = None,
    ) -> None:
        self.record_counts = dict(record_counts)
        self.fail_document_id = fail_document_id
        self.on_delete = on_delete
        self.events: list[tuple[str, int]] = []

    def delete_document_records(self, document_id: int) -> None:
        self.events.append(("delete", document_id))
        if self.on_delete is not None:
            self.on_delete(document_id)
        if document_id == self.fail_document_id:
            raise RuntimeError(
                "raw Chroma failure /private/chroma/path sk-secret-test"
            )
        self.record_counts[document_id] = 0

    def get_document_record_count(self, document_id: int) -> int:
        self.events.append(("count", document_id))
        return self.record_counts.get(document_id, 0)


class ApiSearchEmbeddingModel:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.queries: list[str] = []

    def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return [1.0, 0.0, 0.0]


class ApiSearchVectorStore:
    def __init__(
        self,
        hits: list[ChromaSearchHit],
        *,
        error: Exception | None = None,
    ) -> None:
        self.hits = hits
        self.error = error
        self.calls: list[tuple[list[float], set[str], int]] = []

    def search(
        self,
        query_embedding: list[float],
        *,
        allowed_record_ids: Collection[str],
        top_k: int,
    ) -> list[ChromaSearchHit]:
        self.calls.append((query_embedding, set(allowed_record_ids), top_k))
        if self.error is not None:
            raise self.error
        return self.hits


def create_knowledge_base(
    client: TestClient,
    *,
    name: str = "Computer Science",
    description: str | None = "Core course notes",
) -> dict[str, object]:
    response = client.post(
        "/api/knowledge-bases",
        json={
            "name": name,
            "description": description,
        },
    )
    assert response.status_code == 201
    return response.json()


def upload_document(
    client: TestClient,
    knowledge_base_id: int,
    *,
    filename: str,
) -> dict[str, object]:
    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": (filename, BytesIO(filename.encode()), "text/plain")},
    )
    assert response.status_code == 201
    body = response.json()
    assert isinstance(body, dict)
    return body


def add_document_graph(
    session: Session,
    document_id: int,
    *,
    embedding_status: DocumentEmbeddingStatus = DocumentEmbeddingStatus.EMBEDDED,
    generation_id: str | None = "a" * 32,
) -> tuple[int, int]:
    document = session.get(Document, document_id)
    assert document is not None
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
    text = f"Content for document {document_id}"
    content = DocumentContent(
        document_id=document_id,
        sequence=0,
        text=text,
        source_type="line",
        source_start=1,
        source_end=1,
    )
    chunk = Chunk(
        document_content=content,
        sequence=0,
        text=text,
        start_offset=0,
        end_offset=len(text),
    )
    session.add(content)
    session.commit()
    return content.id, chunk.id


def assert_explicit_utc_timestamp(value: object) -> datetime:
    assert isinstance(value, str)
    assert value.endswith(("Z", "+00:00"))
    parsed_value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed_value.utcoffset() == timedelta(0)
    return parsed_value


def test_create_knowledge_base(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)

    assert knowledge_base["id"] == 1
    assert knowledge_base["name"] == "Computer Science"
    assert knowledge_base["description"] == "Core course notes"
    assert_explicit_utc_timestamp(knowledge_base["created_at"])
    assert_explicit_utc_timestamp(knowledge_base["updated_at"])


def test_list_knowledge_bases(client: TestClient) -> None:
    first = create_knowledge_base(client, name="Computer Science")
    second = create_knowledge_base(client, name="Mathematics")

    response = client.get("/api/knowledge-bases")

    assert response.status_code == 200
    assert response.json() == [first, second]
    assert [item["id"] for item in response.json()] == [1, 2]


def test_get_knowledge_base(client: TestClient) -> None:
    created = create_knowledge_base(client)

    response = client.get(f"/api/knowledge-bases/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_database_roundtrip_keeps_api_timestamps_explicitly_utc(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)

    with test_session_factory() as session:
        stored = session.get(KnowledgeBase, knowledge_base_id)
        assert stored is not None
        assert stored.created_at.tzinfo is None
        assert stored.updated_at.tzinfo is None

    response = client.get(f"/api/knowledge-bases/{knowledge_base_id}")

    assert response.status_code == 200
    assert_explicit_utc_timestamp(response.json()["created_at"])
    assert_explicit_utc_timestamp(response.json()["updated_at"])


def test_get_missing_knowledge_base_returns_404(client: TestClient) -> None:
    response = client.get("/api/knowledge-bases/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}


def test_delete_knowledge_base_removes_documents_and_files(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    def fail_if_chroma_opens() -> ApiKnowledgeBaseCleanupVectorStore:
        raise AssertionError("Chroma must not open for pending documents")

    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)

    documents = []
    for filename, content in [
        ("notes.pdf", b"%PDF-1.7 test content"),
        ("summary.txt", b"summary"),
    ]:
        upload_response = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/documents",
            files={"file": (filename, BytesIO(content), "application/octet-stream")},
        )
        assert upload_response.status_code == 201
        documents.append(upload_response.json())

    stored_paths = [
        upload_directory / document["filename"] for document in documents
    ]
    assert all(stored_path.is_file() for stored_path in stored_paths)

    delete_response = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")
    get_response = client.get(f"/api/knowledge-bases/{knowledge_base_id}")

    assert delete_response.status_code == 204
    assert delete_response.content == b""
    assert get_response.status_code == 404
    assert all(not stored_path.exists() for stored_path in stored_paths)

    with test_session_factory() as session:
        assert session.get(KnowledgeBase, knowledge_base_id) is None
        for document in documents:
            assert session.get(Document, document["id"]) is None


def test_delete_knowledge_base_cleans_documents_before_sqlite_and_files(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)
    documents = [
        upload_document(client, knowledge_base_id, filename="first.txt"),
        upload_document(client, knowledge_base_id, filename="second.txt"),
    ]
    document_ids = [document["id"] for document in documents]
    assert all(isinstance(document_id, int) for document_id in document_ids)
    typed_document_ids = [int(document_id) for document_id in document_ids]
    stored_paths = [
        upload_directory / str(document["filename"]) for document in documents
    ]
    graph_ids: list[tuple[int, int]] = []
    with test_session_factory() as session:
        for document_id in typed_document_ids:
            graph_ids.append(add_document_graph(session, document_id))

    observed_stale_ids: list[int] = []

    def observe_all_stale_before_cleanup(document_id: int) -> None:
        with test_session_factory() as inspection_session:
            documents_in_db = list(
                inspection_session.scalars(
                    select(Document)
                    .where(Document.knowledge_base_id == knowledge_base_id)
                    .order_by(Document.id)
                ).all()
            )
            assert all(
                document.embedding_status == DocumentEmbeddingStatus.STALE.value
                and document.embedding_generation_id is None
                and document.embedded_at is None
                and document.embedding_error is None
                for document in documents_in_db
            )
            assert all(path.is_file() for path in stored_paths)
            observed_stale_ids.append(document_id)

    vector_store = ApiKnowledgeBaseCleanupVectorStore(
        {document_id: 1 for document_id in typed_document_ids},
        on_delete=observe_all_stale_before_cleanup,
    )
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )

    response = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")

    assert response.status_code == 204
    assert response.content == b""
    assert observed_stale_ids == typed_document_ids
    assert vector_store.events == [
        (operation, document_id)
        for document_id in typed_document_ids
        for operation in ("delete", "count")
    ]
    assert all(not path.exists() for path in stored_paths)
    with test_session_factory() as session:
        assert session.get(KnowledgeBase, knowledge_base_id) is None
        for document_id, (content_id, chunk_id) in zip(
            typed_document_ids,
            graph_ids,
            strict=True,
        ):
            assert session.get(Document, document_id) is None
            assert session.get(DocumentContent, content_id) is None
            assert session.get(Chunk, chunk_id) is None


def test_second_document_cleanup_failure_preserves_entire_knowledge_base(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)
    documents = [
        upload_document(client, knowledge_base_id, filename="first.txt"),
        upload_document(client, knowledge_base_id, filename="second.txt"),
    ]
    document_ids = [int(document["id"]) for document in documents]
    stored_paths = [
        upload_directory / str(document["filename"]) for document in documents
    ]
    graph_ids: list[tuple[int, int]] = []
    with test_session_factory() as session:
        for document_id in document_ids:
            graph_ids.append(add_document_graph(session, document_id))

    vector_store = ApiKnowledgeBaseCleanupVectorStore(
        {document_id: 1 for document_id in document_ids},
        fail_document_id=document_ids[1],
    )
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )

    response = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")

    assert response.status_code == 503
    assert response.json() == {"detail": SAFE_EMBEDDING_CLEANUP_ERROR}
    assert "raw Chroma failure" not in response.text
    assert vector_store.events == [
        ("delete", document_ids[0]),
        ("count", document_ids[0]),
        ("delete", document_ids[1]),
    ]
    assert vector_store.record_counts[document_ids[0]] == 0
    assert vector_store.record_counts[document_ids[1]] == 1
    assert all(path.is_file() for path in stored_paths)
    with test_session_factory() as session:
        assert session.get(KnowledgeBase, knowledge_base_id) is not None
        for index, (document_id, (content_id, chunk_id)) in enumerate(
            zip(document_ids, graph_ids, strict=True)
        ):
            document = session.get(Document, document_id)
            assert document is not None
            assert document.embedding_status == DocumentEmbeddingStatus.STALE.value
            assert document.embedding_generation_id is None
            assert document.embedded_at is None
            expected_error = (
                SAFE_EMBEDDING_CLEANUP_ERROR if index == 1 else None
            )
            assert document.embedding_error == expected_error
            assert session.get(DocumentContent, content_id) is not None
            assert session.get(Chunk, chunk_id) is not None


def test_delete_stale_knowledge_base_succeeds_when_chroma_is_already_empty(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)
    document = upload_document(client, knowledge_base_id, filename="notes.txt")
    document_id = int(document["id"])
    with test_session_factory() as session:
        add_document_graph(
            session,
            document_id,
            embedding_status=DocumentEmbeddingStatus.STALE,
            generation_id=None,
        )

    vector_store = ApiKnowledgeBaseCleanupVectorStore({document_id: 0})
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )

    response = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")

    assert response.status_code == 204
    assert vector_store.events == [("delete", document_id), ("count", document_id)]
    with test_session_factory() as session:
        assert session.get(KnowledgeBase, knowledge_base_id) is None


def test_delete_embedding_failed_document_in_knowledge_base_runs_cleanup(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)
    document = upload_document(client, knowledge_base_id, filename="notes.txt")
    document_id = int(document["id"])
    with test_session_factory() as session:
        add_document_graph(
            session,
            document_id,
            embedding_status=DocumentEmbeddingStatus.EMBEDDING_FAILED,
        )

    vector_store = ApiKnowledgeBaseCleanupVectorStore({document_id: 1})
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )

    response = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")

    assert response.status_code == 204
    assert vector_store.events == [("delete", document_id), ("count", document_id)]


def test_delete_knowledge_base_database_failure_rolls_back_all_documents(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)
    documents = []
    for filename in ["first.txt", "second.txt"]:
        upload_response = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/documents",
            files={"file": (filename, BytesIO(filename.encode()), "text/plain")},
        )
        assert upload_response.status_code == 201
        documents.append(upload_response.json())

    stored_paths = [
        upload_directory / document["filename"] for document in documents
    ]

    with test_session_factory() as session:
        def fail_commit_after_flush() -> None:
            session.flush()
            raise RuntimeError("database delete failed")

        session.rollback = Mock(wraps=session.rollback)
        session.commit = Mock(side_effect=fail_commit_after_flush)
        with pytest.raises(
            knowledge_base_service.KnowledgeBaseDeletionPersistenceError,
            match="knowledge base could not be deleted",
        ):
            knowledge_base_service.delete_knowledge_base(
                session,
                knowledge_base_id,
                upload_directory=upload_directory,
            )
        session.rollback.assert_called_once()

    with test_session_factory() as session:
        assert session.get(KnowledgeBase, knowledge_base_id) is not None
        for document in documents:
            assert session.get(Document, document["id"]) is not None
    assert all(stored_path.is_file() for stored_path in stored_paths)


def test_knowledge_base_delete_commit_failure_can_retry_after_chroma_cleanup(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)
    document = upload_document(client, knowledge_base_id, filename="notes.txt")
    document_id = int(document["id"])
    stored_path = upload_directory / str(document["filename"])
    with test_session_factory() as session:
        add_document_graph(session, document_id)

    vector_store = ApiKnowledgeBaseCleanupVectorStore({document_id: 1})
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
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
        failed_response = client.delete(
            f"/api/knowledge-bases/{knowledge_base_id}"
        )
    finally:
        client.app.dependency_overrides[get_db] = original_db_override

    assert failed_response.status_code == 500
    assert failed_response.json() == {
        "detail": "Knowledge base could not be deleted"
    }
    assert vector_store.record_counts[document_id] == 0
    assert stored_path.is_file()
    with test_session_factory() as session:
        assert session.get(KnowledgeBase, knowledge_base_id) is not None
        stored_document = session.get(Document, document_id)
        assert stored_document is not None
        assert stored_document.embedding_status == DocumentEmbeddingStatus.STALE.value
        assert stored_document.embedding_generation_id is None

    retry_response = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")

    assert retry_response.status_code == 204
    assert vector_store.events == [
        ("delete", document_id),
        ("count", document_id),
        ("delete", document_id),
        ("count", document_id),
    ]
    assert not stored_path.exists()
    with test_session_factory() as session:
        assert session.get(KnowledgeBase, knowledge_base_id) is None


def test_deleting_one_knowledge_base_preserves_other_chroma_records(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    first_knowledge_base = create_knowledge_base(client, name="First")
    second_knowledge_base = create_knowledge_base(client, name="Second")
    first_knowledge_base_id = int(first_knowledge_base["id"])
    second_knowledge_base_id = int(second_knowledge_base["id"])
    first_documents = [
        upload_document(client, first_knowledge_base_id, filename="first.txt"),
        upload_document(client, first_knowledge_base_id, filename="second.txt"),
    ]
    other_document = upload_document(
        client,
        second_knowledge_base_id,
        filename="other.txt",
    )
    first_document_ids = [int(document["id"]) for document in first_documents]
    other_document_id = int(other_document["id"])
    with test_session_factory() as session:
        chunk_ids: dict[int, int] = {}
        for document_id in [*first_document_ids, other_document_id]:
            _, chunk_id = add_document_graph(session, document_id)
            chunk_ids[document_id] = chunk_id

    vector_store = ChromaVectorStore(path=tmp_path / "chroma", dimensions=3)
    all_document_ids = [*first_document_ids, other_document_id]
    vector_store.add_records(
        [
            ChromaRecord(
                id=f"generation-{document_id}:{chunk_ids[document_id]}",
                text=f"Document {document_id}",
                embedding=[0.1, 0.2, 0.3],
                metadata={
                    "generation_id": f"generation-{document_id}",
                    "document_id": document_id,
                    "chunk_id": chunk_ids[document_id],
                },
            )
            for document_id in all_document_ids
        ]
    )
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )

    response = client.delete(
        f"/api/knowledge-bases/{first_knowledge_base_id}"
    )

    assert response.status_code == 204
    for document_id in first_document_ids:
        assert vector_store.get_document_record_count(document_id) == 0
    assert vector_store.get_document_record_count(other_document_id) == 1
    assert (
        upload_directory / str(other_document["filename"])
    ).is_file()
    with test_session_factory() as session:
        assert session.get(KnowledgeBase, first_knowledge_base_id) is None
        assert session.get(KnowledgeBase, second_knowledge_base_id) is not None
        assert session.get(Document, other_document_id) is not None


def test_delete_knowledge_base_ignores_missing_document_file(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)
    upload_response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("notes.txt", BytesIO(b"notes"), "text/plain")},
    )
    assert upload_response.status_code == 201
    document = upload_response.json()
    document_id = document["id"]
    stored_path = upload_directory / document["filename"]
    stored_path.unlink()

    delete_response = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")

    assert delete_response.status_code == 204
    with test_session_factory() as session:
        assert session.get(KnowledgeBase, knowledge_base_id) is None
        assert session.get(Document, document_id) is None


def test_delete_missing_knowledge_base_returns_404(client: TestClient) -> None:
    def fail_if_chroma_opens() -> ApiKnowledgeBaseCleanupVectorStore:
        raise AssertionError("Chroma must not open before the knowledge base lookup")

    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )
    response = client.delete("/api/knowledge-bases/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}


@pytest.mark.parametrize("name", ["", "   ", "x" * 256])
def test_invalid_knowledge_base_name_returns_422(
    client: TestClient,
    name: str,
) -> None:
    response = client.post(
        "/api/knowledge-bases",
        json={"name": name, "description": None},
    )

    assert response.status_code == 422


def test_delete_knowledge_base_logs_warning_when_file_unlink_fails(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = int(created["id"])
    document = upload_document(client, knowledge_base_id, filename="notes.txt")
    document_id = int(document["id"])
    stored_path = upload_directory / str(document["filename"])
    original_unlink = Path.unlink

    def fail_stored_file_unlink(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        if path == stored_path:
            raise PermissionError("file is in use")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_stored_file_unlink)

    with caplog.at_level(logging.WARNING):
        response = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")

    assert response.status_code == 204
    assert "Failed to delete the stored file" in caplog.text
    assert stored_path.is_file()
    with test_session_factory() as session:
        assert session.get(KnowledgeBase, knowledge_base_id) is None
        assert session.get(Document, document_id) is None


def test_search_knowledge_base_returns_hydrated_results(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = int(created["id"])
    uploaded = upload_document(client, knowledge_base_id, filename="growth.txt")
    document_id = int(uploaded["id"])
    with test_session_factory() as session:
        content_id, chunk_id = add_document_graph(session, document_id)

    generation_id = "a" * 32
    embedding_model = ApiSearchEmbeddingModel()
    vector_store = ApiSearchVectorStore(
        [
            ChromaSearchHit(
                record_id=f"{generation_id}:{chunk_id}",
                document_id=document_id,
                chunk_id=chunk_id,
                generation_id=generation_id,
                distance=0.125,
            )
        ]
    )
    client.app.dependency_overrides[get_embedding_model] = lambda: embedding_model
    client.app.dependency_overrides[get_search_vector_store_factory] = lambda: (
        lambda: vector_store
    )

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/search",
        json={"query": "  What is growth rate?  "},
    )

    assert response.status_code == 200
    assert response.json() == {
        "query": "What is growth rate?",
        "results": [
            {
                "chunk_id": chunk_id,
                "document_content_id": content_id,
                "document_id": document_id,
                "knowledge_base_id": knowledge_base_id,
                "text": f"Content for document {document_id}",
                "distance": 0.125,
                "original_filename": "growth.txt",
                "content_sequence": 0,
                "chunk_sequence": 0,
                "source_type": "line",
                "source_start": 1,
                "source_end": 1,
                "start_offset": 0,
                "end_offset": len(f"Content for document {document_id}"),
            }
        ],
    }
    assert embedding_model.queries == ["What is growth rate?"]
    assert vector_store.calls == [
        ([1.0, 0.0, 0.0], {f"{generation_id}:{chunk_id}"}, 5)
    ]
    assert {
        "generation_id",
        "record_id",
        "embedding",
        "score",
    }.isdisjoint(response.json()["results"][0])


def test_search_missing_knowledge_base_returns_404_before_external_calls(
    client: TestClient,
) -> None:
    embedding_model = ApiSearchEmbeddingModel()

    def fail_if_chroma_opens() -> ApiSearchVectorStore:
        raise AssertionError("Chroma must not open")

    client.app.dependency_overrides[get_embedding_model] = lambda: embedding_model
    client.app.dependency_overrides[get_search_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )

    response = client.post(
        "/api/knowledge-bases/999/search",
        json={"query": "question"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}
    assert embedding_model.queries == []


def test_search_without_embedded_chunks_returns_409_before_external_calls(
    client: TestClient,
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = int(created["id"])
    upload_document(client, knowledge_base_id, filename="pending.txt")
    embedding_model = ApiSearchEmbeddingModel()

    def fail_if_chroma_opens() -> ApiSearchVectorStore:
        raise AssertionError("Chroma must not open")

    client.app.dependency_overrides[get_embedding_model] = lambda: embedding_model
    client.app.dependency_overrides[get_search_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/search",
        json={"query": "question"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Knowledge base has no searchable embedded chunks"
    }
    assert embedding_model.queries == []


def test_search_returns_200_with_empty_results(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = int(created["id"])
    uploaded = upload_document(client, knowledge_base_id, filename="notes.txt")
    with test_session_factory() as session:
        add_document_graph(session, int(uploaded["id"]))

    vector_store = ApiSearchVectorStore([])
    client.app.dependency_overrides[get_embedding_model] = (
        lambda: ApiSearchEmbeddingModel()
    )
    client.app.dependency_overrides[get_search_vector_store_factory] = lambda: (
        lambda: vector_store
    )

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/search",
        json={"query": "no match", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.json() == {"query": "no match", "results": []}
    assert vector_store.calls[0][2] == 3


def test_search_converts_dashscope_failure_to_502(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = int(created["id"])
    uploaded = upload_document(client, knowledge_base_id, filename="notes.txt")
    with test_session_factory() as session:
        add_document_graph(session, int(uploaded["id"]))

    embedding_model = ApiSearchEmbeddingModel(
        error=DashScopeEmbeddingError("provider-secret-body sk-secret-test")
    )
    client.app.dependency_overrides[get_embedding_model] = lambda: embedding_model

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/search",
        json={"query": "question"},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "Query embedding could not be generated"}
    assert "provider-secret-body" not in response.text
    assert "sk-secret-test" not in response.text


def test_search_converts_chroma_failure_to_503(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = int(created["id"])
    uploaded = upload_document(client, knowledge_base_id, filename="notes.txt")
    with test_session_factory() as session:
        add_document_graph(session, int(uploaded["id"]))

    vector_store = ApiSearchVectorStore(
        [],
        error=ChromaVectorStoreError("raw Chroma error /private/path"),
    )
    client.app.dependency_overrides[get_embedding_model] = (
        lambda: ApiSearchEmbeddingModel()
    )
    client.app.dependency_overrides[get_search_vector_store_factory] = lambda: (
        lambda: vector_store
    )

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/search",
        json={"query": "question"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Knowledge base search is temporarily unavailable"
    }
    assert "/private/path" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": "question", "top_k": 0},
        {"query": "question", "top_k": 21},
        {"query": "question", "top_k": True},
        {"query": "question", "top_k": "5"},
    ],
)
def test_search_rejects_invalid_request_with_422(
    client: TestClient,
    payload: dict[str, object],
) -> None:
    created = create_knowledge_base(client)

    response = client.post(
        f"/api/knowledge-bases/{created['id']}/search",
        json=payload,
    )

    assert response.status_code == 422


def test_context_endpoint_reuses_retrieval_and_returns_assembled_context(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = int(created["id"])
    uploaded = upload_document(client, knowledge_base_id, filename="growth.txt")
    document_id = int(uploaded["id"])
    with test_session_factory() as session:
        content_id, chunk_id = add_document_graph(session, document_id)

    generation_id = "a" * 32
    embedding_model = ApiSearchEmbeddingModel()
    vector_store = ApiSearchVectorStore(
        [
            ChromaSearchHit(
                record_id=f"{generation_id}:{chunk_id}",
                document_id=document_id,
                chunk_id=chunk_id,
                generation_id=generation_id,
                distance=0.125,
            )
        ]
    )
    client.app.dependency_overrides[get_embedding_model] = lambda: embedding_model
    client.app.dependency_overrides[get_search_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    text = f"Content for document {document_id}"
    expected_context = f"[1] growth.txt | line 1\n\n{text}"

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/context",
        json={
            "query": "  What is growth rate?  ",
            "top_k": 2,
            "max_context_characters": 6000,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "query": "What is growth rate?",
        "context": expected_context,
        "blocks": [
            {
                "citation_number": 1,
                "header": "[1] growth.txt | line 1",
                "text": text,
            }
        ],
        "citations": [
            {
                "citation_number": 1,
                "chunk_id": chunk_id,
                "document_content_id": content_id,
                "document_id": document_id,
                "knowledge_base_id": knowledge_base_id,
                "original_filename": "growth.txt",
                "content_sequence": 0,
                "chunk_sequence": 0,
                "source_type": "line",
                "source_start": 1,
                "source_end": 1,
                "start_offset": 0,
                "end_offset": len(text),
                "distance": 0.125,
            }
        ],
        "used_characters": len(expected_context),
        "truncated": False,
    }
    assert embedding_model.queries == ["What is growth rate?"]
    assert vector_store.calls == [
        ([1.0, 0.0, 0.0], {f"{generation_id}:{chunk_id}"}, 2)
    ]


def test_context_endpoint_returns_whole_block_or_nothing_for_small_budget(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = int(created["id"])
    uploaded = upload_document(client, knowledge_base_id, filename="notes.txt")
    document_id = int(uploaded["id"])
    with test_session_factory() as session:
        _, chunk_id = add_document_graph(session, document_id)

    generation_id = "a" * 32
    vector_store = ApiSearchVectorStore(
        [
            ChromaSearchHit(
                record_id=f"{generation_id}:{chunk_id}",
                document_id=document_id,
                chunk_id=chunk_id,
                generation_id=generation_id,
                distance=0.25,
            )
        ]
    )
    client.app.dependency_overrides[get_embedding_model] = (
        lambda: ApiSearchEmbeddingModel()
    )
    client.app.dependency_overrides[get_search_vector_store_factory] = lambda: (
        lambda: vector_store
    )

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/context",
        json={"query": "question", "max_context_characters": 1},
    )

    assert response.status_code == 200
    assert response.json() == {
        "query": "question",
        "context": "",
        "blocks": [],
        "citations": [],
        "used_characters": 0,
        "truncated": True,
    }


def test_context_missing_knowledge_base_returns_404_before_external_calls(
    client: TestClient,
) -> None:
    embedding_model = ApiSearchEmbeddingModel()

    def fail_if_chroma_opens() -> ApiSearchVectorStore:
        raise AssertionError("Chroma must not open")

    client.app.dependency_overrides[get_embedding_model] = lambda: embedding_model
    client.app.dependency_overrides[get_search_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )

    response = client.post(
        "/api/knowledge-bases/999/context",
        json={"query": "question"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}
    assert embedding_model.queries == []


def test_context_without_embedded_chunks_returns_409_before_external_calls(
    client: TestClient,
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = int(created["id"])
    embedding_model = ApiSearchEmbeddingModel()

    def fail_if_chroma_opens() -> ApiSearchVectorStore:
        raise AssertionError("Chroma must not open")

    client.app.dependency_overrides[get_embedding_model] = lambda: embedding_model
    client.app.dependency_overrides[get_search_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/context",
        json={"query": "question"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Knowledge base has no searchable embedded chunks"
    }
    assert embedding_model.queries == []


@pytest.mark.parametrize("invalid_budget", [0, -1, True, 1.5, "6000"])
def test_context_rejects_invalid_character_budget_with_422(
    client: TestClient,
    invalid_budget: object,
) -> None:
    created = create_knowledge_base(client)

    response = client.post(
        f"/api/knowledge-bases/{created['id']}/context",
        json={
            "query": "question",
            "max_context_characters": invalid_budget,
        },
    )

    assert response.status_code == 422
