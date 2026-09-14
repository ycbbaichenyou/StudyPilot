from collections.abc import Callable
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.document_processing.parsers import PARSERS_BY_FILE_TYPE
from app.document_processing.schemas import ParsedTextUnit
from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    DocumentStatus,
)
from app.services.embedding_cleanup import SAFE_EMBEDDING_CLEANUP_ERROR
from app.stores import get_vector_store_factory


class ApiParsingCleanupVectorStore:
    def __init__(
        self,
        *,
        on_delete: Callable[[int], None] | None = None,
        fail: bool = False,
    ) -> None:
        self.on_delete = on_delete
        self.fail = fail
        self.deleted_document_ids: list[int] = []

    def delete_document_records(self, document_id: int) -> None:
        self.deleted_document_ids.append(document_id)
        if self.on_delete is not None:
            self.on_delete(document_id)
        if self.fail:
            raise RuntimeError(
                "raw Chroma failure /private/chroma/path sk-secret-test"
            )


def create_knowledge_base(client: TestClient) -> int:
    response = client.post(
        "/api/knowledge-bases",
        json={"name": "Computer Science", "description": None},
    )
    assert response.status_code == 201
    knowledge_base_id = response.json()["id"]
    assert isinstance(knowledge_base_id, int)
    return knowledge_base_id


def upload_text_document(
    client: TestClient,
    *,
    content: bytes = b"First line\nSecond line",
) -> dict[str, object]:
    knowledge_base_id = create_knowledge_base(client)
    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("notes.txt", BytesIO(content), "text/plain")},
    )
    assert response.status_code == 201
    body = response.json()
    assert isinstance(body, dict)
    return body


def add_existing_embedded_parse(
    session: Session,
    document_id: int,
    *,
    text: str = "Last complete parse",
) -> tuple[int, int, datetime]:
    document = session.get(Document, document_id)
    assert document is not None
    embedded_at = datetime(2026, 9, 13, 12, 0)
    document.status = DocumentStatus.PARSED.value
    document.embedding_status = DocumentEmbeddingStatus.EMBEDDED.value
    document.embedding_generation_id = "a" * 32
    document.embedded_at = embedded_at
    old_content = DocumentContent(
        document_id=document_id,
        sequence=0,
        text=text,
        source_type="line",
        source_start=1,
        source_end=1,
    )
    old_chunk = Chunk(
        document_content=old_content,
        sequence=0,
        text=text,
        start_offset=0,
        end_offset=len(text),
    )
    session.add(old_content)
    session.commit()
    return old_content.id, old_chunk.id, embedded_at


def test_parse_api_transitions_from_pending_through_parsing_to_parsed(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_chroma_opens() -> ApiParsingCleanupVectorStore:
        raise AssertionError("Chroma must not open without an old embedding")

    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )
    uploaded = upload_text_document(client)
    document_id = uploaded["id"]
    assert uploaded["status"] == DocumentStatus.PENDING.value
    observed_statuses: list[str] = []

    def observe_parsing_status(_: Path) -> list[ParsedTextUnit]:
        with test_session_factory() as observer_session:
            document = observer_session.get(Document, document_id)
            assert document is not None
            observed_statuses.append(document.status)
        return [
            ParsedTextUnit(
                text="Parsed text",
                sequence=0,
                source_type="line",
                source_start=1,
                source_end=1,
            )
        ]

    monkeypatch.setitem(PARSERS_BY_FILE_TYPE, "txt", observe_parsing_status)

    response = client.post(f"/api/documents/{document_id}/parse")

    assert response.status_code == 200
    body = response.json()
    assert observed_statuses == [DocumentStatus.PARSING.value]
    assert body["status"] == DocumentStatus.PARSED.value
    assert body["parse_error"] is None
    parsed_at = datetime.fromisoformat(body["parsed_at"].replace("Z", "+00:00"))
    assert parsed_at.tzinfo == timezone.utc

    with test_session_factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.PARSED.value
        assert document.parsed_at is not None
        assert document.parsed_at.tzinfo is None
        contents = list(
            session.scalars(
                select(DocumentContent)
                .where(DocumentContent.document_id == document_id)
                .order_by(DocumentContent.sequence)
            ).all()
        )
        assert [(content.text, content.sequence) for content in contents] == [
            ("Parsed text", 0)
        ]
        assert document.embedding_status == DocumentEmbeddingStatus.PENDING.value
        assert document.embedding_generation_id is None


