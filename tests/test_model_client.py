from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from github_scout_agent.agent_loop import AgentLoop
from github_scout_agent.agent_models import AgentMessage, ToolCall
from github_scout_agent.budget import RequestBudget
from github_scout_agent.config import AppConfig
from github_scout_agent.github_api import GitHubRestClient
from github_scout_agent.model_client import ModelAdapterError, OpenAICompatibleClient
from github_scout_agent.tool_registry import build_github_tool_registry


BASE_URL = "https://model.example.test/v1"
MODEL_KEY = "test-model-key-not-a-credential"
GITHUB_KEY = "github-token-not-a-credential"


def _response(request: httpx.Request, payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=request)


def _client(handler, *, budget: RequestBudget | None = None, key: str = MODEL_KEY) -> tuple[OpenAICompatibleClient, list[httpx.Request]]:
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
