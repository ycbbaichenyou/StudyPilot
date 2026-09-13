from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.embeddings import DashScopeEmbeddingError
from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    DocumentStatus,
    KnowledgeBase,
)
from app.services import document_embedding
from app.stores import (
    ChromaGenerationSummary,
    ChromaRecord,
    ChromaVectorStoreError,
)


class DeterministicEmbeddingModel:
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [
            [float(index), float(len(text)), 1.0]
            for index, text in enumerate(texts)
        ]


class FailingEmbeddingModel:
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        raise DashScopeEmbeddingError("DashScope embedding request failed")


class MemoryVectorStore:
    def __init__(
        self,
        *,
        fail_after_first_record: bool = False,
        verification_mode: str = "complete",
    ) -> None:
        self.records: list[ChromaRecord] = []
        self.fail_after_first_record = fail_after_first_record
        self.verification_mode = verification_mode
        self.deleted_generations: list[str] = []

    def add_records(self, records: list[ChromaRecord]) -> None:
        if self.fail_after_first_record:
            self.records.extend(records[:1])
            raise ChromaVectorStoreError("partial write")
        candidate_records = list(records)
        if self.verification_mode == "missing":
            candidate_records = candidate_records[:-1]
        elif self.verification_mode == "extra":
            extra_metadata = dict(candidate_records[-1].metadata)
            extra_metadata["chunk_id"] = 999_999
            candidate_records.append(
                replace(
                    candidate_records[-1],
                    id=f"{extra_metadata['generation_id']}:999999",
                    metadata=extra_metadata,
                )
            )
        elif self.verification_mode == "mismatch":
            mismatched_metadata = dict(candidate_records[0].metadata)
            mismatched_metadata["chunk_id"] = 999_999
            candidate_records[0] = replace(
                candidate_records[0],
                metadata=mismatched_metadata,
            )
        self.records.extend(candidate_records)

    def get_generation_summary(
        self,
        generation_id: str,
    ) -> ChromaGenerationSummary:
        generation_records = [
            record
            for record in self.records
            if record.metadata["generation_id"] == generation_id
        ]
        chunk_ids = {int(record.metadata["chunk_id"]) for record in generation_records}
        return ChromaGenerationSummary(
            record_count=len(generation_records),
            chunk_ids=frozenset(chunk_ids),
        )

    def delete_generation(self, generation_id: str) -> None:
        self.deleted_generations.append(generation_id)
        self.records = [
            record
            for record in self.records
            if record.metadata["generation_id"] != generation_id
        ]


def create_chunked_document(session: Session) -> Document:
    document = Document(
        knowledge_base=KnowledgeBase(name="Computer Science"),
        filename="stored.txt",
        original_filename="notes.txt",
        file_type="txt",
        file_size=12,
        status=DocumentStatus.PARSED.value,
    )
    first_content = DocumentContent(
        document=document,
        sequence=0,
        text="First parsed unit",
        source_type="line",
        source_start=1,
        source_end=1,
    )
    second_content = DocumentContent(
        document=document,
        sequence=1,
        text="Second parsed unit",
        source_type="line",
        source_start=2,
        source_end=2,
    )
    session.add_all(
        [
            Chunk(
                document_content=second_content,
                sequence=0,
                text="Second",
                start_offset=0,
                end_offset=6,
            ),
            Chunk(
                document_content=first_content,
                sequence=0,
                text="First",
                start_offset=0,
                end_offset=5,
            ),
        ]
    )
    session.commit()
    return document


