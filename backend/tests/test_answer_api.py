from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.embeddings import DashScopeEmbeddingError, get_embedding_model
from app.llm import DashScopeLLMError, LLMMessage, get_llm_model
from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    DocumentStatus,
    KnowledgeBase,
)
from app.stores import (
    ChromaSearchHit,
    ChromaVectorStoreError,
    get_search_vector_store_factory,
)


class ApiEmbeddingModel:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.queries: list[str] = []

    def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return [1.0, 0.0, 0.0]


class ApiVectorStore:
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


class ApiLLM:
    def __init__(
        self,
        answer: str = "增长率表示相对变化。[1]",
        *,
        error: Exception | None = None,
    ) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[tuple[LLMMessage, ...]] = []

    def generate(self, messages: Sequence[LLMMessage]) -> str:
        self.calls.append(tuple(messages))
        if self.error is not None:
            raise self.error
        return self.answer


@dataclass(frozen=True, slots=True)
class SearchableGraph:
    knowledge_base_id: int
    document_id: int
    document_content_id: int
    chunk_id: int
    generation_id: str
    text: str

    def hit(self, *, distance: float = 0.125) -> ChromaSearchHit:
        return ChromaSearchHit(
            record_id=f"{self.generation_id}:{self.chunk_id}",
            document_id=self.document_id,
            chunk_id=self.chunk_id,
            generation_id=self.generation_id,
            distance=distance,
        )


def create_knowledge_base(client: TestClient) -> int:
    response = client.post(
        "/api/knowledge-bases",
        json={"name": "Economics"},
    )
    assert response.status_code == 201
    return int(response.json()["id"])


