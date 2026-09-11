import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, create_engine as sqlalchemy_create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import Pool


DATA_DIRECTORY = Path(__file__).resolve().parents[1] / "data"
DATABASE_PATH = DATA_DIRECTORY / "studypilot.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
DATABASE_URL_ENVIRONMENT_VARIABLE = "STUDYPILOT_DATABASE_URL"


class Base(DeclarativeBase):
    pass


def get_model_metadata() -> MetaData:
    """Load every SQLAlchemy model and return the shared migration metadata."""
    from app import models  # noqa: F401

    return Base.metadata


def _enable_sqlite_foreign_keys(
    dbapi_connection: Any,
    _: Any,
) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_database_url() -> str:
    return os.environ.get(
        DATABASE_URL_ENVIRONMENT_VARIABLE,
        DEFAULT_DATABASE_URL,
    )


def create_db_engine(
    database_url: str | None = None,
    *,
    poolclass: type[Pool] | None = None,
) -> Engine:
    url = make_url(database_url if database_url is not None else get_database_url())
    if url.get_backend_name() != "sqlite":
        raise ValueError("StudyPilot V1 only supports SQLite database URLs")

    engine_options: dict[str, Any] = {
        "connect_args": {"check_same_thread": False},
    }
    if poolclass is not None:
        engine_options["poolclass"] = poolclass

    db_engine = sqlalchemy_create_engine(url, **engine_options)
    event.listen(db_engine, "connect", _enable_sqlite_foreign_keys)
    return db_engine


DATABASE_URL = get_database_url()
engine = create_db_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def init_db(db_engine: Engine = engine) -> None:
    database_path = db_engine.url.database
    if database_path and database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    get_model_metadata().create_all(bind=db_engine)
