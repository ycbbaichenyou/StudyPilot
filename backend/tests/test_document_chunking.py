from collections.abc import Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.document_processing.chunking import ChunkDraft
from app.models import Chunk, Document, DocumentContent, DocumentStatus, KnowledgeBase
from app.services import document_chunking


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
        session.commit()
        old_chunk_id = old_chunk.id
        document_id = document.id

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
            document_chunking.rebuild_document_chunks(session, document_id)

    with test_session_factory() as session:
        chunks = list(session.scalars(select(Chunk)).all())
        assert [(chunk.id, chunk.text) for chunk in chunks] == [
            (old_chunk_id, "Last complete chunk")
        ]


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
