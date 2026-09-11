from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, text

from app.database import create_db_engine


BACKEND_DIRECTORY = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "0001_stage_2_baseline"
HEAD_REVISION = "0002_stage_3_2_document_content"


def test_stage_3_2_migration_upgrades_baseline_database(
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

    command.upgrade(config, "head")

    engine = create_db_engine(database_url)
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == {
            "alembic_version",
            "document_contents",
            "documents",
            "knowledge_bases",
        }

        document_columns = {
            column["name"]: column for column in inspector.get_columns("documents")
        }
        assert document_columns["parse_error"]["nullable"] is True
        assert document_columns["parsed_at"]["nullable"] is True

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

        with engine.connect() as connection:
            migrated_document = connection.execute(
                text(
                    """
                    SELECT status, parse_error, parsed_at
                    FROM documents
                    WHERE id = 1
                    """
                )
            ).one()
            assert migrated_document == ("pending", None, None)
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                HEAD_REVISION
            )
    finally:
        engine.dispose()
