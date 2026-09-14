from collections.abc import Callable, Sequence
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.document_processing.chunking import ChunkDraft
from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    DocumentStatus,
    KnowledgeBase,
)
from app.services import document_chunking
from app.services.embedding_cleanup import SAFE_EMBEDDING_CLEANUP_ERROR


class RecordingVectorStore:
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
            raise RuntimeError("raw Chroma failure /private/chroma/path")


def create_document_with_contents(
    session: Session,
    *,
    status: str = DocumentStatus.PARSED.value,
    contents: Sequence[tuple[int, str]] = ((0, "Parsed text"),),
) -> Document:
    document = Document(
        knowledge_base=KnowledgeBase(name="Computer Science"),
        filename="stored.txt",
        original_filename="notes.txt",
        file_type="txt",
        file_size=12,
        status=status,
    )
    document.contents.extend(
        [
            DocumentContent(
                sequence=sequence,
                text=text,
                source_type="line",
                source_start=sequence + 1,
                source_end=sequence + 1,
            )
            for sequence, text in contents
        ]
    )
    session.add(document)
    session.commit()
    return document


def test_rebuild_chunks_uses_content_order_and_local_chunk_sequences(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        document = create_document_with_contents(
            session,
            contents=((1, "second"), (0, "abcdefghij")),
        )

        result = document_chunking.rebuild_document_chunks(
            session,
            document.id,
            chunk_size=6,
            overlap=2,
        )

        assert result is not None
        _, chunks = result
        assert [
            (
                chunk.document_content.sequence,
                chunk.sequence,
                chunk.text,
                chunk.start_offset,
                chunk.end_offset,
            )
            for chunk in chunks
        ] == [
            (0, 0, "abcdef", 0, 6),
            (0, 1, "efghij", 4, 10),
            (1, 0, "second", 0, 6),
        ]


def test_rebuild_chunks_replaces_existing_chunks(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        document = create_document_with_contents(session)
        content = document.contents[0]
        old_chunk = Chunk(
            document_content=content,
            sequence=0,
            text="Old text",
            start_offset=0,
            end_offset=3,
        )
        session.add(old_chunk)
        session.commit()

        result = document_chunking.rebuild_document_chunks(session, document.id)

        assert result is not None
        _, chunks = result
        assert [(chunk.sequence, chunk.text) for chunk in chunks] == [
            (0, "Parsed text")
        ]
        assert list(session.scalars(select(Chunk)).all()) == chunks


def test_rebuild_chunks_marks_embedded_stale_before_cleanup_then_pending(
    test_session_factory: sessionmaker[Session],
) -> None:
    observed_during_cleanup: list[tuple[str, str | None, list[str]]] = []

    def observe_committed_stale_state(document_id: int) -> None:
        with test_session_factory() as inspection_session:
            stored_document = inspection_session.get(Document, document_id)
            assert stored_document is not None
            stored_chunks = list(
                inspection_session.scalars(select(Chunk).order_by(Chunk.id)).all()
            )
            observed_during_cleanup.append(
                (
                    stored_document.embedding_status,
                    stored_document.embedding_generation_id,
                    [chunk.text for chunk in stored_chunks],
                )
            )

    vector_store = RecordingVectorStore(on_delete=observe_committed_stale_state)
    with test_session_factory() as session:
        document = create_document_with_contents(session)
        content = document.contents[0]
        session.add(
            Chunk(
                document_content=content,
                sequence=0,
                text="Old text",
                start_offset=0,
                end_offset=3,
            )
        )
        document.embedding_status = DocumentEmbeddingStatus.EMBEDDED.value
        document.embedding_generation_id = "a" * 32
        document.embedded_at = datetime(2026, 9, 13, 12, 0)
        session.commit()

        result = document_chunking.rebuild_document_chunks(
            session,
            document.id,
            vector_store_factory=lambda: vector_store,
        )

        assert result is not None
        returned_document, chunks = result
        assert [chunk.text for chunk in chunks] == ["Parsed text"]
        assert returned_document.embedding_status == (
            DocumentEmbeddingStatus.PENDING.value
        )
        assert returned_document.embedding_generation_id is None
        assert returned_document.embedded_at is None
        assert returned_document.embedding_error is None

    assert observed_during_cleanup == [
        (DocumentEmbeddingStatus.STALE.value, None, ["Parsed text"])
    ]
    assert vector_store.deleted_document_ids == [document.id]


def test_rebuild_chunks_keeps_stale_with_safe_error_when_cleanup_fails(
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = RecordingVectorStore(fail=True)
    with test_session_factory() as session:
        document = create_document_with_contents(session)
        document.embedding_status = DocumentEmbeddingStatus.EMBEDDED.value
        document.embedding_generation_id = "b" * 32
        document.embedded_at = datetime(2026, 9, 13, 12, 0)
        session.commit()
        document_id = document.id

        with pytest.raises(
            document_chunking.embedding_cleanup.DocumentEmbeddingCleanupError,
            match=SAFE_EMBEDDING_CLEANUP_ERROR,
        ):
            document_chunking.rebuild_document_chunks(
                session,
                document_id,
                vector_store_factory=lambda: vector_store,
            )

    with test_session_factory() as session:
        stored_document = session.get(Document, document_id)
        assert stored_document is not None
        assert stored_document.embedding_status == DocumentEmbeddingStatus.STALE.value
        assert stored_document.embedding_generation_id is None
        assert stored_document.embedded_at is None
        assert stored_document.embedding_error == SAFE_EMBEDDING_CLEANUP_ERROR
        assert [chunk.text for chunk in session.scalars(select(Chunk)).all()] == [
            "Parsed text"
        ]


def test_rebuild_chunks_with_no_effective_text_returns_empty_list(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        document = create_document_with_contents(
            session,
            contents=((0, "  \n\t"),),
        )

        result = document_chunking.rebuild_document_chunks(session, document.id)

        assert result is not None
        assert result[1] == []
        assert list(session.scalars(select(Chunk)).all()) == []


@pytest.mark.parametrize(
    "status",
    [
        DocumentStatus.PENDING.value,
        DocumentStatus.PARSING.value,
        DocumentStatus.PARSE_FAILED.value,
    ],
)
def test_rebuild_chunks_rejects_documents_that_are_not_parsed(
    test_session_factory: sessionmaker[Session],
    status: str,
) -> None:
    with test_session_factory() as session:
        document = create_document_with_contents(session, status=status)

        with pytest.raises(
            document_chunking.DocumentNotReadyForChunkingError,
            match="Document must be parsed",
        ):
            document_chunking.rebuild_document_chunks(session, document.id)

        assert list(session.scalars(select(Chunk)).all()) == []


def test_rebuild_chunks_returns_none_for_missing_document(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        assert document_chunking.rebuild_document_chunks(session, 999) is None


def test_chunk_persistence_failure_restores_previous_chunks(
    test_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with test_session_factory() as session:
        document = create_document_with_contents(session)
        content = document.contents[0]
        old_chunk = Chunk(
            document_content=content,
            sequence=0,
            text="Last complete chunk",
            start_offset=0,
            end_offset=10,
        )
        session.add(old_chunk)
        document.embedding_status = DocumentEmbeddingStatus.EMBEDDED.value
        document.embedding_generation_id = "c" * 32
        document.embedded_at = datetime(2026, 9, 13, 12, 0)
        session.commit()
        old_chunk_id = old_chunk.id
        document_id = document.id
        old_embedded_at = document.embedded_at

        def fail_if_chroma_opens() -> RecordingVectorStore:
            raise AssertionError("Chroma must not open before the SQLite commit")

        def duplicate_sequences(
            _: str,
            *,
            chunk_size: int,
            overlap: int,
        ) -> list[ChunkDraft]:
            return [
                ChunkDraft("First", 0, 0, 5),
                ChunkDraft("Second", 0, 5, 11),
            ]

        monkeypatch.setattr(
            document_chunking.chunking,
            "split_text",
            duplicate_sequences,
        )

        with pytest.raises(
            document_chunking.DocumentChunkingPersistenceError,
            match="Document chunks could not be saved",
        ):
            document_chunking.rebuild_document_chunks(
                session,
                document_id,
                vector_store_factory=fail_if_chroma_opens,
            )

    with test_session_factory() as session:
        chunks = list(session.scalars(select(Chunk)).all())
        assert [(chunk.id, chunk.text) for chunk in chunks] == [
            (old_chunk_id, "Last complete chunk")
        ]
        stored_document = session.get(Document, document_id)
        assert stored_document is not None
        assert stored_document.embedding_status == (
            DocumentEmbeddingStatus.EMBEDDED.value
        )
        assert stored_document.embedding_generation_id == "c" * 32
        assert stored_document.embedded_at == old_embedded_at
        assert stored_document.embedding_error is None


def test_chunk_generation_failure_preserves_chunks_and_embedding_without_chroma(
    test_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with test_session_factory() as session:
        document = create_document_with_contents(session)
        old_chunk = Chunk(
            document_content=document.contents[0],
            sequence=0,
            text="Last complete chunk",
            start_offset=0,
            end_offset=10,
        )
        session.add(old_chunk)
        document.embedding_status = DocumentEmbeddingStatus.EMBEDDED.value
        document.embedding_generation_id = "d" * 32
        session.commit()
        document_id = document.id
        old_chunk_id = old_chunk.id

        def fail_generation(*_: object, **__: object) -> list[ChunkDraft]:
            raise RuntimeError("chunk generation failed")

        def fail_if_chroma_opens() -> RecordingVectorStore:
            raise AssertionError("Chroma must not open when generation fails")

        monkeypatch.setattr(document_chunking.chunking, "split_text", fail_generation)

        with pytest.raises(
            document_chunking.DocumentChunkingPersistenceError,
            match="Document chunks could not be saved",
        ):
            document_chunking.rebuild_document_chunks(
                session,
                document_id,
                vector_store_factory=fail_if_chroma_opens,
            )

    with test_session_factory() as session:
        stored_document = session.get(Document, document_id)
        assert stored_document is not None
        assert stored_document.embedding_status == (
            DocumentEmbeddingStatus.EMBEDDED.value
        )
        assert stored_document.embedding_generation_id == "d" * 32
        assert [(chunk.id, chunk.text) for chunk in session.scalars(select(Chunk))] == [
            (old_chunk_id, "Last complete chunk")
        ]


def test_pending_document_without_generation_rebuilds_without_opening_chroma(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        document = create_document_with_contents(session)

        def fail_if_chroma_opens() -> RecordingVectorStore:
            raise AssertionError("Chroma must not open without an old embedding")

        result = document_chunking.rebuild_document_chunks(
            session,
            document.id,
            vector_store_factory=fail_if_chroma_opens,
        )

        assert result is not None
        returned_document, chunks = result
        assert [chunk.text for chunk in chunks] == ["Parsed text"]
        assert returned_document.embedding_status == (
            DocumentEmbeddingStatus.PENDING.value
        )
        assert returned_document.embedding_generation_id is None


def test_embedding_failed_with_generation_is_invalidated_and_cleaned(
    test_session_factory: sessionmaker[Session],
) -> None:
    vector_store = RecordingVectorStore()
    with test_session_factory() as session:
        document = create_document_with_contents(session)
        document.embedding_status = DocumentEmbeddingStatus.EMBEDDING_FAILED.value
        document.embedding_generation_id = "e" * 32
        document.embedded_at = datetime(2026, 9, 13, 12, 0)
        document.embedding_error = "Previous embedding attempt failed"
        session.commit()

        result = document_chunking.rebuild_document_chunks(
            session,
            document.id,
            vector_store_factory=lambda: vector_store,
        )

        assert result is not None
        returned_document, _ = result
        assert returned_document.embedding_status == (
            DocumentEmbeddingStatus.PENDING.value
        )
        assert returned_document.embedding_generation_id is None
        assert returned_document.embedded_at is None
        assert returned_document.embedding_error is None
        assert vector_store.deleted_document_ids == [document.id]


def test_get_document_chunks_returns_source_order_without_rebuilding(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        document = create_document_with_contents(
            session,
            status=DocumentStatus.PARSE_FAILED.value,
            contents=((1, "second"), (0, "first")),
        )
        contents_by_sequence = {
            content.sequence: content for content in document.contents
        }
        session.add_all(
            [
                Chunk(
                    document_content=contents_by_sequence[1],
                    sequence=0,
                    text="second",
                    start_offset=0,
                    end_offset=6,
                ),
                Chunk(
                    document_content=contents_by_sequence[0],
                    sequence=1,
                    text="rst",
                    start_offset=2,
                    end_offset=5,
                ),
                Chunk(
                    document_content=contents_by_sequence[0],
                    sequence=0,
                    text="fir",
                    start_offset=0,
                    end_offset=3,
                ),
            ]
        )
        session.commit()

        result = document_chunking.get_document_chunks(session, document.id)

        assert result is not None
        returned_document, chunks = result
        assert returned_document.status == DocumentStatus.PARSE_FAILED.value
        assert [
            (chunk.document_content.sequence, chunk.sequence, chunk.text)
            for chunk in chunks
        ] == [
            (0, 0, "fir"),
            (0, 1, "rst"),
            (1, 0, "second"),
        ]


def test_get_document_chunks_returns_none_for_missing_document(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        assert document_chunking.get_document_chunks(session, 999) is None