def add_searchable_graph(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> SearchableGraph:
    knowledge_base_id = create_knowledge_base(client)
    generation_id = "a" * 32
    text = "增长率表示相对变化。"
    with test_session_factory() as session:
        knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
        assert knowledge_base is not None
        document = Document(
            knowledge_base=knowledge_base,
            filename="stored-growth.txt",
            original_filename="growth.txt",
            file_type="txt",
            file_size=len(text.encode()),
            status=DocumentStatus.PARSED.value,
            embedding_status=DocumentEmbeddingStatus.EMBEDDED.value,
            embedding_generation_id=generation_id,
        )
        content = DocumentContent(
            document=document,
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
        session.add(document)
        session.commit()
        return SearchableGraph(
            knowledge_base_id=knowledge_base_id,
            document_id=document.id,
            document_content_id=content.id,
            chunk_id=chunk.id,
            generation_id=generation_id,
            text=text,
        )


def override_answer_dependencies(
    client: TestClient,
    *,
    embedding_model: ApiEmbeddingModel,
    vector_store: ApiVectorStore,
    llm_model: ApiLLM,
) -> None:
    client.app.dependency_overrides[get_embedding_model] = lambda: embedding_model
    client.app.dependency_overrides[get_search_vector_store_factory] = lambda: (
        lambda: vector_store
    )
    client.app.dependency_overrides[get_llm_model] = lambda: llm_model


def test_answer_api_returns_answer_and_context_citations(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    graph = add_searchable_graph(client, test_session_factory)
    embedding_model = ApiEmbeddingModel()
    vector_store = ApiVectorStore([graph.hit()])
    llm_model = ApiLLM()
    override_answer_dependencies(
        client,
        embedding_model=embedding_model,
        vector_store=vector_store,
        llm_model=llm_model,
    )
    expected_context = f"[1] growth.txt | line 1\n\n{graph.text}"

    response = client.post(
        f"/api/knowledge-bases/{graph.knowledge_base_id}/answer",
        json={
            "query": "  什么是增长率？  ",
            "top_k": 3,
            "max_context_characters": 6000,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "query": "什么是增长率？",
        "status": "answered",
        "answer": "增长率表示相对变化。[1]",
        "citation_status": "valid",
        "citations": [
            {
                "citation_number": 1,
                "chunk_id": graph.chunk_id,
                "document_content_id": graph.document_content_id,
                "document_id": graph.document_id,
                "knowledge_base_id": graph.knowledge_base_id,
                "original_filename": "growth.txt",
                "content_sequence": 0,
                "chunk_sequence": 0,
                "source_type": "line",
                "source_start": 1,
                "source_end": 1,
                "start_offset": 0,
                "end_offset": len(graph.text),
                "distance": 0.125,
            }
        ],
        "used_context_characters": len(expected_context),
        "context_truncated": False,
    }
    assert embedding_model.queries == ["什么是增长率？"]
    assert vector_store.calls == [
        (
            [1.0, 0.0, 0.0],
            {f"{graph.generation_id}:{graph.chunk_id}"},
            3,
        )
    ]
    assert len(llm_model.calls) == 1
    assert expected_context in llm_model.calls[0][1].content
    assert {
        "context",
        "prompt",
        "messages",
        "record_id",
        "generation_id",
    }.isdisjoint(response.json())


def test_answer_api_returns_insufficient_context_without_calling_llm(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    graph = add_searchable_graph(client, test_session_factory)
    llm_model = ApiLLM()
    override_answer_dependencies(
        client,
        embedding_model=ApiEmbeddingModel(),
        vector_store=ApiVectorStore([]),
        llm_model=llm_model,
    )

    response = client.post(
        f"/api/knowledge-bases/{graph.knowledge_base_id}/answer",
        json={"query": "没有匹配的问题"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "query": "没有匹配的问题",
        "status": "insufficient_context",
        "answer": None,
        "citation_status": "missing",
        "citations": [],
        "used_context_characters": 0,
        "context_truncated": False,
    }
    assert llm_model.calls == []


@pytest.mark.parametrize(
    ("answer", "expected_status", "expected_citation_count"),
    [
        ("有效来源 [1]，不存在的来源 [9]。", "invalid_reference", 1),
        ("回答没有任何引用。", "missing", 0),
    ],
)
def test_answer_api_reports_model_citation_integrity(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
    answer: str,
    expected_status: str,
    expected_citation_count: int,
) -> None:
    graph = add_searchable_graph(client, test_session_factory)
    llm_model = ApiLLM(answer)
    override_answer_dependencies(
        client,
        embedding_model=ApiEmbeddingModel(),
        vector_store=ApiVectorStore([graph.hit()]),
        llm_model=llm_model,
    )

    response = client.post(
        f"/api/knowledge-bases/{graph.knowledge_base_id}/answer",
        json={"query": "什么是增长率？"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == answer
    assert body["citation_status"] == expected_status
    assert len(body["citations"]) == expected_citation_count
    if expected_citation_count:
        assert body["citations"][0]["citation_number"] == 1
    assert len(llm_model.calls) == 1


def test_answer_api_returns_404_without_calling_external_services(
    client: TestClient,
) -> None:
    embedding_model = ApiEmbeddingModel()
    llm_model = ApiLLM()
    override_answer_dependencies(
        client,
        embedding_model=embedding_model,
        vector_store=ApiVectorStore([]),
        llm_model=llm_model,
    )

    response = client.post(
        "/api/knowledge-bases/999/answer",
        json={"query": "问题"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}
    assert embedding_model.queries == []
    assert llm_model.calls == []


def test_answer_api_returns_409_without_calling_external_services(
    client: TestClient,
) -> None:
    knowledge_base_id = create_knowledge_base(client)
    embedding_model = ApiEmbeddingModel()
    llm_model = ApiLLM()
    override_answer_dependencies(
        client,
        embedding_model=embedding_model,
        vector_store=ApiVectorStore([]),
        llm_model=llm_model,
    )

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/answer",
        json={"query": "问题"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Knowledge base has no searchable embedded chunks"
    }
    assert embedding_model.queries == []
    assert llm_model.calls == []


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
        {"query": "question", "max_context_characters": 0},
        {"query": "question", "max_context_characters": True},
        {"query": "question", "max_context_characters": "6000"},
    ],
)
def test_answer_api_rejects_invalid_requests_with_422(
    client: TestClient,
    payload: dict[str, object],
) -> None:
    knowledge_base_id = create_knowledge_base(client)

    response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/answer",
        json=payload,
    )

    assert response.status_code == 422


def test_answer_api_converts_embedding_error_to_safe_502(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    graph = add_searchable_graph(client, test_session_factory)
    llm_model = ApiLLM()
    override_answer_dependencies(
        client,
        embedding_model=ApiEmbeddingModel(
            error=DashScopeEmbeddingError(
                "provider-secret-body /private/path sk-secret-test"
            )
        ),
        vector_store=ApiVectorStore([]),
        llm_model=llm_model,
    )

    response = client.post(
        f"/api/knowledge-bases/{graph.knowledge_base_id}/answer",
        json={"query": "问题"},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "Query embedding could not be generated"}
    assert "provider-secret" not in response.text
    assert "sk-secret-test" not in response.text
    assert "/private/path" not in response.text
    assert llm_model.calls == []


def test_answer_api_converts_chroma_error_to_safe_503(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    graph = add_searchable_graph(client, test_session_factory)
    llm_model = ApiLLM()
    override_answer_dependencies(
        client,
        embedding_model=ApiEmbeddingModel(),
        vector_store=ApiVectorStore(
            [],
            error=ChromaVectorStoreError(
                "provider-secret-body /private/path"
            ),
        ),
        llm_model=llm_model,
    )

    response = client.post(
        f"/api/knowledge-bases/{graph.knowledge_base_id}/answer",
        json={"query": "问题"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Knowledge base search is temporarily unavailable"
    }
    assert "provider-secret" not in response.text
    assert "/private/path" not in response.text
    assert llm_model.calls == []


def test_answer_api_converts_llm_error_to_safe_502(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    graph = add_searchable_graph(client, test_session_factory)
    llm_model = ApiLLM(
        error=DashScopeLLMError(
            "provider-secret-body /private/path sk-secret-test"
        )
    )
    override_answer_dependencies(
        client,
        embedding_model=ApiEmbeddingModel(),
        vector_store=ApiVectorStore([graph.hit()]),
        llm_model=llm_model,
    )

    response = client.post(
        f"/api/knowledge-bases/{graph.knowledge_base_id}/answer",
        json={"query": "问题"},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "Answer could not be generated"}
    assert "provider-secret" not in response.text
    assert "sk-secret-test" not in response.text
    assert "/private/path" not in response.text
    assert len(llm_model.calls) == 1
