from __future__ import annotations

import json
import ssl

import httpx
import pytest
from pydantic import SecretStr

from github_scout_agent.agent_loop import AgentLoop
from github_scout_agent.agent_models import AgentMessage, ToolCall
from github_scout_agent.budget import RequestBudget
from github_scout_agent.config import AppConfig
from github_scout_agent.github_api import GitHubRestClient
from github_scout_agent.model_client import ModelAdapterError, OpenAICompatibleClient, list_model_ids
from github_scout_agent.tool_registry import build_github_tool_registry


BASE_URL = "https://model.example.test/v1"
MODEL_KEY = "test-model-key-not-a-credential"
GITHUB_KEY = "github-token-not-a-credential"


def _response(request: httpx.Request, payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=request)


def _client(handler, *, budget: RequestBudget | None = None, key: str = MODEL_KEY, max_retries: int = 0) -> tuple[OpenAICompatibleClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def dispatch(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = OpenAICompatibleClient(
        base_url=BASE_URL,
        api_key=SecretStr(key),
        model="mock-model",
        budget=budget,
        transport=httpx.MockTransport(dispatch),
        max_retries=max_retries,
    )
    return client, requests


def test_request_uses_standard_chat_tool_calling_shape_and_preserves_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "search_public_repositories", "arguments": '{"query":"x"}'},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        }
        return _response(request, payload)

    tools = [{"type": "function", "function": {"name": "search_public_repositories", "parameters": {}}}]
    messages = [AgentMessage(role="user", content="find x")]
    client, requests = _client(handler)
    with client:
        response = client.complete(messages, tools)

    request = requests[0]
    payload = json.loads(request.content)
    assert request.method == "POST"
    assert request.url.host == "model.example.test"
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {MODEL_KEY}"
    assert payload["model"] == "mock-model"
    assert payload["messages"] == [{"role": "user", "content": "find x"}]
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"
    assert payload["stream"] is False
    assert "parallel_tool_calls" not in payload
    assert response.tool_calls[0].id == "call-1"
    assert response.usage.total_tokens == 14


def test_tool_choice_none_is_supported_for_finalization_without_schema_rewrite() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _response(request, {"choices": [{"message": {"role": "assistant", "content": "final"}}]})

    tools = [{"type": "function", "function": {"name": "demo", "parameters": {}}}]
    client, _ = _client(handler)
    with client:
        response = client.complete([AgentMessage(role="user", content="finalize")], tools, tool_choice="none")

    assert response.content == "final"
    assert captured[0]["tool_choice"] == "none"
    assert captured[0]["tools"] == tools


def test_deepseek_preflight_non_thinking_disables_thinking_and_avoids_beta_strict() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _response(request, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    config = AppConfig(
        model_base_url="https://api.deepseek.com",
        model="deepseek-flash",
        model_api_key=SecretStr(MODEL_KEY),
        thinking_mode="disabled",
        strict_tools=False,
        max_tokens=512,
    )
    tools = [{"type": "function", "function": {"name": "demo", "strict": True, "parameters": {}}}]
    client = OpenAICompatibleClient.from_config(config, transport=httpx.MockTransport(handler))
    with client:
        client.complete([AgentMessage(role="user", content="test")], tools)

    payload = captured[0]
    assert payload["model"] == "deepseek-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 512
    assert "strict" not in payload["tools"][0]["function"]
    assert "reasoning_content" not in payload["messages"][0]
    assert payload["stream"] is False
    assert payload["tool_choice"] == "auto"


def test_multiple_tool_calls_and_followup_messages_are_preserved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(
            request,
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "done", "tool_calls": []},
                    }
                ]
            },
        )

    client, requests = _client(handler)
    messages = [
        AgentMessage(role="user", content="find"),
        AgentMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(id="a", name="one", arguments="{}"),
                ToolCall(id="b", name="two", arguments="{\"x\":1}"),
            ],
        ),
        AgentMessage(role="tool", tool_call_id="a", content='{"ok":true}'),
        AgentMessage(role="tool", tool_call_id="b", content='{"ok":true}'),
    ]
    client.complete(messages, [])
    payload = json.loads(requests[0].content)
    assert payload["messages"][1]["tool_calls"][0]["id"] == "a"
    assert payload["messages"][1]["tool_calls"][1]["id"] == "b"
    assert payload["messages"][2] == {"role": "tool", "tool_call_id": "a", "content": '{"ok":true}'}
    assert payload["messages"][3]["tool_call_id"] == "b"


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        {"choices": [{"message": {"role": "assistant", "content": 123}}]},
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"type": "custom"}]}}]},
    ],
)
def test_malformed_model_responses_are_structured_errors(payload: dict[str, object]) -> None:
    client, _ = _client(lambda request: _response(request, payload))
    with client, pytest.raises(ModelAdapterError) as caught:
        client.complete([AgentMessage(role="user", content="x")], [])
    assert caught.value.code in {"invalid_response", "invalid_tool_call"}
    assert MODEL_KEY not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "authentication_error"), (403, "authentication_error"), (429, "rate_limited"), (500, "upstream_error")],
)
def test_http_errors_are_structured_without_retry(status: int, code: str) -> None:
    client, requests = _client(lambda request: _response(request, {"error": {"message": "safe error"}}, status))
    with client, pytest.raises(ModelAdapterError) as caught:
        client.complete([AgentMessage(role="user", content="x")], [])
    assert caught.value.code == code
    assert len(requests) == 1


