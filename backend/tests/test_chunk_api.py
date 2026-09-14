from datetime import datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    DocumentStatus,
)
from app.services import document_chunking
from app.services.embedding_cleanup import SAFE_EMBEDDING_CLEANUP_ERROR
from app.stores import get_vector_store_factory


class ApiCleanupVectorStore:
    def __init__(self, *, failures_remaining: int = 0) -> None:
        self.failures_remaining = failures_remaining
        self.deleted_document_ids: list[int] = []

    def delete_document_records(self, document_id: int) -> None:
        self.deleted_document_ids.append(document_id)
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError(
                "raw Chroma failure /private/chroma/path sk-secret-test"
            )


def upload_document(client: TestClient) -> dict[str, object]:
    knowledge_base_response = client.post(
        "/api/knowledge-bases",
        json={"name": "Computer Science", "description": None},
    )
    assert knowledge_base_response.status_code == 201
    knowledge_base_id = knowledge_base_response.json()["id"]

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("notes.txt", BytesIO(b"source"), "text/plain")},
    )
    assert response.status_code == 201
    body = response.json()
    assert isinstance(body, dict)
    return body


def add_parsed_contents(
    session: Session,
    document_id: int,
) -> list[DocumentContent]:
    document = session.get(Document, document_id)
    assert document is not None
    document.status = DocumentStatus.PARSED.value
    contents = [
        DocumentContent(
            document_id=document_id,
            sequence=1,
            text="第二行",
            source_type="line",
            source_start=2,
            source_end=2,
        ),
        DocumentContent(
            document_id=document_id,
            sequence=0,
            text="第一行",
            source_type="line",
            source_start=1,
            source_end=1,
        ),
    ]
    session.add_all(contents)
    session.commit()
    return contents


def mark_document_embedded(
    session: Session,
    document_id: int,
    contents: list[DocumentContent],
) -> None:
    document = session.get(Document, document_id)
    assert document is not None
    document.embedding_status = DocumentEmbeddingStatus.EMBEDDED.value
    document.embedding_generation_id = "a" * 32
    document.embedded_at = datetime(2026, 9, 13, 12, 0)
    session.add(
        Chunk(
            document_content=contents[0],
            sequence=0,
            text="Old chunk",
            start_offset=0,
            end_offset=3,
        )
    )
    session.commit()


def test_post_chunks_builds_chunks_from_parsed_contents(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    def fail_if_chroma_opens() -> ApiCleanupVectorStore:
        raise AssertionError("Chroma must not open without an old embedding")

    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )
    uploaded = upload_document(client)
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    with test_session_factory() as session:
        contents = add_parsed_contents(session, document_id)
        content_ids = {content.sequence: content.id for content in contents}

    response = client.post(f"/api/documents/{document_id}/chunks")

    assert response.status_code == 200
    assert response.json() == {
        "document_id": document_id,
        "status": DocumentStatus.PARSED.value,
        "chunks": [
            {
                "id": 1,
                "document_content_id": content_ids[0],
                "content_sequence": 0,
                "sequence": 0,
                "text": "第一行",
                "start_offset": 0,
                "end_offset": 3,
                "source_type": "line",
                "source_start": 1,
                "source_end": 1,
            },
            {
                "id": 2,
                "document_content_id": content_ids[1],
                "content_sequence": 1,
                "sequence": 0,
                "text": "第二行",
                "start_offset": 0,
                "end_offset": 3,
                "source_type": "line",
                "source_start": 2,
                "source_end": 2,
            },
        ],
    }
    with test_session_factory() as session:
        assert [chunk.text for chunk in session.scalars(select(Chunk)).all()] == [
            "第一行",
            "第二行",
        ]
        stored_document = session.get(Document, document_id)
        assert stored_document is not None
        assert stored_document.embedding_status == (
            DocumentEmbeddingStatus.PENDING.value
        )


