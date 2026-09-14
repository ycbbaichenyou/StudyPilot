from __future__ import annotations

from collections.abc import Callable, Collection

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    DocumentStatus,
    KnowledgeBase,
)
from app.services.retrieval import (
    KnowledgeBaseNotFoundError,
    KnowledgeBaseNotSearchableError,
    RetrievalResult,
    search_knowledge_base,
)
from app.stores import ChromaSearchHit


class RecordingQueryEmbeddingModel:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        return [1.0, 0.0, 0.0]


class RecordingVectorStore:
    def __init__(
        self,
        hits: list[ChromaSearchHit],
        *,
        on_search: Callable[[], None] | None = None,
    ) -> None:
        self.hits = hits
        self.on_search = on_search
        self.calls: list[tuple[list[float], set[str], int]] = []

    def search(
        self,
        query_embedding: list[float],
        *,
        allowed_record_ids: Collection[str],
        top_k: int,
    ) -> list[ChromaSearchHit]:
        self.calls.append((query_embedding, set(allowed_record_ids), top_k))
        if self.on_search is not None:
            self.on_search()
        return self.hits


def add_document_with_chunk(
    session: Session,
    knowledge_base: KnowledgeBase,
    *,
    embedding_status: DocumentEmbeddingStatus = DocumentEmbeddingStatus.EMBEDDED,
    generation_id: str | None = "active-generation",
    original_filename: str = "notes.txt",
    content_sequence: int = 0,
    chunk_sequence: int = 0,
    text: str = "Growth rate compares change with the original value.",
    source_start: int = 1,
    source_end: int = 1,
) -> tuple[Document, DocumentContent, Chunk]:
    document = Document(
        knowledge_base=knowledge_base,
        filename=f"stored-{original_filename}",
        original_filename=original_filename,
        file_type="txt",
        file_size=len(text),
        status=DocumentStatus.PARSED.value,
        embedding_status=embedding_status.value,
        embedding_generation_id=generation_id,
    )
    content = DocumentContent(
        document=document,
        sequence=content_sequence,
        text=text,
        source_type="line",
        source_start=source_start,
        source_end=source_end,
    )
    chunk = Chunk(
        document_content=content,
        sequence=chunk_sequence,
        text=text,
        start_offset=0,
        end_offset=len(text),
    )
    session.add(document)
    session.commit()
    return document, content, chunk


def hit_for(
    document: Document,
    chunk: Chunk,
    *,
    distance: float,
    generation_id: str | None = None,
) -> ChromaSearchHit:
    selected_generation = generation_id or document.embedding_generation_id
    assert selected_generation is not None
    return ChromaSearchHit(
        record_id=f"{selected_generation}:{chunk.id}",
        document_id=document.id,
        chunk_id=chunk.id,
        generation_id=selected_generation,
        distance=distance,
    )


def test_retrieval_searches_allowlist_and_hydrates_sqlite_fields(
    test_session_factory: sessionmaker[Session],
) -> None:
    embedding_model = RecordingQueryEmbeddingModel()
    with test_session_factory() as session:
        knowledge_base = KnowledgeBase(name="Economics")
        document, content, chunk = add_document_with_chunk(
            session,
            knowledge_base,
            generation_id="g" * 32,
            original_filename="growth.md",
            content_sequence=2,
            chunk_sequence=3,
            source_start=10,
            source_end=12,
        )
        search_hit = hit_for(document, chunk, distance=0.125)
        vector_store = RecordingVectorStore([search_hit])

        results = search_knowledge_base(
            session,
            knowledge_base.id,
            query="What is growth rate?",
            top_k=5,
            embedding_model=embedding_model,
            vector_store_factory=lambda: vector_store,
        )

    assert embedding_model.queries == ["What is growth rate?"]
    assert vector_store.calls == [
        ([1.0, 0.0, 0.0], {f"{'g' * 32}:{chunk.id}"}, 5)
    ]
    assert results == [
        RetrievalResult(
            chunk_id=chunk.id,
            document_content_id=content.id,
            document_id=document.id,
            knowledge_base_id=knowledge_base.id,
            text="Growth rate compares change with the original value.",
            distance=0.125,
            original_filename="growth.md",
            content_sequence=2,
            chunk_sequence=3,
            source_type="line",
            source_start=10,
            source_end=12,
            start_offset=0,
            end_offset=len("Growth rate compares change with the original value."),
        )
    ]


def test_retrieval_missing_knowledge_base_skips_external_services(
    test_session_factory: sessionmaker[Session],
) -> None:
    embedding_model = RecordingQueryEmbeddingModel()

    def fail_if_store_opens() -> RecordingVectorStore:
        raise AssertionError("Chroma must not open")

    with test_session_factory() as session:
        with pytest.raises(KnowledgeBaseNotFoundError):
            search_knowledge_base(
                session,
                999,
                query="question",
                top_k=5,
                embedding_model=embedding_model,
                vector_store_factory=fail_if_store_opens,
            )

    assert embedding_model.queries == []


