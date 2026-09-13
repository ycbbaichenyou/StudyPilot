from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import chromadb
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.embeddings import (
    DashScopeEmbeddingError,
    DashScopeTextEmbedding,
    get_embedding_model,
)
from app.embeddings.dashscope import DEFAULT_DIMENSION, DEFAULT_MODEL
from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    DocumentStatus,
    KnowledgeBase,
)
from app.stores import (
    ChromaGenerationSummary,
    ChromaRecord,
    ChromaVectorStoreError,
    build_collection_name,
    get_vector_store_factory,
)
from app.stores.chroma import (
    DISTANCE_SPACE,
    EMBEDDING_PROVIDER,
    VECTOR_SCHEMA_VERSION,
)


class ApiEmbeddingModel:
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0, 2.0] for text in texts]


class ApiFailingEmbeddingModel:
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        raise DashScopeEmbeddingError("DashScope embedding request failed")


class ApiVectorStore:
    def __init__(self) -> None:
        self.records: list[ChromaRecord] = []

    def add_records(self, records: list[ChromaRecord]) -> None:
        self.records.extend(records)

    def get_generation_summary(
        self,
        generation_id: str,
    ) -> ChromaGenerationSummary:
        records = [
            record
            for record in self.records
            if record.metadata["generation_id"] == generation_id
        ]
        return ChromaGenerationSummary(
            record_count=len(records),
            chunk_ids=frozenset(int(record.metadata["chunk_id"]) for record in records),
        )

    def delete_generation(self, generation_id: str) -> None:
        self.records = [
            record
            for record in self.records
            if record.metadata["generation_id"] != generation_id
        ]


def create_document(
    session: Session,
    *,
    status: str = DocumentStatus.PARSED.value,
    add_chunk: bool = True,
) -> Document:
    document = Document(
        knowledge_base=KnowledgeBase(name="Computer Science"),
        filename="stored.txt",
        original_filename="notes.txt",
        file_type="txt",
        file_size=5,
        status=status,
    )
    content = DocumentContent(
        document=document,
        sequence=0,
        text="First",
        source_type="line",
        source_start=1,
        source_end=1,
    )
    if add_chunk:
        content.chunks.append(
            Chunk(sequence=0, text="First", start_offset=0, end_offset=5)
        )
    session.add(document)
    session.commit()
    return document


