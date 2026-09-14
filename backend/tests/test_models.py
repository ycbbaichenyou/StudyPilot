import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.database import (
    DATABASE_PATH,
    DEFAULT_DATABASE_URL,
    create_db_engine,
    get_database_url,
)
from app.main import create_app
from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    DocumentStatus,
    KnowledgeBase,
)


def test_document_embedding_status_includes_stale() -> None:
    assert DocumentEmbeddingStatus.STALE.value == "stale"


def test_default_database_url_uses_stable_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STUDYPILOT_DATABASE_URL", raising=False)

    assert get_database_url() == DEFAULT_DATABASE_URL
    assert DATABASE_PATH.is_absolute()
    assert Path(make_url(DEFAULT_DATABASE_URL).database or "") == DATABASE_PATH


def test_database_contains_current_tables(test_engine: Engine) -> None:
    inspector = inspect(test_engine)

    assert set(inspector.get_table_names()) == {
        "chunks",
        "document_contents",
        "documents",
        "knowledge_bases",
    }

    document_foreign_keys = inspector.get_foreign_keys("documents")
    assert len(document_foreign_keys) == 1
    assert document_foreign_keys[0]["constrained_columns"] == ["knowledge_base_id"]
    assert document_foreign_keys[0]["referred_table"] == "knowledge_bases"
    assert document_foreign_keys[0]["referred_columns"] == ["id"]
    assert document_foreign_keys[0]["options"]["ondelete"] == "CASCADE"

    content_foreign_keys = inspector.get_foreign_keys("document_contents")
    assert len(content_foreign_keys) == 1
    assert content_foreign_keys[0]["constrained_columns"] == ["document_id"]
    assert content_foreign_keys[0]["referred_table"] == "documents"
    assert content_foreign_keys[0]["referred_columns"] == ["id"]
    assert content_foreign_keys[0]["options"]["ondelete"] == "CASCADE"

    chunk_foreign_keys = inspector.get_foreign_keys("chunks")
    assert len(chunk_foreign_keys) == 1
    assert chunk_foreign_keys[0]["constrained_columns"] == ["document_content_id"]
    assert chunk_foreign_keys[0]["referred_table"] == "document_contents"
    assert chunk_foreign_keys[0]["referred_columns"] == ["id"]
    assert chunk_foreign_keys[0]["options"]["ondelete"] == "CASCADE"


def test_sqlite_engine_enables_foreign_keys(test_engine: Engine) -> None:
    with test_engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1