def test_post_chunks_returns_503_but_keeps_chunks_and_stale_on_cleanup_failure(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = ApiCleanupVectorStore(failures_remaining=1)
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    uploaded = upload_document(client)
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    with test_session_factory() as session:
        contents = add_parsed_contents(session, document_id)
        mark_document_embedded(session, document_id, contents)

    response = client.post(f"/api/documents/{document_id}/chunks")

    assert response.status_code == 503
    assert response.json() == {"detail": SAFE_EMBEDDING_CLEANUP_ERROR}
    assert "raw Chroma failure" not in response.text
    assert "/private/chroma/path" not in response.text
    assert "sk-secret-test" not in response.text
    with test_session_factory() as session:
        stored_document = session.get(Document, document_id)
        assert stored_document is not None
        assert stored_document.embedding_status == DocumentEmbeddingStatus.STALE.value
        assert stored_document.embedding_generation_id is None
        assert stored_document.embedded_at is None
        assert stored_document.embedding_error == SAFE_EMBEDDING_CLEANUP_ERROR
        assert [chunk.text for chunk in session.scalars(select(Chunk)).all()] == [
            "第一行",
            "第二行",
        ]
    assert vector_store.deleted_document_ids == [document_id]


def test_post_chunks_retries_stale_cleanup_and_returns_to_pending(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = ApiCleanupVectorStore(failures_remaining=1)
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    uploaded = upload_document(client)
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    with test_session_factory() as session:
        contents = add_parsed_contents(session, document_id)
        mark_document_embedded(session, document_id, contents)

    first_response = client.post(f"/api/documents/{document_id}/chunks")
    second_response = client.post(f"/api/documents/{document_id}/chunks")

    assert first_response.status_code == 503
    assert second_response.status_code == 200
    with test_session_factory() as session:
        stored_document = session.get(Document, document_id)
        assert stored_document is not None
        assert stored_document.embedding_status == (
            DocumentEmbeddingStatus.PENDING.value
        )
        assert stored_document.embedding_generation_id is None
        assert stored_document.embedded_at is None
        assert stored_document.embedding_error is None
        assert [chunk.text for chunk in session.scalars(select(Chunk)).all()] == [
            "第一行",
            "第二行",
        ]
    assert vector_store.deleted_document_ids == [document_id, document_id]


def test_post_chunks_returns_404_for_missing_document(client: TestClient) -> None:
    def fail_if_chroma_opens() -> ApiCleanupVectorStore:
        raise AssertionError("Chroma must not open before the document lookup")

    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )
    response = client.post("/api/documents/999/chunks")

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


def test_post_chunks_returns_409_when_document_is_not_parsed(
    client: TestClient,
) -> None:
    def fail_if_chroma_opens() -> ApiCleanupVectorStore:
        raise AssertionError("Chroma must not open before precondition checks")

    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )
    uploaded = upload_document(client)

    response = client.post(f"/api/documents/{uploaded['id']}/chunks")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Document must be parsed before chunks can be built"
    }


def test_get_chunks_returns_empty_list_without_building(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploaded = upload_document(client)

    def fail_if_called(*_: object, **__: object) -> None:
        raise AssertionError("GET must not rebuild chunks")

    monkeypatch.setattr(
        document_chunking,
        "rebuild_document_chunks",
        fail_if_called,
    )

    response = client.get(f"/api/documents/{uploaded['id']}/chunks")

    assert response.status_code == 200
    assert response.json() == {
        "document_id": uploaded["id"],
        "status": DocumentStatus.PENDING.value,
        "chunks": [],
    }


def test_get_chunks_returns_existing_chunks_in_source_order_for_parse_failure(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    uploaded = upload_document(client)
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    with test_session_factory() as session:
        contents = add_parsed_contents(session, document_id)
        contents_by_sequence = {content.sequence: content for content in contents}
        document = session.get(Document, document_id)
        assert document is not None
        document.status = DocumentStatus.PARSE_FAILED.value
        session.add_all(
            [
                Chunk(
                    document_content=contents_by_sequence[1],
                    sequence=0,
                    text="第二行",
                    start_offset=0,
                    end_offset=3,
                ),
                Chunk(
                    document_content=contents_by_sequence[0],
                    sequence=0,
                    text="第一行",
                    start_offset=0,
                    end_offset=3,
                ),
            ]
        )
        session.commit()

    response = client.get(f"/api/documents/{document_id}/chunks")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == document_id
    assert body["status"] == DocumentStatus.PARSE_FAILED.value
    assert [chunk["text"] for chunk in body["chunks"]] == ["第一行", "第二行"]
    assert [chunk["content_sequence"] for chunk in body["chunks"]] == [0, 1]


def test_get_chunks_returns_404_for_missing_document(client: TestClient) -> None:
    response = client.get("/api/documents/999/chunks")

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}
