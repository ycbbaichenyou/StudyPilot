from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.database import create_db_engine
from app.main import create_app


BACKEND_DIRECTORY = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "0001_stage_2_baseline"
DOCUMENT_CONTENT_REVISION = "0002_stage_3_2_document_content"
CHUNK_REVISION = "0003_stage_4_2_chunk"
HEAD_REVISION = "0004_stage_5_1_document_embedding"


def test_migrations_upgrade_baseline_database_to_current_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("STUDYPILOT_DATABASE_URL", database_url)
    config = Config(str(BACKEND_DIRECTORY / "alembic.ini"))
    command.upgrade(config, BASELINE_REVISION)

    engine = create_db_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO knowledge_bases
                        (id, name, description, created_at, updated_at)
                    VALUES
                        (1, 'Computer Science', NULL, :created_at, :updated_at)
                    """
                ),
                {
                    "created_at": datetime(2026, 9, 11, 10, 0),
                    "updated_at": datetime(2026, 9, 11, 10, 0),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO documents
                        (
                            id,
                            knowledge_base_id,
                            filename,
                            original_filename,
                            file_type,
                            file_size,
                            status,
                            created_at,
                            updated_at
                        )
                    VALUES
                        (
                            1,
                            1,
                            'stored.txt',
                            'notes.txt',
                            'txt',
                            12,
                            'pending',
                            :created_at,
                            :updated_at
                        )
                    """
                ),
                {
                    "created_at": datetime(2026, 9, 11, 10, 0),
                    "updated_at": datetime(2026, 9, 11, 10, 0),
                },
            )
    finally:
        engine.dispose()

    command.upgrade(config, DOCUMENT_CONTENT_REVISION)

    engine = create_db_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO document_contents
                        (
                            id,
                            document_id,
                            sequence,
                            text,
                            source_type,
                            source_start,
                            source_end,
                            created_at
                        )
                    VALUES
                        (1, 1, 0, 'Parsed text', 'line', 1, 1, :created_at)
                    """
                ),
                {"created_at": datetime(2026, 9, 11, 10, 1)},
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_db_engine(database_url)
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == {
            "alembic_version",
            "chunks",
            "document_contents",
            "documents",
            "knowledge_bases",
        }

        document_columns = {
            column["name"]: column for column in inspector.get_columns("documents")
        }
        assert document_columns["parse_error"]["nullable"] is True
        assert document_columns["parsed_at"]["nullable"] is True
        assert document_columns["embedding_status"]["nullable"] is False
        assert document_columns["embedding_error"]["nullable"] is True
        assert document_columns["embedded_at"]["nullable"] is True
        assert document_columns["embedding_generation_id"]["nullable"] is True

        content_foreign_keys = inspector.get_foreign_keys("document_contents")
        assert len(content_foreign_keys) == 1
        assert content_foreign_keys[0]["constrained_columns"] == ["document_id"]
        assert content_foreign_keys[0]["referred_table"] == "documents"
        assert content_foreign_keys[0]["options"]["ondelete"] == "CASCADE"

        unique_constraints = inspector.get_unique_constraints("document_contents")
        assert unique_constraints == [
            {
                "name": "uq_document_contents_document_id_sequence",
                "column_names": ["document_id", "sequence"],
            }
        ]
        indexes = inspector.get_indexes("document_contents")
        assert indexes == [
            {
                "name": "ix_document_contents_document_id",
                "column_names": ["document_id"],
                "unique": 0,
                "dialect_options": {},
            }
        ]

        chunk_foreign_keys = inspector.get_foreign_keys("chunks")
        assert len(chunk_foreign_keys) == 1
        assert chunk_foreign_keys[0]["constrained_columns"] == [
            "document_content_id"
        ]
        assert chunk_foreign_keys[0]["referred_table"] == "document_contents"
        assert chunk_foreign_keys[0]["options"]["ondelete"] == "CASCADE"

        assert inspector.get_unique_constraints("chunks") == [
            {
                "name": "uq_chunks_document_content_id_sequence",
                "column_names": ["document_content_id", "sequence"],
            }
        ]
        chunk_check_constraint_names = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("chunks")
        }
        assert chunk_check_constraint_names == {
            "ck_chunks_end_offset_after_start_offset",
            "ck_chunks_sequence_nonnegative",
            "ck_chunks_start_offset_nonnegative",
        }
        assert inspector.get_indexes("chunks") == [
            {
                "name": "ix_chunks_document_content_id",
                "column_names": ["document_content_id"],
                "unique": 0,
                "dialect_options": {},
            }
        ]

        with engine.connect() as connection:
            migrated_document = connection.execute(
                text(
                    """
                    SELECT
                        status,
                        parse_error,
                        parsed_at,
                        embedding_status,
                        embedding_error,
                        embedded_at,
                        embedding_generation_id
                    FROM documents
                    WHERE id = 1
                    """
                )
            ).one()
            assert migrated_document == (
                "pending",
                None,
                None,
                "pending",
                None,
                None,
                None,
            )
            assert connection.execute(
                text(
                    """
                    SELECT document_id, sequence, text
                    FROM document_contents
                    WHERE id = 1
                    """
                )
            ).one() == (1, 0, "Parsed text")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                HEAD_REVISION
            )
    finally:
        engine.dispose()


def test_application_start_does_not_preempt_pending_embedding_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'application-start.db'}"
    monkeypatch.setenv("STUDYPILOT_DATABASE_URL", database_url)
    config = Config(str(BACKEND_DIRECTORY / "alembic.ini"))
    command.upgrade(config, CHUNK_REVISION)

    engine = create_db_engine(database_url)
    try:
        test_app = create_app(database_engine=engine)
        with TestClient(test_app) as client:
            assert client.get("/api/health").status_code == 200
            document_columns = {
                column["name"] for column in inspect(engine).get_columns("documents")
            }
            assert "embedding_status" not in document_columns
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_db_engine(database_url)
    try:
        assert "chunks" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                HEAD_REVISION
            )
    finally:
        engine.dispose()


def test_embedding_migration_downgrade_preserves_stage_4_2_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'downgrade.db'}"
    monkeypatch.setenv("STUDYPILOT_DATABASE_URL", database_url)
    config = Config(str(BACKEND_DIRECTORY / "alembic.ini"))
    command.upgrade(config, CHUNK_REVISION)

    engine = create_db_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO knowledge_bases
                        (id, name, description, created_at, updated_at)
                    VALUES
                        (7, 'Migration KB', 'must survive', :created, :updated)
                    """
                ),
                {
                    "created": datetime(2026, 9, 12, 8, 0),
                    "updated": datetime(2026, 9, 12, 8, 1),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO documents
                        (
                            id, knowledge_base_id, filename, original_filename,
                            file_type, file_size, status, created_at, updated_at,
                            parse_error, parsed_at
                        )
                    VALUES
                        (
                            11, 7, 'stored-name.txt', 'original-name.txt',
                            'txt', 123, 'parsed', :created, :updated,
                            NULL, :parsed_at
                        )
                    """
                ),
                {
                    "created": datetime(2026, 9, 12, 8, 2),
                    "updated": datetime(2026, 9, 12, 8, 3),
                    "parsed_at": datetime(2026, 9, 12, 8, 4),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO document_contents
                        (
                            id, document_id, sequence, text, source_type,
                            source_start, source_end, created_at
                        )
                    VALUES
                        (
                            13, 11, 2, 'Preserved parsed content', 'line',
                            21, 21, :created
                        )
                    """
                ),
                {"created": datetime(2026, 9, 12, 8, 5)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO chunks
                        (
                            id, document_content_id, sequence, text,
                            start_offset, end_offset, created_at
                        )
                    VALUES
                        (17, 13, 3, 'eserved', 2, 9, :created)
                    """
                ),
                {"created": datetime(2026, 9, 12, 8, 6)},
            )
        with engine.connect() as connection:
            original_rows = {
                "knowledge_base": dict(
                    connection.execute(
                        text("SELECT * FROM knowledge_bases WHERE id = 7")
                    ).mappings().one()
                ),
                "document": dict(
                    connection.execute(
                        text("SELECT * FROM documents WHERE id = 11")
                    ).mappings().one()
                ),
                "document_content": dict(
                    connection.execute(
                        text("SELECT * FROM document_contents WHERE id = 13")
                    ).mappings().one()
                ),
                "chunk": dict(
                    connection.execute(
                        text("SELECT * FROM chunks WHERE id = 17")
                    ).mappings().one()
                ),
            }
    finally:
        engine.dispose()

    def assert_business_data_preserved(*, at_head: bool) -> None:
        verification_engine = create_db_engine(database_url)
        try:
            with verification_engine.connect() as connection:
                document_columns = {
                    column["name"]
                    for column in inspect(verification_engine).get_columns("documents")
                }
                selected_document_columns = list(original_rows["document"])
                assert dict(
                    connection.execute(
                        text("SELECT * FROM knowledge_bases WHERE id = 7")
                    ).mappings().one()
                ) == original_rows["knowledge_base"]
                assert dict(
                    connection.execute(
                        text(
                            "SELECT "
                            + ", ".join(selected_document_columns)
                            + " FROM documents WHERE id = 11"
                        )
                    ).mappings().one()
                ) == original_rows["document"]
                assert dict(
                    connection.execute(
                        text("SELECT * FROM document_contents WHERE id = 13")
                    ).mappings().one()
                ) == original_rows["document_content"]
                assert dict(
                    connection.execute(
                        text("SELECT * FROM chunks WHERE id = 17")
                    ).mappings().one()
                ) == original_rows["chunk"]
                assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
                assert connection.scalar(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM chunks AS chunk
                        JOIN document_contents AS content
                            ON content.id = chunk.document_content_id
                        JOIN documents AS document
                            ON document.id = content.document_id
                        JOIN knowledge_bases AS knowledge_base
                            ON knowledge_base.id = document.knowledge_base_id
                        WHERE chunk.id = 17
                            AND content.id = 13
                            AND document.id = 11
                            AND knowledge_base.id = 7
                        """
                    )
                ) == 1
                if at_head:
                    assert "embedding_status" in document_columns
                    assert connection.scalar(
                        text(
                            "SELECT embedding_status FROM documents WHERE id = 11"
                        )
                    ) == "pending"
                else:
                    assert "embedding_status" not in document_columns
        finally:
            verification_engine.dispose()

    command.upgrade(config, "head")
    assert_business_data_preserved(at_head=True)

    command.downgrade(config, CHUNK_REVISION)
    assert_business_data_preserved(at_head=False)

    command.upgrade(config, "head")
    assert_business_data_preserved(at_head=True)
