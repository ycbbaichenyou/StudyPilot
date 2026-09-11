from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, create_db_engine, get_db
from app.main import create_app


@pytest.fixture
def test_engine() -> Generator[Engine, None, None]:
    engine = create_db_engine(
        "sqlite://",
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def test_session_factory(test_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )


@pytest.fixture
def client(
    test_session_factory: sessionmaker[Session],
) -> Generator[TestClient, None, None]:
    test_app = create_app(initialize_database=False)

    def override_get_db() -> Generator[Session, None, None]:
        with test_session_factory() as session:
            yield session

    test_app.dependency_overrides[get_db] = override_get_db

    with TestClient(test_app) as test_client:
        yield test_client

    test_app.dependency_overrides.clear()
