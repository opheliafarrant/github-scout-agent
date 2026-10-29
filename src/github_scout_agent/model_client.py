"""Narrow OpenAI-compatible Chat Completions Tool Calling adapter.

This adapter is intentionally synchronous and does not retry. The Agent Loop
owns model-call budgets; this module owns one bounded HTTP request and protocol
normalization. No real endpoint is contacted by S5-01 tests.
"""

from __future__ import annotations

import copy
import json
import re
import ssl
import time
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import SecretStr, ValidationError

from .agent_models import AgentMessage, AssistantResponse, ModelCallError, ModelUsage, ToolCall
from .budget import BudgetExceeded, RequestBudget
from .config import AppConfig, is_loopback_endpoint


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


_SAFE_ERRNO_WHITELIST = frozenset({104, 110, 111, 101, 10051, 10054, 10060, 10061, 11001})

_SECRET_LIKE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


def _mask_secrets(text: str) -> str:
    """Mask anything that looks like a provider key before it reaches a log or the UI."""

    return _SECRET_LIKE.sub("sk-***", text)


def _upstream_message(content: bytes) -> str | None:
    """Extract the provider's own error message so a 401 says *why* it failed."""

    try:
        payload = json.loads(content[:8192])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    error = payload.get("error") if isinstance(payload, Mapping) else None
    message: object = None
    if isinstance(error, Mapping):
        message = error.get("message") or error.get("code")
    elif isinstance(error, str):
        message = error
    if not isinstance(message, str):
        return None
    cleaned = " ".join(_mask_secrets(message).split())
    return cleaned[:240] or None


def _models_url(base_url: str) -> str:
    """Derive the OpenAI-compatible /models URL from a chat-completions base URL."""

    parsed = urlsplit(_validate_base_url(base_url))
    path = parsed.path[: -len("/chat/completions")] if parsed.path.endswith("/chat/completions") else parsed.path
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/models" if path else "/models", "", ""))