def test_successful_reparse_invalidates_cleans_and_allows_new_chunks(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    uploaded = upload_text_document(client, content=b"Fresh first\nFresh second")
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    observed_during_cleanup: list[
        tuple[
            str,
            str | None,
            datetime | None,
            str | None,
            list[str],
            list[str],
        ]
    ] = []

    def observe_committed_stale_state(cleaned_document_id: int) -> None:
        with test_session_factory() as inspection_session:
            stored_document = inspection_session.get(Document, cleaned_document_id)
            assert stored_document is not None
            contents = list(
                inspection_session.scalars(
                    select(DocumentContent)
                    .where(DocumentContent.document_id == cleaned_document_id)
                    .order_by(DocumentContent.sequence)
                ).all()
            )
            chunks = list(inspection_session.scalars(select(Chunk)).all())
            observed_during_cleanup.append(
                (
                    stored_document.embedding_status,
                    stored_document.embedding_generation_id,
                    stored_document.embedded_at,
                    stored_document.embedding_error,
                    [content.text for content in contents],
                    [chunk.text for chunk in chunks],
                )
            )

    vector_store = ApiParsingCleanupVectorStore(
        on_delete=observe_committed_stale_state
    )
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    with test_session_factory() as session:
        add_existing_embedded_parse(session, document_id)

    parse_response = client.post(f"/api/documents/{document_id}/parse")
    chunk_response = client.post(f"/api/documents/{document_id}/chunks")

    assert parse_response.status_code == 200
    assert chunk_response.status_code == 200
    assert [chunk["text"] for chunk in chunk_response.json()["chunks"]] == [
        "Fresh first",
        "Fresh second",
    ]
    assert observed_during_cleanup == [
        (
            DocumentEmbeddingStatus.STALE.value,
            None,
            None,
            None,
            ["Fresh first", "Fresh second"],
            [],
        )
    ]
    assert vector_store.deleted_document_ids == [document_id]
    with test_session_factory() as session:
        stored_document = session.get(Document, document_id)
        assert stored_document is not None
        assert stored_document.embedding_status == (
            DocumentEmbeddingStatus.PENDING.value
        )
        assert stored_document.embedding_generation_id is None
        assert stored_document.embedded_at is None
        assert stored_document.embedding_error is None


def test_parse_api_returns_404_for_missing_document(client: TestClient) -> None:
    def fail_if_chroma_opens() -> ApiParsingCleanupVectorStore:
        raise AssertionError("Chroma must not open before the document lookup")

    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )
    response = client.post("/api/documents/999/parse")

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


