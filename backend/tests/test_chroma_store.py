from pathlib import Path
from typing import Any

import chromadb
import pytest

from app.stores.chroma import (
    DISTANCE_SPACE,
    ChromaCollectionConfigurationError,
    ChromaRecord,
    ChromaVectorStore,
    ChromaVectorStoreError,
    build_collection_name,
)


def _record(
    generation_id: str,
    chunk_id: int,
    *,
    document_id: int = 1,
    embedding: list[float] | None = None,
) -> ChromaRecord:
    return ChromaRecord(
        id=f"{generation_id}:{chunk_id}",
        text=f"Chunk {chunk_id}",
        embedding=embedding or [0.1, 0.2, 0.3],
        metadata={
            "generation_id": generation_id,
            "document_id": document_id,
            "chunk_id": chunk_id,
        },
    )


class QueryCollection:
    def __init__(
        self,
        result: dict[str, Any] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error

    def query(self, **_: object) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def test_chroma_store_persists_explicit_embeddings_one_record_per_chunk(
    tmp_path: Path,
) -> None:
    chroma_path = tmp_path / "chroma"
    store = ChromaVectorStore(path=chroma_path, dimensions=3)
    store.add_records([_record("generation-a", 10), _record("generation-a", 11)])

    second_client = chromadb.PersistentClient(path=str(chroma_path))
    collection = second_client.get_collection(
        store.collection_name,
        embedding_function=None,
    )
    stored = collection.get(
        where={"generation_id": "generation-a"},
        include=["documents", "embeddings", "metadatas"],
    )

    assert set(stored["ids"]) == {"generation-a:10", "generation-a:11"}
    assert set(stored["documents"] or []) == {"Chunk 10", "Chunk 11"}
    assert {metadata["chunk_id"] for metadata in stored["metadatas"] or []} == {
        10,
        11,
    }
    assert stored["embeddings"] is not None
    assert len(stored["embeddings"]) == 2

    summary = store.get_generation_summary("generation-a")
    assert summary.record_count == 2
    assert summary.chunk_ids == frozenset({10, 11})


def test_chroma_store_searches_by_cosine_distance(tmp_path: Path) -> None:
    store = ChromaVectorStore(
        path=tmp_path / "chroma",
        dimensions=3,
    )
    store.add_records(
        [
            _record("active", 1, embedding=[1.0, 0.0, 0.0]),
            _record("active", 2, embedding=[0.0, 1.0, 0.0]),
            _record("active", 3, embedding=[-1.0, 0.0, 0.0]),
        ]
    )

    hits = store.search(
        [1.0, 0.0, 0.0],
        allowed_record_ids={"active:1", "active:2", "active:3"},
        top_k=3,
    )

    assert [hit.record_id for hit in hits] == ["active:1", "active:2", "active:3"]
    assert [hit.document_id for hit in hits] == [1, 1, 1]
    assert [hit.chunk_id for hit in hits] == [1, 2, 3]
    assert [hit.generation_id for hit in hits] == ["active", "active", "active"]
    assert [hit.distance for hit in hits] == pytest.approx([0.0, 1.0, 2.0])


def test_chroma_search_is_limited_to_allowed_record_ids(tmp_path: Path) -> None:
    store = ChromaVectorStore(path=tmp_path / "chroma", dimensions=3)
    store.add_records(
        [
            _record("old", 1, embedding=[1.0, 0.0, 0.0]),
            _record("active", 2, embedding=[0.0, 1.0, 0.0]),
        ]
    )

    hits = store.search(
        [1.0, 0.0, 0.0],
        allowed_record_ids={"active:2"},
        top_k=5,
    )

    assert [hit.record_id for hit in hits] == ["active:2"]


def test_chroma_search_respects_top_k(tmp_path: Path) -> None:
    store = ChromaVectorStore(path=tmp_path / "chroma", dimensions=3)
    store.add_records(
        [
            _record("active", 1, embedding=[1.0, 0.0, 0.0]),
            _record("active", 2, embedding=[0.8, 0.2, 0.0]),
            _record("active", 3, embedding=[0.0, 1.0, 0.0]),
        ]
    )

    hits = store.search(
        [1.0, 0.0, 0.0],
        allowed_record_ids={"active:1", "active:2", "active:3"},
        top_k=2,
    )

    assert [hit.record_id for hit in hits] == ["active:1", "active:2"]


def test_chroma_search_returns_empty_when_allowed_records_are_absent(
    tmp_path: Path,
) -> None:
    store = ChromaVectorStore(path=tmp_path / "chroma", dimensions=3)

    assert (
        store.search(
            [1.0, 0.0, 0.0],
            allowed_record_ids={"missing:1"},
            top_k=5,
        )
        == []
    )


def test_search_store_does_not_create_a_missing_collection(tmp_path: Path) -> None:
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))

    with pytest.raises(
        ChromaVectorStoreError,
        match="embedding collection does not exist",
    ):
        ChromaVectorStore(
            path=tmp_path / "chroma",
            dimensions=3,
            create_if_missing=False,
            client=client,
        )

    assert client.list_collections() == []