def _safe_network_error_detail(exc: httpx.RequestError) -> str:
    """Classify network errors safely by type and numerical errno without persisting raw text."""

    if isinstance(exc, httpx.ProxyError):
        return "ProxyError"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "RemoteProtocolError"
    if isinstance(exc, httpx.ConnectError):
        cause = getattr(exc, "__cause__", None)
        if cause is not None:
            cause_type = type(cause).__name__
            if cause_type == "SSLCertVerificationError":
                return "ConnectError[tls_certificate]"
            if cause_type == "SSLEOFError":
                return "ConnectError[tls_unexpected_eof]"
            if isinstance(cause, ssl.SSLError) or "SSL" in cause_type:
                return "ConnectError[tls]"
            errno_val = getattr(cause, "winerror", None) or getattr(cause, "errno", None)
            if isinstance(errno_val, int) and errno_val in _SAFE_ERRNO_WHITELIST:
                return f"ConnectError[errno={errno_val}]"
        errno_val = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
        if isinstance(errno_val, int) and errno_val in _SAFE_ERRNO_WHITELIST:
            return f"ConnectError[errno={errno_val}]"
        return "ConnectError"
    if isinstance(exc, httpx.ReadError):
        return "ReadError"
    if isinstance(exc, httpx.WriteError):
        return "WriteError"
    if isinstance(exc, httpx.ProtocolError):
        return "ProtocolError"
    return "RequestError"


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
        max_retries: int = 0,
        thinking_mode: Literal["disabled", "enabled"] | None = None,
        strict_tools: bool = True,
        max_tokens: int | None = None,
    ) -> None:
        if not model.strip():
            raise ModelAdapterError("model ID is required", code="invalid_model", category="configuration")
        if timeout <= 0:
            raise ModelAdapterError("model timeout must be positive", code="invalid_timeout", category="configuration")
        if max_tokens is not None and max_tokens <= 0:
            raise ModelAdapterError("max_tokens must be positive", code="invalid_max_tokens", category="configuration")
        self.endpoint = _validate_base_url(base_url)
        self.model = model
        self.thinking_mode = thinking_mode
        self.strict_tools = strict_tools
        self.max_tokens = max_tokens
        self.max_retries = max(0, max_retries)
        self._api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not self._api_key and not is_loopback_endpoint(self.endpoint):
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
        max_retries: int = 0,
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
            max_retries=max_retries,
            thinking_mode=config.thinking_mode,
            strict_tools=config.strict_tools,
            max_tokens=config.max_tokens,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OpenAICompatibleClient":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def complete(
        self,
        messages: list[AgentMessage],
        tools: list[dict[str, object]],
        *,
        tool_choice: Literal["auto", "none"] = "auto",
    ) -> AssistantResponse:
        self.budget.check_task_time()
        try:
            payload = self.build_payload(messages, tools, tool_choice=tool_choice)
        except (TypeError, ValueError) as exc:
            raise ModelAdapterError("unable to construct model request", code="invalid_request", category="protocol") from exc

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        for attempt in range(1, self.max_retries + 2):
            self.budget.check_task_time()
            if attempt > 1:
                time.sleep(min(1.0 * (2 ** (attempt - 2)), 4.0))
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

                if status_code < 200 or status_code >= 300:
                    err = self._http_error(status_code, content)
                    if err.retryable and attempt <= self.max_retries:
                        continue
                    raise err

                return self._parse_response(content)

            except ModelAdapterError as exc:
                if exc.retryable and attempt <= self.max_retries:
                    continue
                raise
            except httpx.TimeoutException as exc:
                if attempt <= self.max_retries:
                    continue
                raise ModelAdapterError("model request timed out", code="timeout", category="timeout", retryable=True) from exc
            except httpx.RequestError as exc:
                if attempt <= self.max_retries:
                    continue
                detail = _safe_network_error_detail(exc)
                raise ModelAdapterError(f"model network request failed ({detail})", code="network_error", category="network", retryable=True) from exc

        raise ModelAdapterError("model request failed after retries", code="network_error", category="network", retryable=True)

    def build_payload(
        self,
        messages: list[AgentMessage],
        tools: list[dict[str, object]],
        *,
        tool_choice: Literal["auto", "none"] = "auto",
    ) -> dict[str, object]:
        """Build the exact non-network request body for preflight and tests."""

        safe_tools = copy.deepcopy(tools)
        if not self.strict_tools:
            for tool in safe_tools:
                function = tool.get("function") if isinstance(tool, Mapping) else None
                if isinstance(function, dict):
                    function.pop("strict", None)
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [self._message_to_wire(message) for message in messages],
            "tools": safe_tools,
            "tool_choice": tool_choice,
            "stream": False,
        }
        if self.thinking_mode is not None:
            payload["thinking"] = {"type": self.thinking_mode}
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        return payload

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
        detail = _upstream_message(content)
        suffix = f": {detail}" if detail else ""
        if status_code in {401, 403}:
            return ModelAdapterError(
                f"model authentication or permission failed{suffix}", code="authentication_error", category="auth", status_code=status_code
            )
        if status_code == 429:
            return ModelAdapterError(
                f"model endpoint rate limited the request{suffix}", code="rate_limited", category="rate_limit", status_code=status_code, retryable=True
            )
        return ModelAdapterError(
            f"model endpoint returned an error{suffix}",
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


def list_model_ids(
    base_url: str,
    api_key: str | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 15.0,
    max_models: int = 500,
) -> list[str]:
    """Fetch the provider's advertised model ids from the OpenAI-compatible /models route.

    This is a settings-panel diagnostic: it never touches the task budget and it
    reports the provider's own words (masked) so a rejected key is diagnosable.
    """

    url = _models_url(base_url)
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=False) as client:
            response = client.get(url, headers=headers)
            content = response.content[:1_000_000]
            status_code = response.status_code
    except httpx.TimeoutException as exc:
        raise ModelAdapterError(
            "listing models timed out", code="model_list_timeout", category="timeout", retryable=True
        ) from exc
    except httpx.RequestError as exc:
        raise ModelAdapterError(
            f"listing models failed ({_safe_network_error_detail(exc)})", code="model_list_network", category="network", retryable=True
        ) from exc

    detail = _upstream_message(content)
    suffix = f": {detail}" if detail else ""
    if status_code in {401, 403}:
        raise ModelAdapterError(
            f"model endpoint rejected the API key{suffix}", code="model_list_auth", category="auth", status_code=status_code
        )
    if status_code in {404, 405, 501}:
        raise ModelAdapterError(
            "model endpoint does not expose a /models route", code="model_list_unsupported", category="compatibility", status_code=status_code
        )
    if status_code < 200 or status_code >= 300:
        raise ModelAdapterError(
            f"model endpoint returned HTTP {status_code}{suffix}",
            code="model_list_http_error",
            category="upstream" if status_code >= 500 else "protocol",
            status_code=status_code,
            retryable=status_code >= 500,
        )
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ModelAdapterError("model list is not valid JSON", code="model_list_payload", category="protocol") from exc

    entries = payload.get("data") if isinstance(payload, Mapping) else None
    ids: list[str] = []
    seen: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        identifier = entry.get("id") if isinstance(entry, Mapping) else entry
        if isinstance(identifier, str) and identifier.strip() and identifier not in seen:
            seen.add(identifier)
            ids.append(identifier)
        if len(ids) >= max_models:
            break
    if not ids:
        raise ModelAdapterError("model endpoint returned no models", code="model_list_empty", category="protocol")
    return ids
