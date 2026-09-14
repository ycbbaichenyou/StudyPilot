from __future__ import annotations

import json
from io import BytesIO
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from app.llm import (
    DashScopeLLM,
    DashScopeLLMConfigurationError,
    DashScopeLLMError,
    LLMMessage,
)
from app.llm.dashscope import DEFAULT_LLM_MODEL, DEFAULT_LLM_URL


class JsonResponse(BytesIO):
    def __enter__(self) -> JsonResponse:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def valid_response(content: str = "增长率表示相对变化。[1]") -> bytes:
    return json.dumps(
        {
            "output": {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": content,
                        },
                    }
                ]
            }
        }
    ).encode()


def test_dashscope_llm_sends_messages_and_returns_text() -> None:
    requests: list[dict[str, Any]] = []

    def opener(request: Request, *, timeout: float) -> JsonResponse:
        requests.append(
            {
                "url": request.full_url,
                "body": json.loads(request.data or b"{}"),
                "authorization": request.get_header("Authorization"),
                "timeout": timeout,
            }
        )
        return JsonResponse(valid_response("  增长率表示相对变化。[1]  "))

    adapter = DashScopeLLM(api_key="test-key", opener=opener)
    messages = (
        LLMMessage(role="system", content="system rules"),
        LLMMessage(role="user", content="question and context"),
    )

    answer = adapter.generate(messages)

    assert answer == "增长率表示相对变化。[1]"
    assert requests == [
        {
            "url": DEFAULT_LLM_URL,
            "body": {
                "model": "qwen-plus",
                "input": {
                    "messages": [
                        {"role": "system", "content": "system rules"},
                        {"role": "user", "content": "question and context"},
                    ]
                },
                "parameters": {"result_format": "message"},
            },
            "authorization": "Bearer test-key",
            "timeout": 60.0,
        }
    ]
    assert "tools" not in requests[0]["body"]


def test_dashscope_llm_reads_model_and_key_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", " environment-key ")
    monkeypatch.setenv("DASHSCOPE_LLM_MODEL", " custom-qwen ")

    adapter = DashScopeLLM.from_environment()

    assert adapter.api_key == "environment-key"
    assert adapter.model == "custom-qwen"


def test_dashscope_llm_uses_qwen_plus_when_model_environment_is_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_LLM_MODEL", "   ")

    adapter = DashScopeLLM.from_environment()

    assert adapter.model == DEFAULT_LLM_MODEL == "qwen-plus"


def test_dashscope_llm_requires_api_key_without_making_request() -> None:
    def opener(*_: object, **__: object) -> JsonResponse:
        raise AssertionError("No HTTP request should be made")

    adapter = DashScopeLLM(api_key="", opener=opener)

    with pytest.raises(
        DashScopeLLMConfigurationError,
        match="DASHSCOPE_API_KEY is not configured",
    ):
        adapter.generate([LLMMessage(role="user", content="question")])


def test_dashscope_llm_http_error_is_converted_without_leaking_details() -> None:
    provider_body = b"provider-secret-body /private/internal/path sk-secret-test"

    def opener(*_: object, **__: object) -> JsonResponse:
        raise HTTPError(
            "https://dashscope.example.test",
            429,
            "provider error",
            None,
            BytesIO(provider_body),
        )

    adapter = DashScopeLLM(api_key="sk-secret-test", opener=opener)

    with pytest.raises(DashScopeLLMError) as error:
        adapter.generate([LLMMessage(role="user", content="question")])

    message = str(error.value)
    assert message == "DashScope LLM request failed with HTTP 429"
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
def test_dashscope_llm_network_and_timeout_errors_are_safe(
    network_error: Exception,
) -> None:
    def opener(*_: object, **__: object) -> JsonResponse:
        raise network_error

    adapter = DashScopeLLM(api_key="sk-secret-test", opener=opener)

    with pytest.raises(DashScopeLLMError) as error:
        adapter.generate([LLMMessage(role="user", content="question")])

    message = str(error.value)
    assert message == "DashScope LLM request could not be completed"
    assert "provider-secret" not in message
    assert "sk-secret-test" not in message
    assert "/private/internal/path" not in message


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"output": {}},
        {"output": {"choices": []}},
        {
            "output": {
                "choices": [
                    {"message": {"role": "user", "content": "wrong role"}}
                ]
            }
        },
        {
            "output": {
                "choices": [
                    {"message": {"role": "assistant", "content": "   "}}
                ]
            }
        },
        {
            "output": {
                "choices": [
                    {"message": {"role": "assistant", "content": "one"}},
                    {"message": {"role": "assistant", "content": "two"}},
                ]
            }
        },
    ],
)
def test_dashscope_llm_rejects_invalid_response_structure(
    payload: dict[str, object],
) -> None:
    def opener(*_: object, **__: object) -> JsonResponse:
        return JsonResponse(json.dumps(payload).encode())

    adapter = DashScopeLLM(api_key="test-key", opener=opener)

    with pytest.raises(DashScopeLLMError, match="invalid LLM response"):
        adapter.generate([LLMMessage(role="user", content="question")])


def test_dashscope_llm_rejects_invalid_json_response() -> None:
    def opener(*_: object, **__: object) -> JsonResponse:
        return JsonResponse(b"not-json provider-secret")

    adapter = DashScopeLLM(api_key="test-key", opener=opener)

    with pytest.raises(DashScopeLLMError) as error:
        adapter.generate([LLMMessage(role="user", content="question")])

    assert str(error.value) == "DashScope returned an invalid LLM response"
    assert "provider-secret" not in str(error.value)


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [LLMMessage(role="user", content="   ")],
    ],
)
def test_dashscope_llm_rejects_invalid_messages(
    messages: list[LLMMessage],
) -> None:
    adapter = DashScopeLLM(api_key="test-key")

    with pytest.raises(DashScopeLLMError, match="messages"):
        adapter.generate(messages)