def test_embed_document_activates_one_complete_generation(
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = MemoryVectorStore()
    with test_session_factory() as session:
        document = create_chunked_document(session)

        result = document_embedding.embed_document(
            session,
            document.id,
            embedding_model=DeterministicEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
        )

        assert result is document
        assert document.embedding_status == DocumentEmbeddingStatus.EMBEDDED.value
        assert document.embedding_error is None
        assert document.embedded_at is not None
        assert document.embedded_at.tzinfo is None
        assert document.embedding_generation_id is not None

    assert len(vector_store.records) == 2
    assert [record.text for record in vector_store.records] == ["First", "Second"]
    assert all(
        record.id == f"{record.metadata['generation_id']}:{record.metadata['chunk_id']}"
        for record in vector_store.records
    )
    assert {
        record.metadata["generation_id"] for record in vector_store.records
    } == {document.embedding_generation_id}
    assert {record.metadata["document_id"] for record in vector_store.records} == {
        document.id
    }


def test_embedding_provider_failure_sets_failed_status_without_generation(
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = MemoryVectorStore()
    with test_session_factory() as session:
        document = create_chunked_document(session)

        result = document_embedding.embed_document(
            session,
            document.id,
            embedding_model=FailingEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
        )

        assert result is document
        assert document.embedding_status == (
            DocumentEmbeddingStatus.EMBEDDING_FAILED.value
        )
        assert document.embedding_error == "DashScope embedding request failed"
        assert document.embedded_at is None
        assert document.embedding_generation_id is None
    assert vector_store.records == []


def test_partial_chroma_failure_removes_inactive_generation(
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = MemoryVectorStore(fail_after_first_record=True)
    with test_session_factory() as session:
        document = create_chunked_document(session)

        result = document_embedding.embed_document(
            session,
            document.id,
            embedding_model=DeterministicEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
        )

        assert result is document
        assert document.embedding_status == (
            DocumentEmbeddingStatus.EMBEDDING_FAILED.value
        )
        assert document.embedding_error == "Document embeddings could not be saved"
        assert document.embedding_generation_id is None
    assert vector_store.records == []


def test_completion_status_failure_removes_unactivated_chroma_generation(
    test_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector_store = MemoryVectorStore()
    with test_session_factory() as session:
        document = create_chunked_document(session)
        real_commit = session.commit
        commit_count = 0

        def fail_completion_commit_once() -> None:
            nonlocal commit_count
            commit_count += 1
            if commit_count == 2:
                session.flush()
                raise RuntimeError("database write failed")
            real_commit()

        monkeypatch.setattr(session, "commit", fail_completion_commit_once)

        result = document_embedding.embed_document(
            session,
            document.id,
            embedding_model=DeterministicEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
        )

        assert result is document
        assert commit_count == 3
        assert document.embedding_status == (
            DocumentEmbeddingStatus.EMBEDDING_FAILED.value
        )
        assert document.embedding_error == (
            "Document embedding completion status could not be saved"
        )
        assert document.embedding_generation_id is None
    assert vector_store.records == []


@pytest.mark.parametrize(
    ("status", "with_chunks", "message"),
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
def test_embed_document_requires_parsed_document_with_chunks(
    test_session_factory: sessionmaker[Session],
    status: str,
    with_chunks: bool,
    message: str,
) -> None:
    with test_session_factory() as session:
        document = create_chunked_document(session)
        document.status = status
        if not with_chunks:
            for content in document.contents:
                content.chunks.clear()
        session.commit()

        with pytest.raises(
            document_embedding.DocumentNotReadyForEmbeddingError,
            match=message,
        ):
            document_embedding.embed_document(
                session,
                document.id,
                embedding_model=DeterministicEmbeddingModel(),
                vector_store_factory=MemoryVectorStore,
            )

        assert document.embedding_status == DocumentEmbeddingStatus.PENDING.value


def test_successful_reembedding_replaces_previous_generation(
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = MemoryVectorStore()
    with test_session_factory() as session:
        document = create_chunked_document(session)
        first_result = document_embedding.embed_document(
            session,
            document.id,
            embedding_model=DeterministicEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
        )
        assert first_result is not None
        first_generation_id = first_result.embedding_generation_id

        second_result = document_embedding.embed_document(
            session,
            document.id,
            embedding_model=DeterministicEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
        )

        assert second_result is not None
        assert second_result.embedding_generation_id != first_generation_id
        assert {
            record.metadata["generation_id"] for record in vector_store.records
        } == {second_result.embedding_generation_id}


@pytest.mark.parametrize("verification_mode", ["missing", "extra", "mismatch"])
def test_candidate_verification_failure_never_activates_or_deletes_previous_generation(
    test_session_factory: sessionmaker[Session],
    verification_mode: str,
) -> None:
    vector_store = MemoryVectorStore()
    with test_session_factory() as session:
        document = create_chunked_document(session)
        first_result = document_embedding.embed_document(
            session,
            document.id,
            embedding_model=DeterministicEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
        )
        assert first_result is not None
        previous_generation_id = first_result.embedding_generation_id
        previous_embedded_at = first_result.embedded_at
        assert previous_generation_id is not None

        vector_store.verification_mode = verification_mode
        second_result = document_embedding.embed_document(
            session,
            document.id,
            embedding_model=DeterministicEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
        )

        assert second_result is not None
        assert second_result.embedding_status == (
            DocumentEmbeddingStatus.EMBEDDING_FAILED.value
        )
        assert second_result.embedding_error == (
            "Chroma candidate embedding generation is incomplete"
        )
        assert second_result.embedding_generation_id == previous_generation_id
        assert second_result.embedded_at == previous_embedded_at

    assert previous_generation_id not in vector_store.deleted_generations
    assert len(vector_store.deleted_generations) == 1
    assert {
        record.metadata["generation_id"] for record in vector_store.records
    } == {previous_generation_id}