def test_get_embedding_returns_status_without_triggering_embedding(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        document = create_document(session, status=DocumentStatus.PENDING.value)
        document_id = document.id

    response = client.get(f"/api/documents/{document_id}/embedding")

    assert response.status_code == 200
    assert response.json() == {
        "document_id": document_id,
        "embedding_status": DocumentEmbeddingStatus.PENDING.value,
        "embedding_error": None,
        "embedded_at": None,
        "generation_id": None,
    }


def test_post_embedding_builds_and_reports_active_generation(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = ApiVectorStore()
    client.app.dependency_overrides[get_embedding_model] = ApiEmbeddingModel
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    with test_session_factory() as session:
        document = create_document(session)
        document_id = document.id

    response = client.post(f"/api/documents/{document_id}/embedding")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == document_id
    assert body["embedding_status"] == DocumentEmbeddingStatus.EMBEDDED.value
    assert body["embedding_error"] is None
    assert body["generation_id"] is not None
    embedded_at = datetime.fromisoformat(body["embedded_at"].replace("Z", "+00:00"))
    assert embedded_at.utcoffset() is not None
    assert len(vector_store.records) == 1
    assert vector_store.records[0].metadata["generation_id"] == body["generation_id"]

    get_response = client.get(f"/api/documents/{document_id}/embedding")
    assert get_response.json() == body


def test_post_embedding_reports_provider_failure_without_active_generation(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = ApiVectorStore()
    client.app.dependency_overrides[get_embedding_model] = ApiFailingEmbeddingModel
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    with test_session_factory() as session:
        document = create_document(session)
        document_id = document.id

    response = client.post(f"/api/documents/{document_id}/embedding")

    assert response.status_code == 200
    assert response.json() == {
        "document_id": document_id,
        "embedding_status": DocumentEmbeddingStatus.EMBEDDING_FAILED.value,
        "embedding_error": "DashScope embedding request failed",
        "embedded_at": None,
        "generation_id": None,
    }
    assert vector_store.records == []


def test_post_embedding_never_exposes_provider_body_key_or_internal_path(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    def opener(*_: object, **__: object) -> BytesIO:
        raise HTTPError(
            "https://dashscope.example.test",
            500,
            "provider error",
            None,
            BytesIO(b"provider-secret-body sk-secret-test /private/internal/path"),
        )

    adapter = DashScopeTextEmbedding(api_key="sk-secret-test", opener=opener)
    vector_store = ApiVectorStore()
    client.app.dependency_overrides[get_embedding_model] = lambda: adapter
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    with test_session_factory() as session:
        document = create_document(session)
        document_id = document.id

    response = client.post(f"/api/documents/{document_id}/embedding")

    assert response.status_code == 200
    assert response.json()["embedding_error"] == (
        "DashScope embedding request failed with HTTP 500"
    )
    assert "provider-secret-body" not in response.text
    assert "sk-secret-test" not in response.text
    assert "/private/internal/path" not in response.text


def test_embedding_endpoints_return_404_for_missing_document(
    client: TestClient,
) -> None:
    def fail_if_chroma_opens() -> ApiVectorStore:
        raise AssertionError("Chroma must not open before the document lookup")

    client.app.dependency_overrides[get_embedding_model] = ApiEmbeddingModel
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )

    assert client.get("/api/documents/999/embedding").status_code == 404
    assert client.post("/api/documents/999/embedding").status_code == 404


@pytest.mark.parametrize(
    ("document_status", "add_chunk", "detail"),
    [
        (
            DocumentStatus.PENDING.value,
            True,
            "Document must be parsed before embeddings can be built",
        ),
        (
            DocumentStatus.PARSED.value,
            False,
            "Document must have chunks before embeddings can be built",
        ),
    ],
)
def test_post_embedding_returns_409_before_opening_chroma(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
    document_status: str,
    add_chunk: bool,
    detail: str,
) -> None:
    def fail_if_chroma_opens() -> ApiVectorStore:
        raise AssertionError("Chroma must not open before precondition checks")

    client.app.dependency_overrides[get_embedding_model] = ApiEmbeddingModel
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_if_chroma_opens
    )
    with test_session_factory() as session:
        document = create_document(
            session,
            status=document_status,
            add_chunk=add_chunk,
        )
        document_id = document.id

    response = client.post(f"/api/documents/{document_id}/embedding")

    assert response.status_code == 409
    assert response.json() == {"detail": detail}


def test_chroma_initialization_failure_is_persisted_as_embedding_failed(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    def fail_to_open_chroma() -> ApiVectorStore:
        raise ChromaVectorStoreError(
            "raw Chroma failure /private/chroma/path sk-secret-test"
        )

    client.app.dependency_overrides[get_embedding_model] = ApiEmbeddingModel
    client.app.dependency_overrides[get_vector_store_factory] = lambda: (
        fail_to_open_chroma
    )
    with test_session_factory() as session:
        document_id = create_document(session).id

    response = client.post(f"/api/documents/{document_id}/embedding")

    assert response.status_code == 200
    assert response.json()["embedding_status"] == (
        DocumentEmbeddingStatus.EMBEDDING_FAILED.value
    )
    assert response.json()["embedding_error"] == (
        "Document embeddings could not be saved"
    )
    assert "/private/chroma/path" not in response.text
    assert "sk-secret-test" not in response.text
    assert "raw Chroma failure" not in response.text
    with test_session_factory() as session:
        stored_document = session.get(Document, document_id)
        assert stored_document is not None
        assert stored_document.embedding_status == (
            DocumentEmbeddingStatus.EMBEDDING_FAILED.value
        )
        assert stored_document.embedding_error == (
            "Document embeddings could not be saved"
        )


@pytest.mark.parametrize("mismatch", ["distance", "metadata"])
def test_existing_incompatible_collection_is_persisted_as_embedding_failed(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    chroma_path = tmp_path / mismatch
    monkeypatch.setenv("STUDYPILOT_CHROMA_PATH", str(chroma_path))
    collection_name = build_collection_name(
        provider=EMBEDDING_PROVIDER,
        model=DEFAULT_MODEL,
        dimensions=DEFAULT_DIMENSION,
        schema_version=VECTOR_SCHEMA_VERSION,
    )
    metadata: dict[str, str | int] = {
        "provider": EMBEDDING_PROVIDER,
        "model": DEFAULT_MODEL,
        "dimensions": DEFAULT_DIMENSION,
        "schema_version": VECTOR_SCHEMA_VERSION,
        "distance": DISTANCE_SPACE,
    }
    configuration = {"hnsw": {"space": DISTANCE_SPACE}}
    if mismatch == "distance":
        configuration = {"hnsw": {"space": "l2"}}
    else:
        metadata["model"] = "mismatched-model"
    chromadb.PersistentClient(path=str(chroma_path)).create_collection(
        name=collection_name,
        configuration=configuration,
        metadata=metadata,
        embedding_function=None,
    )

    client.app.dependency_overrides[get_embedding_model] = ApiEmbeddingModel
    with test_session_factory() as session:
        document_id = create_document(session).id

    response = client.post(f"/api/documents/{document_id}/embedding")

    assert response.status_code == 200
    assert response.json()["embedding_status"] == (
        DocumentEmbeddingStatus.EMBEDDING_FAILED.value
    )
    assert response.json()["embedding_error"] == (
        "Document embeddings could not be saved"
    )
    assert str(chroma_path) not in response.text
    assert "mismatched-model" not in response.text
    with test_session_factory() as session:
        stored_document = session.get(Document, document_id)
        assert stored_document is not None
        assert stored_document.embedding_status == (
            DocumentEmbeddingStatus.EMBEDDING_FAILED.value
        )


def test_get_embedding_does_not_resolve_unavailable_embedding_dependencies(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    def fail_dependency_resolution() -> object:
        raise AssertionError("GET must not resolve model or Chroma dependencies")

    client.app.dependency_overrides[get_embedding_model] = fail_dependency_resolution
    client.app.dependency_overrides[get_vector_store_factory] = (
        fail_dependency_resolution
    )
    with test_session_factory() as session:
        document_id = create_document(
            session,
            status=DocumentStatus.PENDING.value,
        ).id

    response = client.get(f"/api/documents/{document_id}/embedding")

    assert response.status_code == 200
    assert response.json()["embedding_status"] == (
        DocumentEmbeddingStatus.PENDING.value
    )