def test_chroma_search_rejects_invalid_metadata(tmp_path: Path) -> None:
    store = ChromaVectorStore(path=tmp_path / "chroma", dimensions=3)
    store._collection = QueryCollection(
        {
            "ids": [["active:1"]],
            "metadatas": [
                [{"document_id": 1, "chunk_id": 2, "generation_id": "active"}]
            ],
            "distances": [[0.1]],
        }
    )

    with pytest.raises(ChromaVectorStoreError, match="search could not be completed"):
        store.search(
            [1.0, 0.0, 0.0],
            allowed_record_ids={"active:1"},
            top_k=1,
        )


def test_chroma_search_rejects_non_finite_distance(tmp_path: Path) -> None:
    store = ChromaVectorStore(path=tmp_path / "chroma", dimensions=3)
    store._collection = QueryCollection(
        {
            "ids": [["active:1"]],
            "metadatas": [
                [{"document_id": 1, "chunk_id": 1, "generation_id": "active"}]
            ],
            "distances": [[float("nan")]],
        }
    )

    with pytest.raises(ChromaVectorStoreError, match="search could not be completed"):
        store.search(
            [1.0, 0.0, 0.0],
            allowed_record_ids={"active:1"},
            top_k=1,
        )


def test_chroma_search_converts_chroma_errors(tmp_path: Path) -> None:
    store = ChromaVectorStore(path=tmp_path / "chroma", dimensions=3)
    store._collection = QueryCollection(error=RuntimeError("raw Chroma error"))

    with pytest.raises(ChromaVectorStoreError, match="search could not be completed"):
        store.search(
            [1.0, 0.0, 0.0],
            allowed_record_ids={"active:1"},
            top_k=1,
        )


@pytest.mark.parametrize(
    "query_embedding",
    [
        [1.0, 2.0],
        [1.0, True, 3.0],
        [1.0, float("inf"), 3.0],
    ],
)
def test_chroma_search_validates_query_embedding(
    tmp_path: Path,
    query_embedding: list[object],
) -> None:
    store = ChromaVectorStore(path=tmp_path / "chroma", dimensions=3)

    with pytest.raises(ChromaVectorStoreError, match="search could not be completed"):
        store.search(
            query_embedding,  # type: ignore[arg-type]
            allowed_record_ids={"active:1"},
            top_k=1,
        )


def test_chroma_store_deletes_only_requested_generation(tmp_path: Path) -> None:
    chroma_path = tmp_path / "chroma"
    store = ChromaVectorStore(path=chroma_path, dimensions=3)
    store.add_records([_record("old", 1), _record("current", 1)])

    store.delete_generation("old")

    collection = chromadb.PersistentClient(path=str(chroma_path)).get_collection(
        store.collection_name,
        embedding_function=None,
    )
    assert collection.get()["ids"] == ["current:1"]


