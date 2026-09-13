from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Sequence
from numbers import Real
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_EMBEDDING_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
    "text-embedding/text-embedding"
)
DEFAULT_MODEL = "text-embedding-v4"
DEFAULT_DIMENSION = 1024
MAX_BATCH_SIZE = 10


class DashScopeEmbeddingError(RuntimeError):
    """A safe, provider-facing embedding error."""


class DashScopeEmbeddingConfigurationError(DashScopeEmbeddingError):
    pass


class DashScopeTextEmbedding:
    """Small synchronous adapter for DashScope's native text embedding API."""

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = DEFAULT_EMBEDDING_URL,
        model: str = DEFAULT_MODEL,
        dimension: int = DEFAULT_DIMENSION,
        timeout_seconds: float = 30.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds
        self._opener = opener

    @classmethod
    def from_environment(cls) -> DashScopeTextEmbedding:
        return cls(
            api_key=os.environ.get("DASHSCOPE_API_KEY", "").strip(),
            endpoint=(
                os.environ.get("DASHSCOPE_EMBEDDING_URL", DEFAULT_EMBEDDING_URL)
                .strip()
                or DEFAULT_EMBEDDING_URL
            ),
            model=(
                os.environ.get("STUDYPILOT_EMBEDDING_MODEL", DEFAULT_MODEL).strip()
                or DEFAULT_MODEL
            ),
        )

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            raise DashScopeEmbeddingConfigurationError(
                "DASHSCOPE_API_KEY is not configured"
            )

        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH_SIZE):
            batch = list(texts[start : start + MAX_BATCH_SIZE])
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        request_body = {
            "model": self.model,
            "input": {"texts": texts},
            "parameters": {
                "text_type": "document",
                "dimension": self.dimension,
                "output_type": "dense",
            },
        }
        request = Request(
            self.endpoint,
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise DashScopeEmbeddingError(
                f"DashScope embedding request failed with HTTP {exc.code}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise DashScopeEmbeddingError(
                "DashScope embedding request could not be completed"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DashScopeEmbeddingError(
                "DashScope returned an invalid embedding response"
            ) from exc

        return self._parse_vectors(payload, expected_count=len(texts))

    def _parse_vectors(
        self,
        payload: object,
        *,
        expected_count: int,
    ) -> list[list[float]]:
        try:
            if not isinstance(payload, dict):
                raise TypeError
            output = payload["output"]
            if not isinstance(output, dict):
                raise TypeError
            embeddings = output["embeddings"]
            if not isinstance(embeddings, list):
                raise TypeError

            vectors_by_index: dict[int, list[float]] = {}
            for item in embeddings:
                if not isinstance(item, dict):
                    raise TypeError
                text_index = item["text_index"]
                raw_vector = item["embedding"]
                if not isinstance(text_index, int) or isinstance(text_index, bool):
                    raise TypeError
                if not isinstance(raw_vector, list):
                    raise TypeError
                vector = self._validate_vector(raw_vector)
                if text_index in vectors_by_index:
                    raise ValueError
                vectors_by_index[text_index] = vector

            if set(vectors_by_index) != set(range(expected_count)):
                raise ValueError
            return [vectors_by_index[index] for index in range(expected_count)]
        except (KeyError, TypeError, ValueError) as exc:
            raise DashScopeEmbeddingError(
                "DashScope returned an invalid embedding response"
            ) from exc

    def _validate_vector(self, raw_vector: list[object]) -> list[float]:
        if len(raw_vector) != self.dimension:
            raise ValueError

        vector: list[float] = []
        for value in raw_vector:
            if not isinstance(value, Real) or isinstance(value, bool):
                raise TypeError
            converted = float(value)
            if not math.isfinite(converted):
                raise ValueError
            vector.append(converted)
        return vector


def get_embedding_model() -> DashScopeTextEmbedding:
    return DashScopeTextEmbedding.from_environment()
