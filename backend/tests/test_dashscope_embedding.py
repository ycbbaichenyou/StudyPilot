from __future__ import annotations

import json
from io import BytesIO
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from app.embeddings.dashscope import (
    DashScopeEmbeddingConfigurationError,
    DashScopeEmbeddingError,
    DashScopeTextEmbedding,
    MAX_BATCH_SIZE,
)


class JsonResponse(BytesIO):
    def __enter__(self) -> JsonResponse:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def test_dashscope_adapter_batches_requests_and_restores_text_order() -> None:
    requests: list[dict[str, Any]] = []

    def opener(request: Request, *, timeout: float) -> JsonResponse:
        body = json.loads(request.data or b"{}")
        requests.append(
            {
                "body": body,
                "authorization": request.get_header("Authorization"),
                "timeout": timeout,
            }
        )
        texts = body["input"]["texts"]
        embeddings = [
            {
                "text_index": index,
                "embedding": [float(text), float(text) + 0.1, float(text) + 0.2],
            }
            for index, text in reversed(list(enumerate(texts)))
        ]
        return JsonResponse(json.dumps({"output": {"embeddings": embeddings}}).encode())

    adapter = DashScopeTextEmbedding(
        api_key="test-key",
        dimension=3,
        opener=opener,
    )
    texts = [str(index) for index in range(MAX_BATCH_SIZE + 1)]

    vectors = adapter.embed_texts(texts)

    assert vectors == [
        [float(index), float(index) + 0.1, float(index) + 0.2]
        for index in range(MAX_BATCH_SIZE + 1)
    ]
    assert [len(call["body"]["input"]["texts"]) for call in requests] == [10, 1]
    assert all(call["body"]["model"] == "text-embedding-v4" for call in requests)
    assert all(
        call["body"]["parameters"]
        == {"text_type": "document", "dimension": 3, "output_type": "dense"}
        for call in requests
    )
    assert all(call["authorization"] == "Bearer test-key" for call in requests)
    assert all(call["timeout"] == 30.0 for call in requests)


def test_dashscope_adapter_requires_api_key_without_making_request() -> None:
    def opener(*_: object, **__: object) -> JsonResponse:
        raise AssertionError("No HTTP request should be made")

    adapter = DashScopeTextEmbedding(api_key="", opener=opener)

    with pytest.raises(
        DashScopeEmbeddingConfigurationError,
        match="DASHSCOPE_API_KEY is not configured",
    ):
        adapter.embed_texts(["text"])


@pytest.mark.parametrize(
    "payload",
    [
        {"output": {"embeddings": []}},
        {
            "output": {
                "embeddings": [
                    {"text_index": 0, "embedding": [1.0, 2.0]},
                ]
            }
        },
        {
            "output": {
                "embeddings": [
                    {"text_index": 1, "embedding": [1.0, 2.0, 3.0]},
                ]
            }
        },
    ],
)
def test_dashscope_adapter_rejects_incomplete_or_invalid_responses(
    payload: dict[str, object],
) -> None:
    def opener(*_: object, **__: object) -> JsonResponse:
        return JsonResponse(json.dumps(payload).encode())

    adapter = DashScopeTextEmbedding(
        api_key="test-key",
        dimension=3,
        opener=opener,
    )

    with pytest.raises(
        DashScopeEmbeddingError,
        match="invalid embedding response",
    ):
        adapter.embed_texts(["text"])


@pytest.mark.parametrize(
    "invalid_value",
    [float("nan"), float("inf"), float("-inf"), True, "bad"],
)
def test_dashscope_adapter_rejects_non_finite_bool_and_non_numeric_values(
    invalid_value: object,
) -> None:
    payload = {
        "output": {
            "embeddings": [
                {"text_index": 0, "embedding": [1.0, invalid_value, 3.0]}
            ]
        }
    }

    def opener(*_: object, **__: object) -> JsonResponse:
        return JsonResponse(json.dumps(payload).encode())

    adapter = DashScopeTextEmbedding(
        api_key="test-key",
        dimension=3,
        opener=opener,
    )

    with pytest.raises(DashScopeEmbeddingError, match="invalid embedding response"):
        adapter.embed_texts(["text"])


def test_dashscope_adapter_uses_1024_dimensions_by_default() -> None:
    requested_dimensions: list[int] = []

    def opener(request: Request, *, timeout: float) -> JsonResponse:
        body = json.loads(request.data or b"{}")
        requested_dimensions.append(body["parameters"]["dimension"])
        return JsonResponse(
            json.dumps(
                {
                    "output": {
                        "embeddings": [
                            {"text_index": 0, "embedding": [0.0] * 1024}
                        ]
                    }
                }
            ).encode()
        )

    adapter = DashScopeTextEmbedding(api_key="test-key", opener=opener)

    vectors = adapter.embed_texts(["text"])

    assert adapter.dimension == 1024
    assert requested_dimensions == [1024]
    assert len(vectors[0]) == 1024


def test_dashscope_http_error_does_not_expose_response_body_or_api_key() -> None:
    provider_body = b"provider-secret-body /private/internal/path sk-secret-test"

    def opener(*_: object, **__: object) -> JsonResponse:
        raise HTTPError(
            "https://dashscope.example.test",
            429,
            "provider error",
            None,
            BytesIO(provider_body),
        )

    adapter = DashScopeTextEmbedding(api_key="sk-secret-test", opener=opener)

    with pytest.raises(DashScopeEmbeddingError) as error:
        adapter.embed_texts(["text"])

    message = str(error.value)
    assert message == "DashScope embedding request failed with HTTP 429"
    assert "provider-secret-body" not in message
    assert "sk-secret-test" not in message
    assert "/private/internal/path" not in message


@pytest.mark.parametrize(
    "network_error",
    [
        URLError("provider-secret-reason /private/internal/path"),
        TimeoutError("provider-secret-timeout /private/internal/path"),
    ],
)
def test_dashscope_network_errors_are_converted_to_safe_internal_errors(
    network_error: Exception,
) -> None:
    def opener(*_: object, **__: object) -> JsonResponse:
        raise network_error

    adapter = DashScopeTextEmbedding(api_key="sk-secret-test", opener=opener)

    with pytest.raises(DashScopeEmbeddingError) as error:
        adapter.embed_texts(["text"])

    message = str(error.value)
    assert message == "DashScope embedding request could not be completed"
    assert "provider-secret" not in message
    assert "sk-secret-test" not in message
    assert "/private/internal/path" not in message
