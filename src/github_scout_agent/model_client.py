"""Narrow OpenAI-compatible Chat Completions Tool Calling adapter.

This adapter is intentionally synchronous and does not retry. The Agent Loop
owns model-call budgets; this module owns one bounded HTTP request and protocol
normalization. No real endpoint is contacted by S5-01 tests.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import SecretStr, ValidationError

from .agent_models import AgentMessage, AssistantResponse, ModelCallError, ModelUsage, ToolCall
from .budget import BudgetExceeded, RequestBudget
from .config import AppConfig


class ModelAdapterError(ModelCallError):
    """Safe structured error from the model HTTP/protocol boundary."""


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    local_http = parsed.scheme == "http" and hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme != "https" and not local_http) or not parsed.netloc:
        raise ModelAdapterError(
            "model Base URL must be HTTPS; HTTP is allowed only for localhost development",
            code="invalid_base_url",
            category="configuration",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment or ".." in parsed.path:
        raise ModelAdapterError(
            "model Base URL contains forbidden URL components",
            code="invalid_base_url",
            category="configuration",
        )
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        endpoint_path = path
    else:
        endpoint_path = f"{path}/chat/completions" if path else "/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, "", ""))


class OpenAICompatibleClient:
    """One bounded Chat Completions request adapter."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr | str | None,
        model: str,
        budget: RequestBudget | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not model.strip():
            raise ModelAdapterError("model ID is required", code="invalid_model", category="configuration")
        if timeout <= 0:
            raise ModelAdapterError("model timeout must be positive", code="invalid_timeout", category="configuration")
        self.endpoint = _validate_base_url(base_url)
        self.model = model
        self._api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not self._api_key:
            raise ModelAdapterError("model API Key is not configured", code="model_not_configured", category="configuration")
        self.budget = budget or RequestBudget()
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            transport=transport,
        )

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        budget: RequestBudget | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> "OpenAICompatibleClient":
        if not config.model_base_url or not config.model:
            raise ModelAdapterError("model Base URL and Model are required", code="model_not_configured", category="configuration")
        return cls(
            base_url=config.model_base_url,
            api_key=config.model_api_key,
            model=config.model,
            budget=budget,
            transport=transport,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OpenAICompatibleClient":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def complete(self, messages: list[AgentMessage], tools: list[dict[str, object]]) -> AssistantResponse:
        self.budget.check_task_time()
        try:
            safe_tools = copy.deepcopy(tools)
            payload = {
                "model": self.model,
                "messages": [self._message_to_wire(message) for message in messages],
                "tools": safe_tools,
                "tool_choice": "auto",
                "stream": False,
            }
        except (TypeError, ValueError) as exc:
            raise ModelAdapterError("unable to construct model request", code="invalid_request", category="protocol") from exc

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        try:
            with self._client.stream("POST", self.endpoint, headers=headers, json=payload) as response:
                content_length = response.headers.get("content-length")
                if content_length and content_length.isdigit() and self.budget.max_model_response_bytes is not None:
                    if int(content_length) > self.budget.max_model_response_bytes:
                        raise ModelAdapterError(
                            "model response exceeds the response-size limit",
                            code="response_too_large",
                            category="budget",
                        )
                chunks: list[bytes] = []
                response_size = 0
                try:
                    for chunk in response.iter_bytes():
                        response_size += len(chunk)
                        self.budget.record_model_response_chunk(len(chunk), response_size)
                        chunks.append(chunk)
                except BudgetExceeded as exc:
                    raise ModelAdapterError(
                        "model response exceeds the response-size limit",
                        code="response_too_large",
                        category="budget",
                    ) from exc
                status_code = response.status_code
                content = b"".join(chunks)
        except ModelAdapterError:
            raise
        except httpx.TimeoutException as exc:
            raise ModelAdapterError("model request timed out", code="timeout", category="timeout", retryable=True) from exc
        except httpx.RequestError as exc:
            raise ModelAdapterError("model network request failed", code="network_error", category="network", retryable=True) from exc

        if status_code < 200 or status_code >= 300:
            raise self._http_error(status_code, content)
        return self._parse_response(content)

    @staticmethod
    def _message_to_wire(message: AgentMessage) -> dict[str, object]:
        if message.role == "tool":
            if not message.tool_call_id:
                raise ValueError("tool message requires tool_call_id")
            return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
        wire: dict[str, object] = {"role": message.role, "content": message.content or None}
        if message.tool_calls:
            wire["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in message.tool_calls
            ]
        return wire

    @staticmethod
    def _http_error(status_code: int, content: bytes) -> ModelAdapterError:
        hint = content[:2048].decode("utf-8", errors="replace").lower()
        if "tool" in hint and ("not support" in hint or "unsupported" in hint):
            return ModelAdapterError(
                "model endpoint does not support Tool Calling",
                code="tool_calling_unsupported",
                category="compatibility",
                status_code=status_code,
            )
        if status_code in {401, 403}:
            return ModelAdapterError("model authentication or permission failed", code="authentication_error", category="auth", status_code=status_code)
        if status_code == 429:
            return ModelAdapterError("model endpoint rate limited the request", code="rate_limited", category="rate_limit", status_code=status_code, retryable=True)
        return ModelAdapterError(
            "model endpoint returned an error",
            code="upstream_error" if status_code >= 500 else "model_http_error",
            category="upstream" if status_code >= 500 else "protocol",
            status_code=status_code,
            retryable=status_code >= 500,
        )

    @staticmethod
    def _parse_response(content: bytes) -> AssistantResponse:
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ModelAdapterError("model response is not valid JSON", code="invalid_response", category="protocol") from exc
        if not isinstance(payload, Mapping):
            raise ModelAdapterError("model response must be a JSON object", code="invalid_response", category="protocol")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ModelAdapterError("model response is missing choices", code="invalid_response", category="protocol")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise ModelAdapterError("model response is missing assistant message", code="invalid_response", category="protocol")
        content_value = message.get("content")
        if content_value is not None and not isinstance(content_value, str):
            raise ModelAdapterError("assistant content has an unsupported shape", code="invalid_response", category="protocol")
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise ModelAdapterError("tool_calls must be an array", code="invalid_response", category="protocol")
        calls: list[ToolCall] = []
        seen_ids: set[str] = set()
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping) or raw_call.get("type") != "function":
                raise ModelAdapterError("unsupported tool call shape", code="invalid_tool_call", category="protocol")
            call_id = raw_call.get("id")
            function = raw_call.get("function")
            if not isinstance(call_id, str) or not isinstance(function, Mapping):
                raise ModelAdapterError("tool call is missing required fields", code="invalid_tool_call", category="protocol")
            name = function.get("name")
            arguments = function.get("arguments")
            if not isinstance(name, str) or not isinstance(arguments, str):
                raise ModelAdapterError("tool call function fields are invalid", code="invalid_tool_call", category="protocol")
            if call_id in seen_ids:
                raise ModelAdapterError("duplicate tool call ID", code="duplicate_tool_call_id", category="protocol")
            seen_ids.add(call_id)
            calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
        usage_value = payload.get("usage")
        usage = None
        if isinstance(usage_value, Mapping):
            try:
                usage = ModelUsage.model_validate(usage_value)
            except ValidationError:
                usage = None
        try:
            return AssistantResponse(
                content=content_value,
                tool_calls=calls,
                finish_reason=choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None,
                usage=usage,
            )
        except ValidationError as exc:
            raise ModelAdapterError("model response cannot be represented by the local protocol", code="invalid_response", category="protocol") from exc
