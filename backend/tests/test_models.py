import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
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
from app.models import Document, KnowledgeBase


def test_default_database_url_uses_stable_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STUDYPILOT_DATABASE_URL", raising=False)

    assert get_database_url() == DEFAULT_DATABASE_URL
    assert DATABASE_PATH.is_absolute()
    assert Path(make_url(DEFAULT_DATABASE_URL).database or "") == DATABASE_PATH


def test_database_contains_stage_1_tables(test_engine: Engine) -> None:
    inspector = inspect(test_engine)

    assert set(inspector.get_table_names()) == {"documents", "knowledge_bases"}

    document_foreign_keys = inspector.get_foreign_keys("documents")
    assert len(document_foreign_keys) == 1
    assert document_foreign_keys[0]["constrained_columns"] == ["knowledge_base_id"]
    assert document_foreign_keys[0]["referred_table"] == "knowledge_bases"
    assert document_foreign_keys[0]["referred_columns"] == ["id"]
    assert document_foreign_keys[0]["options"]["ondelete"] == "CASCADE"


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


def test_lifespan_uses_environment_database_and_creates_tables(
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
            assert set(inspect(temporary_engine).get_table_names()) == {
                "documents",
                "knowledge_bases",
            }

        assert database_path.exists()
        with temporary_engine.connect() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
    finally:
        temporary_engine.dispose()