def test_tool_calling_unsupported_is_not_disguised_as_normal_response() -> None:
    client, _ = _client(
        lambda request: httpx.Response(
            400,
            content=b'{"error":{"message":"tools are not supported"}}',
            headers={"content-type": "application/json"},
            request=request,
        )
    )
    with client, pytest.raises(ModelAdapterError) as caught:
        client.complete([AgentMessage(role="user", content="x")], [])
    assert caught.value.code == "tool_calling_unsupported"


def test_timeout_and_network_errors_are_structured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client, _ = _client(handler)
    with client, pytest.raises(ModelAdapterError) as caught:
        client.complete([AgentMessage(role="user", content="x")], [])
    assert caught.value.code == "timeout"
    assert caught.value.retryable is True


def test_model_response_size_is_limited_during_stream_read() -> None:
    budget = RequestBudget(max_model_response_bytes=10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"{" + b"x" * 100),
            headers={"content-type": "application/json"},
            request=request,
        )

    client, _ = _client(handler, budget=budget)
    with client, pytest.raises(ModelAdapterError) as caught:
        client.complete([AgentMessage(role="user", content="x")], [])
    assert caught.value.code == "response_too_large"
    assert budget.model_response_bytes_used > 10


def test_missing_key_fails_before_network_request() -> None:
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, request=request))
    with pytest.raises(ModelAdapterError) as caught:
        OpenAICompatibleClient(
            base_url=BASE_URL,
            api_key=None,
            model="mock-model",
            transport=transport,
        )
    assert caught.value.code == "model_not_configured"
    assert requests == []


def test_agent_loop_consumes_adapter_response_and_returns_tool_result() -> None:
    model_calls = 0

    def model_handler(request: httpx.Request) -> httpx.Response:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            payload = {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "search-1",
                            "type": "function",
                            "function": {"name": "search_public_repositories", "arguments": '{"query":"x"}'},
                        }],
                    },
                }]
            }
        else:
            payload = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}]}
        return _response(request, payload)

    def github_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total_count": 0, "items": []}, request=request)

    budget = RequestBudget(max_github_requests=3, max_model_calls=3, max_feedback_bytes=20_000)
    model = OpenAICompatibleClient(
        base_url=BASE_URL,
        api_key=SecretStr(MODEL_KEY),
        model="mock-model",
        budget=budget,
        transport=httpx.MockTransport(model_handler),
    )
    with model, GitHubRestClient(budget=budget, transport=httpx.MockTransport(github_handler)) as github:
        result = AgentLoop(model=model, tools=build_github_tool_registry(github), budget=budget).run("find x")
    assert result.status == "completed"
    assert model_calls == 2
    assert result.tool_results[0].tool_name == "search_public_repositories"


def test_github_token_is_not_added_to_model_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _response(request, {"choices": [{"message": {"role": "assistant", "content": "done"}}]})

    config = AppConfig(
        model_base_url=BASE_URL,
        model="mock-model",
        model_api_key=SecretStr(MODEL_KEY),
        github_token=SecretStr(GITHUB_KEY),
    )
    client = OpenAICompatibleClient.from_config(config, transport=httpx.MockTransport(handler))
    with client:
        client.complete([AgentMessage(role="user", content="find")], [])
    request = requests[0]
    assert request.headers["authorization"] == f"Bearer {MODEL_KEY}"
    assert GITHUB_KEY not in request.content.decode("utf-8")
    assert GITHUB_KEY not in str(request.headers)


