from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.errors import NotFoundError

from app.embeddings.dashscope import DEFAULT_DIMENSION, DEFAULT_MODEL


CHROMA_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "chroma"
EMBEDDING_PROVIDER = "dashscope"
VECTOR_SCHEMA_VERSION = 1
DISTANCE_SPACE = "cosine"


class ChromaVectorStoreError(RuntimeError):
    pass


class ChromaCollectionConfigurationError(ChromaVectorStoreError):
    pass


@dataclass(frozen=True)
class ChromaRecord:
    id: str
    text: str
    embedding: list[float]
    metadata: dict[str, str | int | float | bool]


@dataclass(frozen=True)
class ChromaGenerationSummary:
    record_count: int
    chunk_ids: frozenset[int]


def _collection_component(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "unknown"


def build_collection_name(
    *,
    provider: str,
    model: str,
    dimensions: int,
    schema_version: int,
) -> str:
    """Build a stable, Chroma-safe identity for one embedding vector space."""
    identity = {
        "provider": provider,
        "model": model,
        "dimensions": dimensions,
        "schema_version": schema_version,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return (
        f"studypilot_chunks_v{schema_version}_"
        f"{_collection_component(provider)[:24]}_"
        f"{_collection_component(model)[:64]}_"
        f"{dimensions}_{digest}"
    )


class ChromaVectorStore:
    """Chroma persistence boundary; callers always supply embeddings."""

    def __init__(
        self,
        *,
        path: Path,
        provider: str = EMBEDDING_PROVIDER,
        model: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIMENSION,
        schema_version: int = VECTOR_SCHEMA_VERSION,
        client: Any | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.dimensions = dimensions
        self.schema_version = schema_version
        self.collection_name = build_collection_name(
            provider=provider,
            model=model,
            dimensions=dimensions,
            schema_version=schema_version,
        )
        self._expected_metadata: dict[str, str | int] = {
            "provider": provider,
            "model": model,
            "dimensions": dimensions,
            "schema_version": schema_version,
            "distance": DISTANCE_SPACE,
        }

        try:
            self._client = client or chromadb.PersistentClient(path=str(path))
            self._collection = self._open_or_create_collection()
            self._validate_collection_configuration()
        except ChromaVectorStoreError:
            raise
        except Exception as exc:
            raise ChromaVectorStoreError(
                "Chroma vector storage could not be opened"
            ) from exc

    def _open_or_create_collection(self) -> Any:
        try:
            return self._client.get_collection(
                name=self.collection_name,
                embedding_function=None,
            )
        except NotFoundError:
            return self._client.create_collection(
                name=self.collection_name,
                configuration={"hnsw": {"space": DISTANCE_SPACE}},
                metadata=self._expected_metadata,
                embedding_function=None,
            )

    def _validate_collection_configuration(self) -> None:
        configuration = self._collection.configuration_json
        hnsw_configuration = configuration.get("hnsw")
        distance = (
            hnsw_configuration.get("space")
            if isinstance(hnsw_configuration, dict)
            else None
        )
        if distance != DISTANCE_SPACE:
            raise ChromaCollectionConfigurationError(
                "Chroma collection distance space must be cosine"
            )
        if self._collection.metadata != self._expected_metadata:
            raise ChromaCollectionConfigurationError(
                "Chroma collection metadata does not match the embedding space"
            )

    def add_records(self, records: list[ChromaRecord]) -> None:
        if not records:
            return
        if any(len(record.embedding) != self.dimensions for record in records):
            raise ChromaVectorStoreError(
                "Document embedding dimensions do not match the Chroma collection"
            )
        try:
            batch_size = self._client.get_max_batch_size()
            for start in range(0, len(records), batch_size):
                batch = records[start : start + batch_size]
                self._collection.add(
                    ids=[record.id for record in batch],
                    embeddings=[record.embedding for record in batch],
                    documents=[record.text for record in batch],
                    metadatas=[record.metadata for record in batch],
                )
        except Exception as exc:
            raise ChromaVectorStoreError(
                "Document embeddings could not be saved to Chroma"
            ) from exc

    def get_generation_summary(
        self,
        generation_id: str,
    ) -> ChromaGenerationSummary:
        try:
            result = self._collection.get(
                where={"generation_id": generation_id},
                include=["metadatas"],
            )
            metadatas = result.get("metadatas")
            if not isinstance(metadatas, list):
                raise TypeError

            chunk_ids: set[int] = set()
            for metadata in metadatas:
                if not isinstance(metadata, dict):
                    raise TypeError
                chunk_id = metadata.get("chunk_id")
                if not isinstance(chunk_id, int) or isinstance(chunk_id, bool):
                    raise TypeError
                chunk_ids.add(chunk_id)
            return ChromaGenerationSummary(
                record_count=len(result.get("ids") or []),
                chunk_ids=frozenset(chunk_ids),
            )
        except Exception as exc:
            raise ChromaVectorStoreError(
                "Chroma embedding generation could not be verified"
            ) from exc

    def delete_generation(self, generation_id: str) -> None:
        try:
            self._collection.delete(where={"generation_id": generation_id})
        except Exception as exc:
            raise ChromaVectorStoreError(
                "A Chroma embedding generation could not be removed"
            ) from exc


def get_chroma_path() -> Path:
    configured_path = os.environ.get("STUDYPILOT_CHROMA_PATH")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return CHROMA_DIRECTORY


def get_embedding_model_name() -> str:
    return (
        os.environ.get("STUDYPILOT_EMBEDDING_MODEL", DEFAULT_MODEL).strip()
        or DEFAULT_MODEL
    )


def get_vector_store() -> ChromaVectorStore:
    return ChromaVectorStore(
        path=get_chroma_path(),
        provider=EMBEDDING_PROVIDER,
        model=get_embedding_model_name(),
        dimensions=DEFAULT_DIMENSION,
        schema_version=VECTOR_SCHEMA_VERSION,
    )


def get_vector_store_factory() -> Callable[[], ChromaVectorStore]:
    """Return a lazy factory so request preconditions run before Chroma opens."""
    return get_vector_store