def test_retrieval_without_embedded_chunks_skips_external_services(
    test_session_factory: sessionmaker[Session],
) -> None:
    embedding_model = RecordingQueryEmbeddingModel()

    def fail_if_store_opens() -> RecordingVectorStore:
        raise AssertionError("Chroma must not open")

    with test_session_factory() as session:
        knowledge_base = KnowledgeBase(name="Empty")
        add_document_with_chunk(
            session,
            knowledge_base,
            embedding_status=DocumentEmbeddingStatus.STALE,
            generation_id=None,
        )

        with pytest.raises(KnowledgeBaseNotSearchableError):
            search_knowledge_base(
                session,
                knowledge_base.id,
                query="question",
                top_k=5,
                embedding_model=embedding_model,
                vector_store_factory=fail_if_store_opens,
            )

    assert embedding_model.queries == []


def test_retrieval_filters_stale_failed_old_generation_and_other_kb_hits(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        target = KnowledgeBase(name="Target")
        active_document, _, active_chunk = add_document_with_chunk(
            session,
            target,
            generation_id="active",
        )
        stale_document, _, stale_chunk = add_document_with_chunk(
            session,
            target,
            embedding_status=DocumentEmbeddingStatus.STALE,
            generation_id="stale",
        )
        failed_document, _, failed_chunk = add_document_with_chunk(
            session,
            target,
            embedding_status=DocumentEmbeddingStatus.EMBEDDING_FAILED,
            generation_id="failed",
        )
        other_kb = KnowledgeBase(name="Other")
        other_document, _, other_chunk = add_document_with_chunk(
            session,
            other_kb,
            generation_id="other",
        )
        hits = [
            hit_for(stale_document, stale_chunk, distance=0.01),
            hit_for(failed_document, failed_chunk, distance=0.02),
            hit_for(other_document, other_chunk, distance=0.03),
            hit_for(active_document, active_chunk, distance=0.04, generation_id="old"),
            hit_for(active_document, active_chunk, distance=0.05),
        ]
        vector_store = RecordingVectorStore(hits)

        results = search_knowledge_base(
            session,
            target.id,
            query="question",
            top_k=10,
            embedding_model=RecordingQueryEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
        )

    assert vector_store.calls[0][1] == {f"active:{active_chunk.id}"}
    assert [result.chunk_id for result in results] == [active_chunk.id]
    assert results[0].distance == 0.05


def test_retrieval_discards_orphaned_chunk_after_chroma_search(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        knowledge_base = KnowledgeBase(name="Orphan check")
        document, _, chunk = add_document_with_chunk(session, knowledge_base)
        search_hit = hit_for(document, chunk, distance=0.1)

        def delete_chunk() -> None:
            session.execute(delete(Chunk).where(Chunk.id == chunk.id))
            session.commit()

        vector_store = RecordingVectorStore([search_hit], on_search=delete_chunk)

        results = search_knowledge_base(
            session,
            knowledge_base.id,
            query="question",
            top_k=5,
            embedding_model=RecordingQueryEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
        )

    assert results == []


def test_retrieval_rechecks_generation_after_chroma_search(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        knowledge_base = KnowledgeBase(name="Generation check")
        document, _, chunk = add_document_with_chunk(
            session,
            knowledge_base,
            generation_id="generation-a",
        )
        search_hit = hit_for(document, chunk, distance=0.1)

        def replace_generation() -> None:
            stored_document = session.get(Document, document.id)
            assert stored_document is not None
            stored_document.embedding_generation_id = "generation-b"
            session.commit()

        vector_store = RecordingVectorStore(
            [search_hit],
            on_search=replace_generation,
        )

        results = search_knowledge_base(
            session,
            knowledge_base.id,
            query="question",
            top_k=5,
            embedding_model=RecordingQueryEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
        )

    assert results == []


def test_retrieval_applies_stable_sort_and_final_top_k(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        knowledge_base = KnowledgeBase(name="Sorting")
        first_document, first_content, first_chunk = add_document_with_chunk(
            session,
            knowledge_base,
            generation_id="first",
            content_sequence=1,
            chunk_sequence=1,
            text="first",
        )
        second_content = DocumentContent(
            document=first_document,
            sequence=0,
            text="second content",
            source_type="line",
            source_start=2,
            source_end=2,
        )
        second_chunk = Chunk(
            document_content=second_content,
            sequence=2,
            text="second",
            start_offset=0,
            end_offset=6,
        )
        third_chunk = Chunk(
            document_content=second_content,
            sequence=0,
            text="third",
            start_offset=0,
            end_offset=5,
        )
        session.add_all([second_chunk, third_chunk])
        second_document, _, fourth_chunk = add_document_with_chunk(
            session,
            knowledge_base,
            generation_id="second",
            text="fourth",
        )
        session.commit()

        vector_store = RecordingVectorStore(
            [
                hit_for(first_document, first_chunk, distance=0.2),
                hit_for(second_document, fourth_chunk, distance=0.1),
                hit_for(first_document, second_chunk, distance=0.2),
                hit_for(first_document, third_chunk, distance=0.2),
            ]
        )

        results = search_knowledge_base(
            session,
            knowledge_base.id,
            query="question",
            top_k=3,
            embedding_model=RecordingQueryEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
        )

    assert vector_store.calls[0][2] == 3
    assert [result.chunk_id for result in results] == [
        fourth_chunk.id,
        third_chunk.id,
        second_chunk.id,
    ]
    assert first_content.sequence == 1