def test_chroma_store_deletes_all_records_for_only_requested_document(
    tmp_path: Path,
) -> None:
    chroma_path = tmp_path / "chroma"
    store = ChromaVectorStore(path=chroma_path, dimensions=3)
    store.add_records(
        [
            _record("old", 1),
            _record("current", 2),
            _record("other", 3, document_id=2),
        ]
    )
    assert store.get_document_record_count(1) == 2
    assert store.get_document_record_count(2) == 1

    store.delete_document_records(1)

    collection = chromadb.PersistentClient(path=str(chroma_path)).get_collection(
        store.collection_name,
        embedding_function=None,
    )
    assert collection.get()["ids"] == ["other:3"]
    assert store.get_document_record_count(1) == 0
    assert store.get_document_record_count(2) == 1


def test_persistent_collection_is_reopened_with_verified_cosine_configuration(
    tmp_path: Path,
) -> None:
    chroma_path = tmp_path / "chroma"
    first_store = ChromaVectorStore(path=chroma_path, dimensions=3)
    first_store.add_records([_record("generation-a", 1)])

    reopened_store = ChromaVectorStore(path=chroma_path, dimensions=3)
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(
        reopened_store.collection_name,
        embedding_function=None,
    )

    assert reopened_store.collection_name == first_store.collection_name
    assert collection.configuration_json["hnsw"]["space"] == DISTANCE_SPACE
    assert collection.metadata == {
        "provider": "dashscope",
        "model": "text-embedding-v4",
        "dimensions": 3,
        "schema_version": 1,
        "distance": "cosine",
    }
    assert collection.get()["ids"] == ["generation-a:1"]


def test_model_and_dimensions_create_distinct_collection_identities(
    tmp_path: Path,
) -> None:
    chroma_path = tmp_path / "chroma"
    default_store = ChromaVectorStore(
        path=chroma_path,
        model="text-embedding-v4",
        dimensions=1024,
    )
    other_model_store = ChromaVectorStore(
        path=chroma_path,
        model="another/model@v1",
        dimensions=1024,
    )
    other_dimension_store = ChromaVectorStore(
        path=chroma_path,
        model="text-embedding-v4",
        dimensions=768,
    )

    names = {
        default_store.collection_name,
        other_model_store.collection_name,
        other_dimension_store.collection_name,
    }
    assert len(names) == 3
    assert all(name.replace("_", "").isalnum() for name in names)
    assert {collection.name for collection in chromadb.PersistentClient(
        path=str(chroma_path)
    ).list_collections()} == names


@pytest.mark.parametrize(
    ("configuration", "metadata", "message"),
    [
        (
            {"hnsw": {"space": "l2"}},
            {
                "provider": "dashscope",
                "model": "text-embedding-v4",
                "dimensions": 3,
                "schema_version": 1,
                "distance": "cosine",
            },
            "distance space must be cosine",
        ),
        (
            {"hnsw": {"space": "cosine"}},
            {
                "provider": "dashscope",
                "model": "wrong-model",
                "dimensions": 3,
                "schema_version": 1,
                "distance": "cosine",
            },
            "metadata does not match",
        ),
    ],
)
def test_existing_collection_configuration_mismatch_fails_explicitly(
    tmp_path: Path,
    configuration: dict[str, object],
    metadata: dict[str, str | int],
    message: str,
) -> None:
    chroma_path = tmp_path / message.replace(" ", "-")
    collection_name = build_collection_name(
        provider="dashscope",
        model="text-embedding-v4",
        dimensions=3,
        schema_version=1,
    )
    chromadb.PersistentClient(path=str(chroma_path)).create_collection(
        name=collection_name,
        configuration=configuration,
        metadata=metadata,
        embedding_function=None,
    )

    with pytest.raises(ChromaCollectionConfigurationError, match=message):
        ChromaVectorStore(path=chroma_path, dimensions=3)
