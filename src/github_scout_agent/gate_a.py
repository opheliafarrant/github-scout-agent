"""Offline-only Gate A preflight for DeepSeek Tool Calling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

from .agent_models import AgentMessage
from .budget import RequestBudget
from .github_api import GitHubRestClient
from .model_client import ModelAdapterError, OpenAICompatibleClient
from .tool_registry import SearchArguments, build_github_tool_registry


@dataclass(frozen=True)
class GateAConfig:
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    thinking: str = "disabled"
    tool_choice: str = "auto"
    strict_tools: bool = False
    stream: bool = False
    max_tokens: int = 512
    max_model_calls: int = 1
    max_github_requests: int = 0


class GateAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "failed"]
    code: str
    message: str
    request_summary: dict[str, Any]
    protocol_summary: dict[str, Any]
    usage_summary: dict[str, Any]
    budget: dict[str, object]
    real_network_requests: int = 0
    github_requests: int = 0


def _response_payload(scenario: str) -> dict[str, Any]:
    valid_call = {
        "id": "gate-a-call-1",
        "type": "function",
        "function": {
            "name": "search_public_repositories",
            "arguments": json.dumps(
                {"query": "Python AI agents native tool calling is:public"}
            ),
        },
    }
    if scenario == "final_text":
        message = {"role": "assistant", "content": "no tool", "tool_calls": []}
    elif scenario == "unknown_tool":
        valid_call["function"]["name"] = "unknown_tool"
        message = {"role": "assistant", "content": None, "tool_calls": [valid_call]}
    elif scenario == "invalid_json":
        valid_call["function"]["arguments"] = "{invalid"
        message = {"role": "assistant", "content": None, "tool_calls": [valid_call]}
    elif scenario == "invalid_schema":
        valid_call["function"]["arguments"] = json.dumps({"query": "x", "extra": "blocked"})
        message = {"role": "assistant", "content": None, "tool_calls": [valid_call]}
    elif scenario == "missing_id":
        missing = {key: value for key, value in valid_call.items() if key != "id"}
        message = {"role": "assistant", "content": None, "tool_calls": [missing]}
    elif scenario == "duplicate_id":
        message = {"role": "assistant", "content": None, "tool_calls": [valid_call, dict(valid_call)]}
    elif scenario == "multiple_calls":
        second = dict(valid_call)
        second["id"] = "gate-a-call-2"
        message = {"role": "assistant", "content": None, "tool_calls": [valid_call, second]}
    else:
        message = {"role": "assistant", "content": None, "tool_calls": [valid_call]}
    return {
        "id": "dry-run-completion",
        "model": "deepseek-flash",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if message["tool_calls"] else "stop"}],
        "usage": None if scenario == "usage_missing" else {"prompt_tokens": 32, "completion_tokens": 18, "total_tokens": 50},
    }


def _error_payload(status: int, scenario: str) -> tuple[int, bytes]:
    if scenario == "unsupported_tools":
        return 400, b'{"error":{"message":"tools are not supported"}}'
    return status, b'{"error":{"message":"offline gate error"}}'


def run_gate_a_dry_run(scenario: str = "valid", config: GateAConfig | None = None) -> GateAResult:
    target = config or GateAConfig()
    budget = RequestBudget(
        max_model_calls=target.max_model_calls,
        max_github_requests=target.max_github_requests,
        max_model_response_bytes=16 * 1024,
        max_feedback_bytes=16 * 1024,
        max_task_seconds=30,
    )
    request_log: list[dict[str, object]] = []
    request_payload_summary: dict[str, object] = {}
    github_requests: list[httpx.Request] = []

    mismatch = next(
        (
            ("config_mismatch", "Gate A target configuration is not the fixed DeepSeek dry-run contract")
            for actual, expected in (
                (target.base_url, "https://api.deepseek.com"),
                (target.model, "deepseek-flash"),
                (target.thinking, "disabled"),
                (target.tool_choice, "auto"),
                (target.strict_tools, False),
                (target.stream, False),
                (target.max_tokens, 512),
                (target.max_model_calls, 1),
                (target.max_github_requests, 0),
            )
            if actual != expected
        ),
        None,
    )
    if mismatch:
        return GateAResult(
            status="failed",
            code=mismatch[0],
            message=mismatch[1],
            request_summary={"transport": "MockTransport", "model_requests": 0},
            protocol_summary={},
            usage_summary={"status": "unknown"},
            budget=budget.snapshot(),
        )

    def model_handler(request: httpx.Request) -> httpx.Response:
        request_log.append({"method": request.method, "host": request.url.host, "path": request.url.path})
        try:
            payload = json.loads(request.content)
            request_payload_summary.update(
                {
                    "model": payload.get("model"),
                    "path": request.url.path,
                    "thinking": payload.get("thinking"),
                    "tool_choice": payload.get("tool_choice"),
                    "stream": payload.get("stream"),
                    "max_tokens": payload.get("max_tokens"),
                    "tool_count": len(payload.get("tools", [])) if isinstance(payload.get("tools"), list) else None,
                    "strict_present": any(
                        isinstance(tool, dict)
                        and isinstance(tool.get("function"), dict)
                        and "strict" in tool["function"]
                        for tool in payload.get("tools", [])
                        if isinstance(payload.get("tools"), list)
                    ),
                    "authorization_present": "authorization" in request.headers,
                }
            )
        except (TypeError, ValueError):
            request_payload_summary["parse"] = "unavailable"
        if scenario == "timeout":
            raise httpx.ReadTimeout("offline timeout", request=request)
        if scenario == "response_too_large":
            return httpx.Response(
                200,
                stream=httpx.ByteStream(b"{" + b"x" * 20_000),
                headers={"content-type": "application/json"},
                request=request,
            )
        if scenario in {"http_400", "http_401", "http_403", "http_429", "http_500", "unsupported_tools"}:
            status = {"http_400": 400, "http_401": 401, "http_403": 403, "http_429": 429, "http_500": 500}.get(scenario, 400)
            code, content = _error_payload(status, scenario)
            return httpx.Response(code, content=content, headers={"content-type": "application/json"}, request=request)
        return httpx.Response(200, json=_response_payload(scenario), request=request)

    def github_handler(request: httpx.Request) -> httpx.Response:
        github_requests.append(request)
        raise AssertionError("Gate A must not execute GitHub tools")

    def request_summary() -> dict[str, object]:
        return {
            "transport": "MockTransport",
            "model_requests": len(request_log),
            "request_log": request_log,
            "request_payload": request_payload_summary,
        }

    github_client = GitHubRestClient(budget=budget, transport=httpx.MockTransport(github_handler))
    registry = build_github_tool_registry(github_client)
    model_client = OpenAICompatibleClient(
        base_url=target.base_url,
        api_key=SecretStr("dry-run-placeholder-not-a-credential"),
        model=target.model,
        budget=budget,
        transport=httpx.MockTransport(model_handler),
        thinking_mode=target.thinking,  # type: ignore[arg-type]
        strict_tools=target.strict_tools,
        max_tokens=target.max_tokens,
    )
    try:
        budget.reserve_model_call()
        response = model_client.complete(
            [AgentMessage(role="user", content="Find public GitHub repositories that demonstrate Python AI agents using native LLM tool calling. Use the repository search tool to propose a relevant search query.")],
            [registry.schemas()[0]],
        )
    except ModelAdapterError as exc:
        return GateAResult(
            status="failed",
            code=exc.code,
            message=str(exc),
            request_summary=request_summary(),
            protocol_summary={},
            usage_summary={"status": "unknown"},
            budget=budget.snapshot(),
            real_network_requests=0,
            github_requests=len(github_requests),
        )
    finally:
        model_client.close()
        github_client.close()

    usage = response.usage.model_dump() if response.usage else {"status": "unknown"}
    protocol = {
        "tool_call_count": len(response.tool_calls),
        "tool_call_ids": [call.id for call in response.tool_calls],
        "tool_names": [call.name for call in response.tool_calls],
        "finish_reason": response.finish_reason,
    }
    if len(response.tool_calls) != 1:
        code = "no_tool_call" if not response.tool_calls else "multiple_tool_calls"
        return GateAResult(
            status="failed", code=code, message="Gate A requires exactly one search tool call",
            request_summary=request_summary(),
            protocol_summary=protocol, usage_summary=usage, budget=budget.snapshot(),
            real_network_requests=0, github_requests=len(github_requests),
        )
    call = response.tool_calls[0]
    if call.name != "search_public_repositories":
        code, message = "wrong_tool", "Gate A returned an unexpected tool name"
    else:
        try:
            arguments = json.loads(call.arguments)
            SearchArguments.model_validate(arguments)
            code, message = "ok", "single search Tool Call passed offline protocol validation"
        except (json.JSONDecodeError, ValueError):
            code, message = "invalid_arguments", "search Tool Call arguments failed JSON/schema validation"
    return GateAResult(
        status="passed" if code == "ok" else "failed",
        code=code,
        message=message,
        request_summary=request_summary(),
        protocol_summary=protocol,
        usage_summary=usage,
        budget=budget.snapshot(),
        real_network_requests=0,
        github_requests=len(github_requests),
    )
