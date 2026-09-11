from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.models import Document, KnowledgeBase
from app.services import knowledge_bases as knowledge_base_service


def create_knowledge_base(
    client: TestClient,
    *,
    name: str = "Computer Science",
    description: str | None = "Core course notes",
) -> dict[str, object]:
    response = client.post(
        "/api/knowledge-bases",
        json={
            "name": name,
            "description": description,
        },
    )
    assert response.status_code == 201
    return response.json()


def assert_explicit_utc_timestamp(value: object) -> datetime:
    assert isinstance(value, str)
    assert value.endswith(("Z", "+00:00"))
    parsed_value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed_value.utcoffset() == timedelta(0)
    return parsed_value


def test_create_knowledge_base(client: TestClient) -> None:
    knowledge_base = create_knowledge_base(client)

    assert knowledge_base["id"] == 1
    assert knowledge_base["name"] == "Computer Science"
    assert knowledge_base["description"] == "Core course notes"
    assert_explicit_utc_timestamp(knowledge_base["created_at"])
    assert_explicit_utc_timestamp(knowledge_base["updated_at"])


def test_list_knowledge_bases(client: TestClient) -> None:
    first = create_knowledge_base(client, name="Computer Science")
    second = create_knowledge_base(client, name="Mathematics")

    response = client.get("/api/knowledge-bases")

    assert response.status_code == 200
    assert response.json() == [first, second]
    assert [item["id"] for item in response.json()] == [1, 2]


def test_get_knowledge_base(client: TestClient) -> None:
    created = create_knowledge_base(client)

    response = client.get(f"/api/knowledge-bases/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_database_roundtrip_keeps_api_timestamps_explicitly_utc(
    client: TestClient,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)

    with test_session_factory() as session:
        stored = session.get(KnowledgeBase, knowledge_base_id)
        assert stored is not None
        assert stored.created_at.tzinfo is None
        assert stored.updated_at.tzinfo is None

    response = client.get(f"/api/knowledge-bases/{knowledge_base_id}")

    assert response.status_code == 200
    assert_explicit_utc_timestamp(response.json()["created_at"])
    assert_explicit_utc_timestamp(response.json()["updated_at"])


def test_get_missing_knowledge_base_returns_404(client: TestClient) -> None:
    response = client.get("/api/knowledge-bases/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}


def test_delete_knowledge_base_removes_documents_and_files(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)

    documents = []
    for filename, content in [
        ("notes.pdf", b"%PDF-1.7 test content"),
        ("summary.txt", b"summary"),
    ]:
        upload_response = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/documents",
            files={"file": (filename, BytesIO(content), "application/octet-stream")},
        )
        assert upload_response.status_code == 201
        documents.append(upload_response.json())

    stored_paths = [
        upload_directory / document["filename"] for document in documents
    ]
    assert all(stored_path.is_file() for stored_path in stored_paths)

    delete_response = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")
    get_response = client.get(f"/api/knowledge-bases/{knowledge_base_id}")

    assert delete_response.status_code == 204
    assert delete_response.content == b""
    assert get_response.status_code == 404
    assert all(not stored_path.exists() for stored_path in stored_paths)

    with test_session_factory() as session:
        assert session.get(KnowledgeBase, knowledge_base_id) is None
        for document in documents:
            assert session.get(Document, document["id"]) is None


def test_delete_knowledge_base_database_failure_rolls_back_all_documents(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)
    documents = []
    for filename in ["first.txt", "second.txt"]:
        upload_response = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/documents",
            files={"file": (filename, BytesIO(filename.encode()), "text/plain")},
        )
        assert upload_response.status_code == 201
        documents.append(upload_response.json())

    stored_paths = [
        upload_directory / document["filename"] for document in documents
    ]

    with test_session_factory() as session:
        def fail_commit_after_flush() -> None:
            session.flush()
            raise RuntimeError("database delete failed")

        session.rollback = Mock(wraps=session.rollback)
        session.commit = Mock(side_effect=fail_commit_after_flush)
        with pytest.raises(RuntimeError, match="database delete failed"):
            knowledge_base_service.delete_knowledge_base(
                session,
                knowledge_base_id,
                upload_directory=upload_directory,
            )
        session.rollback.assert_called_once()

    with test_session_factory() as session:
        assert session.get(KnowledgeBase, knowledge_base_id) is not None
        for document in documents:
            assert session.get(Document, document["id"]) is not None
    assert all(stored_path.is_file() for stored_path in stored_paths)


def test_delete_knowledge_base_ignores_missing_document_file(
    client: TestClient,
    upload_directory: Path,
    test_session_factory: sessionmaker[Session],
) -> None:
    created = create_knowledge_base(client)
    knowledge_base_id = created["id"]
    assert isinstance(knowledge_base_id, int)
    upload_response = client.post(
        f"/api/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("notes.txt", BytesIO(b"notes"), "text/plain")},
    )
    assert upload_response.status_code == 201
    document = upload_response.json()
    document_id = document["id"]
    stored_path = upload_directory / document["filename"]
    stored_path.unlink()

    delete_response = client.delete(f"/api/knowledge-bases/{knowledge_base_id}")

    assert delete_response.status_code == 204
    with test_session_factory() as session:
        assert session.get(KnowledgeBase, knowledge_base_id) is None
        assert session.get(Document, document_id) is None


def test_delete_missing_knowledge_base_returns_404(client: TestClient) -> None:
    response = client.delete("/api/knowledge-bases/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}


@pytest.mark.parametrize("name", ["", "   ", "x" * 256])
def test_invalid_knowledge_base_name_returns_422(
    client: TestClient,
    name: str,
) -> None:
    response = client.post(
        "/api/knowledge-bases",
        json={"name": name, "description": None},
    )

    assert response.status_code == 422