def test_database_rejects_orphan_document(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        session.add(
            Document(
                knowledge_base_id=999,
                filename="stored.pdf",
                original_filename="notes.pdf",
                file_type="pdf",
                file_size=1024,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()


def test_document_content_orm_relationship_and_fields(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        document = Document(
            knowledge_base=KnowledgeBase(name="Computer Science"),
            filename="stored.txt",
            original_filename="notes.txt",
            file_type="txt",
            file_size=12,
        )
        content = DocumentContent(
            document=document,
            sequence=0,
            text="First parsed unit",
            source_type="line",
            source_start=1,
            source_end=2,
        )
        session.add(content)
        session.commit()
        session.refresh(content)

        assert content.id is not None
        assert content.document_id == document.id
        assert document.contents == [content]
        assert content.document is document
        assert content.sequence == 0
        assert content.text == "First parsed unit"
        assert content.source_type == "line"
        assert content.source_start == 1
        assert content.source_end == 2
        assert content.created_at.tzinfo is None
        assert document.status == DocumentStatus.PENDING.value
        assert document.parse_error is None
        assert document.parsed_at is None
        assert document.embedding_status == DocumentEmbeddingStatus.PENDING.value
        assert document.embedding_error is None
        assert document.embedded_at is None
        assert document.embedding_generation_id is None


def test_chunk_orm_relationship_and_fields(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        content = DocumentContent(
            document=Document(
                knowledge_base=KnowledgeBase(name="Computer Science"),
                filename="stored.txt",
                original_filename="notes.txt",
                file_type="txt",
                file_size=12,
            ),
            sequence=0,
            text="First parsed unit",
            source_type="line",
            source_start=1,
            source_end=1,
        )
        chunk = Chunk(
            document_content=content,
            sequence=0,
            text="First",
            start_offset=0,
            end_offset=5,
        )
        session.add(chunk)
        session.commit()
        session.refresh(chunk)

        assert chunk.id is not None
        assert chunk.document_content_id == content.id
        assert content.chunks == [chunk]
        assert chunk.document_content is content
        assert chunk.sequence == 0
        assert chunk.text == "First"
        assert chunk.start_offset == 0
        assert chunk.end_offset == 5
        assert chunk.created_at.tzinfo is None


def test_deleting_document_cascades_to_contents_and_chunks(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        document = Document(
            knowledge_base=KnowledgeBase(name="Computer Science"),
            filename="stored.txt",
            original_filename="notes.txt",
            file_type="txt",
            file_size=12,
        )
        content = DocumentContent(
            document=document,
            sequence=0,
            text="Parsed unit",
            source_type="line",
            source_start=1,
            source_end=1,
        )
        chunk = Chunk(
            document_content=content,
            sequence=0,
            text="Parsed unit",
            start_offset=0,
            end_offset=11,
        )
        session.add(content)
        session.commit()
        document_id = document.id
        content_id = content.id
        chunk_id = chunk.id

        session.execute(delete(Document).where(Document.id == document_id))
        session.commit()

        assert session.scalar(
            select(Document).where(Document.id == document_id)
        ) is None
        assert session.scalar(
            select(DocumentContent).where(DocumentContent.id == content_id)
        ) is None
        assert session.scalar(select(Chunk).where(Chunk.id == chunk_id)) is None


def test_document_content_sequence_is_unique_per_document(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        document = Document(
            knowledge_base=KnowledgeBase(name="Computer Science"),
            filename="stored.txt",
            original_filename="notes.txt",
            file_type="txt",
            file_size=12,
        )
        document.contents.extend(
            [
                DocumentContent(
                    sequence=0,
                    text="First parsed unit",
                    source_type="line",
                    source_start=1,
                    source_end=1,
                ),
                DocumentContent(
                    sequence=0,
                    text="Duplicate sequence",
                    source_type="line",
                    source_start=2,
                    source_end=2,
                ),
            ]
        )
        session.add(document)

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()
        assert list(session.scalars(select(DocumentContent)).all()) == []


def test_chunk_sequence_is_unique_per_document_content(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        content = DocumentContent(
            document=Document(
                knowledge_base=KnowledgeBase(name="Computer Science"),
                filename="stored.txt",
                original_filename="notes.txt",
                file_type="txt",
                file_size=12,
            ),
            sequence=0,
            text="Parsed unit",
            source_type="line",
            source_start=1,
            source_end=1,
        )
        content.chunks.extend(
            [
                Chunk(
                    sequence=0,
                    text="First",
                    start_offset=0,
                    end_offset=5,
                ),
                Chunk(
                    sequence=0,
                    text="Second",
                    start_offset=6,
                    end_offset=11,
                ),
            ]
        )
        session.add(content)

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()
        assert list(session.scalars(select(Chunk)).all()) == []


def test_updated_at_changes_after_database_update(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        knowledge_base = KnowledgeBase(name="Computer Science")
        session.add(knowledge_base)
        session.commit()
        session.refresh(knowledge_base)

        original_updated_at = knowledge_base.updated_at
        assert knowledge_base.created_at.tzinfo is None
        assert original_updated_at.tzinfo is None

        time.sleep(0.001)
        knowledge_base.description = "Updated description"
        session.commit()
        session.refresh(knowledge_base)

        assert knowledge_base.updated_at > original_updated_at
        assert knowledge_base.updated_at.tzinfo is None


def test_lifespan_prepares_environment_database_without_creating_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "nested" / "lifespan.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("STUDYPILOT_DATABASE_URL", database_url)
    temporary_engine = create_db_engine()

    try:
        assert temporary_engine.url.database == str(database_path)
        assert not database_path.exists()

        test_app = create_app(database_engine=temporary_engine)
        with TestClient(test_app):
            assert database_path.parent.is_dir()
            assert inspect(temporary_engine).get_table_names() == []

        assert database_path.exists()
        with temporary_engine.connect() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
    finally:
        temporary_engine.dispose()