def test_parser_failure_returns_parse_failed_without_exposing_exception(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploaded = upload_text_document(client)
    document_id = uploaded["id"]

    def fail_parser(_: Path) -> list[ParsedTextUnit]:
        raise RuntimeError("private implementation detail")

    monkeypatch.setitem(PARSERS_BY_FILE_TYPE, "txt", fail_parser)

    response = client.post(f"/api/documents/{document_id}/parse")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == DocumentStatus.PARSE_FAILED.value
    assert body["parse_error"] == "Document parsing failed (RuntimeError)"
    assert "private implementation detail" not in response.text
    assert body["parsed_at"] is None

    with test_session_factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.PARSE_FAILED.value
        assert document.parse_error == "Document parsing failed (RuntimeError)"
        assert list(
            session.scalars(
                select(DocumentContent).where(
                    DocumentContent.document_id == document_id
                )
            ).all()
        ) == []


def test_parser_failure_preserves_old_contents_chunks_and_embedding(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_chroma_opens() -> ApiParsingCleanupVectorStore:
        raise AssertionError("Chroma must not open when parsing fails")

    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )
    uploaded = upload_text_document(client)
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    with test_session_factory() as session:
        old_content_id, old_chunk_id, old_embedded_at = add_existing_embedded_parse(
            session,
            document_id,
        )

    def fail_parser(_: Path) -> list[ParsedTextUnit]:
        raise RuntimeError("parser failed")

    monkeypatch.setitem(PARSERS_BY_FILE_TYPE, "txt", fail_parser)

    response = client.post(f"/api/documents/{document_id}/parse")

    assert response.status_code == 200
    assert response.json()["status"] == DocumentStatus.PARSE_FAILED.value
    with test_session_factory() as session:
        stored_document = session.get(Document, document_id)
        assert stored_document is not None
        assert stored_document.embedding_status == (
            DocumentEmbeddingStatus.EMBEDDED.value
        )
        assert stored_document.embedding_generation_id == "a" * 32
        assert stored_document.embedded_at == old_embedded_at
        assert stored_document.embedding_error is None
        assert [
            (content.id, content.text)
            for content in session.scalars(select(DocumentContent)).all()
        ] == [(old_content_id, "Last complete parse")]
        assert [
            (chunk.id, chunk.text)
            for chunk in session.scalars(select(Chunk)).all()
        ] == [(old_chunk_id, "Last complete parse")]


def test_reparse_cleanup_failure_returns_503_and_keeps_new_parse_stale(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = ApiParsingCleanupVectorStore(fail=True)
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    uploaded = upload_text_document(client, content=b"Fresh first\nFresh second")
    document_id = uploaded["id"]
    assert isinstance(document_id, int)
    with test_session_factory() as session:
        add_existing_embedded_parse(session, document_id)

    response = client.post(f"/api/documents/{document_id}/parse")

    assert response.status_code == 503
    assert response.json() == {"detail": SAFE_EMBEDDING_CLEANUP_ERROR}
    assert "raw Chroma failure" not in response.text
    assert "/private/chroma/path" not in response.text
    assert "sk-secret-test" not in response.text
    with test_session_factory() as session:
        stored_document = session.get(Document, document_id)
        assert stored_document is not None
        assert stored_document.status == DocumentStatus.PARSED.value
        assert stored_document.embedding_status == DocumentEmbeddingStatus.STALE.value
        assert stored_document.embedding_generation_id is None
        assert stored_document.embedded_at is None
        assert stored_document.embedding_error == SAFE_EMBEDDING_CLEANUP_ERROR
        contents = list(
            session.scalars(
                select(DocumentContent).order_by(DocumentContent.sequence)
            ).all()
        )
        assert [content.text for content in contents] == [
            "Fresh first",
            "Fresh second",
        ]
        assert list(session.scalars(select(Chunk)).all()) == []
    assert vector_store.deleted_document_ids == [document_id]


def test_successful_reparse_replaces_old_content_and_clears_error(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    uploaded = upload_text_document(client, content=b"Fresh first\nFresh second")
    document_id = uploaded["id"]

    with test_session_factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        document.status = DocumentStatus.PARSE_FAILED.value
        document.parse_error = "Old parse error"
        document.parsed_at = datetime(2026, 9, 10, 8, 0)
        old_content = DocumentContent(
            document_id=document_id,
            sequence=0,
            text="Stale content",
            source_type="line",
            source_start=1,
            source_end=1,
        )
        old_content.chunks.append(
            Chunk(
                sequence=0,
                text="Stale content",
                start_offset=0,
                end_offset=len("Stale content"),
            )
        )
        session.add(old_content)
        session.commit()

    response = client.post(f"/api/documents/{document_id}/parse")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == DocumentStatus.PARSED.value
    assert body["parse_error"] is None

    with test_session_factory() as session:
        contents = list(
            session.scalars(
                select(DocumentContent)
                .where(DocumentContent.document_id == document_id)
                .order_by(DocumentContent.sequence)
            ).all()
        )
        assert [content.text for content in contents] == [
            "Fresh first",
            "Fresh second",
        ]
        assert list(session.scalars(select(Chunk)).all()) == []
        document = session.get(Document, document_id)
        assert document is not None
        assert document.parsed_at is not None
        assert document.parsed_at > datetime(2026, 9, 10, 8, 0)


def test_content_transaction_failure_rolls_back_partial_replacement(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_chroma_opens() -> ApiParsingCleanupVectorStore:
        raise AssertionError("Chroma must not open when the SQLite commit fails")

    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )
    uploaded = upload_text_document(client)
    document_id = uploaded["id"]
    assert isinstance(document_id, int)

    with test_session_factory() as session:
        old_content_id, old_chunk_id, old_embedded_at = (
            add_existing_embedded_parse(
                session,
                document_id,
            )
        )

    def duplicate_sequences(_: Path) -> list[ParsedTextUnit]:
        return [
            ParsedTextUnit("Partial first", 0, "line", 1, 1),
            ParsedTextUnit("Partial second", 0, "line", 2, 2),
        ]

    monkeypatch.setitem(PARSERS_BY_FILE_TYPE, "txt", duplicate_sequences)

    response = client.post(f"/api/documents/{document_id}/parse")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == DocumentStatus.PARSE_FAILED.value
    assert body["parse_error"] == "Parsed document content could not be saved"

    with test_session_factory() as session:
        contents = list(
            session.scalars(
                select(DocumentContent).where(
                    DocumentContent.document_id == document_id
                )
            ).all()
        )
        assert [(content.id, content.text) for content in contents] == [
            (old_content_id, "Last complete parse")
        ]
        assert [
            (chunk.id, chunk.text) for chunk in session.scalars(select(Chunk)).all()
        ] == [(old_chunk_id, "Last complete parse")]
        document = session.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.PARSE_FAILED.value
        assert document.embedding_status == DocumentEmbeddingStatus.EMBEDDED.value
        assert document.embedding_generation_id == "a" * 32
        assert document.embedded_at == old_embedded_at
        assert document.embedding_error is None
