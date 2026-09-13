from pathlib import Path

import chromadb
import pytest

from app.stores.chroma import (
    DISTANCE_SPACE,
    ChromaCollectionConfigurationError,
    ChromaRecord,
    ChromaVectorStore,
    build_collection_name,
)


def _record(generation_id: str, chunk_id: int) -> ChromaRecord:
    return ChromaRecord(
        id=f"{generation_id}:{chunk_id}",
        text=f"Chunk {chunk_id}",
        embedding=[0.1, 0.2, 0.3],
        metadata={
            "generation_id": generation_id,
            "document_id": 1,
            "chunk_id": chunk_id,
        },
    )


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
