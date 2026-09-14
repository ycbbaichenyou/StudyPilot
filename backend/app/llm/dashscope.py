from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_LLM_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
    "text-generation/generation"
)
DEFAULT_LLM_MODEL = "qwen-plus"


class DashScopeLLMError(RuntimeError):
    """A safe, provider-facing text generation error."""


class DashScopeLLMConfigurationError(DashScopeLLMError):
    pass


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: Literal["system", "user", "assistant"]
    content: str


class DashScopeLLM:
    """Small synchronous adapter for DashScope's native generation API."""

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = DEFAULT_LLM_URL,
        model: str = DEFAULT_LLM_MODEL,
        timeout_seconds: float = 60.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._opener = opener

    @classmethod
    def from_environment(cls) -> DashScopeLLM:
        return cls(
            api_key=os.environ.get("DASHSCOPE_API_KEY", "").strip(),
            model=(
                os.environ.get(
                    "DASHSCOPE_LLM_MODEL",
                    DEFAULT_LLM_MODEL,
                ).strip()
                or DEFAULT_LLM_MODEL
            ),
        )

    def generate(self, messages: Sequence[LLMMessage]) -> str:
        if not self.api_key:
            raise DashScopeLLMConfigurationError(
                "DASHSCOPE_API_KEY is not configured"
            )

        serialized_messages = self._serialize_messages(messages)
        request_body = {
            "model": self.model,
            "input": {"messages": serialized_messages},
            "parameters": {"result_format": "message"},
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
            raise DashScopeLLMError(
                f"DashScope LLM request failed with HTTP {exc.code}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise DashScopeLLMError(
                "DashScope LLM request could not be completed"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DashScopeLLMError(
                "DashScope returned an invalid LLM response"
            ) from exc

        return self._parse_text(payload)

    @staticmethod
    def _serialize_messages(
        messages: Sequence[LLMMessage],
    ) -> list[dict[str, str]]:
        if not messages:
            raise DashScopeLLMError("LLM messages must not be empty")

        serialized: list[dict[str, str]] = []
        for message in messages:
            if (
                not isinstance(message, LLMMessage)
                or message.role not in {"system", "user", "assistant"}
                or not isinstance(message.content, str)
                or not message.content.strip()
            ):
                raise DashScopeLLMError("LLM messages are invalid")
            serialized.append(
                {
                    "role": message.role,
                    "content": message.content,
                }
            )
        return serialized

    @staticmethod
    def _parse_text(payload: object) -> str:
        try:
            if not isinstance(payload, dict):
                raise TypeError
            output = payload["output"]
            if not isinstance(output, dict):
                raise TypeError
            choices = output["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            if not isinstance(choice, dict):
                raise TypeError
            message = choice["message"]
            if not isinstance(message, dict):
                raise TypeError
            if message.get("role") != "assistant":
                raise ValueError
            content = message["content"]
            if not isinstance(content, str):
                raise TypeError
            answer = content.strip()
            if not answer:
                raise ValueError
            return answer
        except (KeyError, TypeError, ValueError) as exc:
            raise DashScopeLLMError(
                "DashScope returned an invalid LLM response"
            ) from exc


def get_llm_model() -> DashScopeLLM:
    return DashScopeLLM.from_environment()