def test_t27_network_and_timeout_errors_are_safely_classified_without_leaks() -> None:
    cases = [
        (
            httpx.ConnectError("Connection refused"),
            "network_error",
            "network",
            True,
            "ConnectError",
        ),
        (
            httpx.ProxyError("502 Bad Gateway from proxy"),
            "network_error",
            "network",
            True,
            "ProxyError",
        ),
        (
            httpx.RemoteProtocolError("Server disconnected"),
            "network_error",
            "network",
            True,
            "RemoteProtocolError",
        ),
        (
            httpx.ConnectTimeout("Connect timeout"),
            "timeout",
            "timeout",
            True,
            None,
        ),
        (
            httpx.ReadTimeout("Read timeout"),
            "timeout",
            "timeout",
            True,
            None,
        ),
    ]

    for exc_to_raise, expected_code, expected_cat, expected_retry, expected_marker in cases:
        calls = 0

        def handler(request: httpx.Request, _exc: type[Exception] = exc_to_raise) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise _exc

        client, _ = _client(handler)
        with client, pytest.raises(ModelAdapterError) as caught:
            client.complete([AgentMessage(role="user", content="x")], [])

        assert caught.value.code == expected_code
        assert caught.value.category == expected_cat
        assert caught.value.retryable is expected_retry
        assert calls == 1
        if expected_marker:
            assert expected_marker in str(caught.value)

    # TLS specific exceptions
    tls_cases = [
        (ssl.SSLEOFError(8, "EOF occurred in violation of protocol"), "ConnectError[tls_unexpected_eof]"),
        (ssl.SSLCertVerificationError(1, "certificate verify failed"), "ConnectError[tls_certificate]"),
        (ssl.SSLError("generic ssl error"), "ConnectError[tls]"),
    ]
    for ssl_cause, expected_marker in tls_cases:
        calls = 0
        conn_err = httpx.ConnectError("tls connection failure")
        conn_err.__cause__ = ssl_cause

        def tls_handler(request: httpx.Request, _conn_err: httpx.ConnectError = conn_err) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise _conn_err

        client, _ = _client(tls_handler)
        with client, pytest.raises(ModelAdapterError) as caught:
            client.complete([AgentMessage(role="user", content="x")], [])

        assert caught.value.code == "network_error"
        assert caught.value.category == "network"
        assert caught.value.retryable is True
        assert calls == 1
        assert expected_marker in str(caught.value)

    # Malicious exception message with simulated key and credentials
    malicious_text = f"Connection failed with Bearer {MODEL_KEY} on https://api.deepseek.com/chat?token={GITHUB_KEY}"
    malicious_exc = httpx.ConnectError(malicious_text)
    calls = 0

    def leak_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise malicious_exc

    client, _ = _client(leak_handler)
    with client, pytest.raises(ModelAdapterError) as caught:
        client.complete([AgentMessage(role="user", content="x")], [])

    msg = str(caught.value)
    assert caught.value.code == "network_error"
    assert calls == 1
    assert MODEL_KEY not in msg
    assert GITHUB_KEY not in msg
    assert "https://" not in msg
    assert "ConnectError" in msg


def test_model_client_retries_transient_network_error_and_succeeds() -> None:
    calls = 0

    def retry_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("Transient TLS reset")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hello"}}],
                "usage": {"total_tokens": 5},
            },
            request=request,
        )

    client, _ = _client(retry_handler, max_retries=2)
    with client:
        res = client.complete([AgentMessage(role="user", content="hi")], [])
    assert res.content == "hello"
    assert calls == 2


def test_model_client_fails_after_max_retries_exhausted() -> None:
    calls = 0

    def failing_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("Permanent down")

    client, _ = _client(failing_handler, max_retries=2)
    with client, pytest.raises(ModelAdapterError) as caught:
        client.complete([AgentMessage(role="user", content="hi")], [])
    assert caught.value.code == "network_error"
    assert calls == 3  # initial + 2 retries


def test_model_client_does_not_retry_non_retryable_errors() -> None:
    calls = 0

    def auth_error_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "invalid_key"}, request=request)

    client, _ = _client(auth_error_handler, max_retries=2)
    with client, pytest.raises(ModelAdapterError) as caught:
        client.complete([AgentMessage(role="user", content="hi")], [])
    assert caught.value.code == "authentication_error"
    assert calls == 1  # 401 is not retryable, no extra attempts


def test_authentication_error_carries_the_provider_message_masked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Authentication Fails, Your api key: sk-abcdef123456 is invalid"}},
            request=request,
        )

    client, _ = _client(handler)
    with client, pytest.raises(ModelAdapterError) as caught:
        client.complete([AgentMessage(role="user", content="hi")], [])
    assert caught.value.code == "authentication_error"
    assert "is invalid" in str(caught.value)
    assert "sk-abcdef123456" not in str(caught.value)


def test_list_model_ids_reads_the_models_route() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}, {"id": "deepseek-chat"}]},
            request=request,
        )

    ids = list_model_ids("https://api.deepseek.com/v1", "sk-secret", transport=httpx.MockTransport(handler))
    assert ids == ["deepseek-chat", "deepseek-reasoner"]
    assert str(seen[0].url) == "https://api.deepseek.com/v1/models"
    assert seen[0].headers["authorization"] == "Bearer sk-secret"


def test_list_model_ids_masks_the_key_in_auth_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Authentication Fails, Your api key: sk-abcdef123456 is invalid"}},
            request=request,
        )

    with pytest.raises(ModelAdapterError) as caught:
        list_model_ids("https://api.deepseek.com/v1", "sk-abcdef123456", transport=httpx.MockTransport(handler))
    assert caught.value.code == "model_list_auth"
    assert "sk-***" in str(caught.value)
    assert "sk-abcdef123456" not in str(caught.value)


def test_list_model_ids_reports_an_unsupported_route() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "not found"}}, request=request)

    with pytest.raises(ModelAdapterError) as caught:
        list_model_ids("https://api.deepseek.com/v1", None, transport=httpx.MockTransport(handler))
    assert caught.value.code == "model_list_unsupported"
