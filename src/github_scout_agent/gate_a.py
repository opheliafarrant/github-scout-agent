"""Offline-only Gate A preflight for DeepSeek Tool Calling."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

from .agent_models import AgentMessage
from .budget import RequestBudget
from .config import load_config
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
    preflight_status: str = "not_run"
    estimated_cost_cny: float | None = None
    actual_cost_cny: float | None = None


def _response_payload(scenario: str) -> dict[str, Any]:
    def _base_call() -> dict[str, Any]:
        return {
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
        call = _base_call()
        call["function"]["name"] = "unknown_tool"
        message = {"role": "assistant", "content": None, "tool_calls": [call]}
    elif scenario == "invalid_json":
        call = _base_call()
        call["function"]["arguments"] = "{invalid"
        message = {"role": "assistant", "content": None, "tool_calls": [call]}
    elif scenario == "invalid_schema":
        call = _base_call()
        call["function"]["arguments"] = json.dumps({"query": "x", "extra": "blocked"})
        message = {"role": "assistant", "content": None, "tool_calls": [call]}
    elif scenario == "missing_id":
        call = _base_call()
        missing = {key: value for key, value in call.items() if key != "id"}
        message = {"role": "assistant", "content": None, "tool_calls": [missing]}
    elif scenario == "duplicate_id":
        message = {"role": "assistant", "content": None, "tool_calls": [_base_call(), _base_call()]}
    elif scenario == "multiple_calls":
        call1 = _base_call()
        call2 = _base_call()
        call2["id"] = "gate-a-call-2"
        message = {"role": "assistant", "content": None, "tool_calls": [call1, call2]}
    else:
        message = {"role": "assistant", "content": None, "tool_calls": [_base_call()]}
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


def _gate_a_project_state_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".scout" / "gate_a_online_authorization.json"


def _online_cost_estimate(payload: dict[str, object]) -> tuple[int, int, float]:
    input_bytes = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    input_tokens = max(1, math.ceil(input_bytes / 4))
    output_tokens = 512
    # Conservative planning assumption: cache miss + peak rates, 1 USD = 7.2 CNY.
    usd = (input_tokens / 1_000_000 * 0.30) + (output_tokens / 1_000_000 * 1.20)
    return input_tokens, output_tokens, round(usd * 7.2, 6)


def _usage_cost_cny(usage: dict[str, object]) -> float | None:
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if not isinstance(completion, int):
        return None
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    if isinstance(hit, int) and isinstance(miss, int):
        input_usd = hit / 1_000_000 * 0.006 + miss / 1_000_000 * 0.30
    elif isinstance(prompt, int):
        input_usd = prompt / 1_000_000 * 0.30
    else:
        return None
    return round((input_usd + completion / 1_000_000 * 1.20) * 7.2, 6)


def _write_online_state(path: Path, state: dict[str, object], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf-8", newline="\n") as handle:
        json.dump(state, handle, ensure_ascii=False, sort_keys=True, indent=2)


def _online_blocked(code: str, message: str) -> GateAResult:
    return GateAResult(
        status="failed",
        code=code,
        message=message,
        request_summary={"mode": "online", "model_requests": 0},
        protocol_summary={},
        usage_summary={"status": "unknown"},
        budget={},
        preflight_status="blocked",
        real_network_requests=0,
        github_requests=0,
    )


def run_gate_a_online() -> GateAResult:
    """Run the single authorized live attempt; a local record makes it one-shot."""

    state_path = _gate_a_project_state_path()
    if state_path.exists():
        return _online_blocked("authorization_consumed", "Gate A one-shot authorization is already consumed or indeterminate")

    try:
        config = load_config()
    except (ValueError, TypeError) as exc:
        return _online_blocked("invalid_local_config", str(exc))
    if not config.model_api_key:
        return _online_blocked("missing_api_key", "GITHUB_SCOUT_MODEL_API_KEY is not configured")
    if (
        config.model_base_url != "https://api.deepseek.com"
        or config.model != "deepseek-flash"
        or config.thinking_mode != "disabled"
        or config.strict_tools is not False
        or config.max_tokens != 512
    ):
        return _online_blocked("config_mismatch", "local model configuration does not match the fixed Gate A contract")

    budget = RequestBudget(
        max_model_calls=1,
        max_github_requests=0,
        max_model_response_bytes=16 * 1024,
        max_feedback_bytes=16 * 1024,
        max_task_seconds=60,
    )
    github_client = GitHubRestClient(
        budget=budget,
        transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("GitHub must not be called"))),
    )
    preflight_client = OpenAICompatibleClient.from_config(
        config,
        budget=budget,
        transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request)),
        timeout=10.0,
    )
    try:
        registry = build_github_tool_registry(github_client)
        messages = [
            AgentMessage(
                role="user",
                content="Find public GitHub repositories that demonstrate Python AI agents using native LLM tool calling. Use the repository search tool to propose a relevant search query.",
            )
        ]
        payload = preflight_client.build_payload(messages, [registry.schemas()[0]])
        input_tokens, output_tokens, estimate_cny = _online_cost_estimate(payload)
    finally:
        preflight_client.close()
        github_client.close()

    if estimate_cny >= 5.0:
        return _online_blocked("cost_preflight_failed", "conservative Gate A estimate reaches the cumulative 5 CNY budget")

    authorization = {
        "status": "reserved_before_request",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-flash",
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "estimated_cost_cny": estimate_cny,
        "model_requests_authorized": 1,
        "github_requests_authorized": 0,
    }
    try:
        _write_online_state(state_path, authorization, exclusive=True)
    except FileExistsError:
        return _online_blocked("authorization_consumed", "Gate A one-shot authorization is already consumed or indeterminate")
    except OSError:
        return _online_blocked("authorization_record_failed", "unable to persist one-shot authorization record")

    attempted = False
    result: GateAResult
    live_budget = RequestBudget(
        max_model_calls=1,
        max_github_requests=0,
        max_model_response_bytes=16 * 1024,
        max_feedback_bytes=16 * 1024,
        max_task_seconds=60,
    )
    client = OpenAICompatibleClient.from_config(config, budget=live_budget, timeout=10.0)
    try:
        live_budget.reserve_model_call()
        attempted = True
        response = client.complete(messages, [registry.schemas()[0]])
        usage = response.usage.model_dump() if response.usage else {"status": "unknown"}
        protocol = {
            "tool_call_count": len(response.tool_calls),
            "tool_call_ids": [call.id for call in response.tool_calls],
            "tool_names": [call.name for call in response.tool_calls],
            "finish_reason": response.finish_reason,
        }
        if len(response.tool_calls) != 1:
            result = GateAResult(
                status="failed",
                code="no_tool_call" if not response.tool_calls else "multiple_tool_calls",
                message="Gate A requires exactly one search tool call",
                request_summary={"mode": "online", "model_requests": 1, "request_payload": {"model": "deepseek-flash", "thinking": {"type": "disabled"}, "tool_choice": "auto", "stream": False, "max_tokens": 512, "strict_present": False}},
                protocol_summary=protocol,
                usage_summary=usage,
                budget=live_budget.snapshot(),
                preflight_status="passed",
                estimated_cost_cny=estimate_cny,
                actual_cost_cny=_usage_cost_cny(usage),
                real_network_requests=1,
                github_requests=0,
            )
        else:
            call = response.tool_calls[0]
            try:
                arguments = json.loads(call.arguments)
                SearchArguments.model_validate(arguments)
                code, message = "ok", "single search Tool Call passed live protocol validation"
            except (json.JSONDecodeError, ValueError):
                code, message = "invalid_arguments", "live search Tool Call arguments failed JSON/schema validation"
            result = GateAResult(
                status="passed" if code == "ok" else "failed",
                code=code,
                message=message,
                request_summary={"mode": "online", "model_requests": 1, "request_payload": {"model": "deepseek-flash", "thinking": {"type": "disabled"}, "tool_choice": "auto", "stream": False, "max_tokens": 512, "strict_present": False}},
                protocol_summary=protocol,
                usage_summary=usage,
                budget=live_budget.snapshot(),
                preflight_status="passed",
                estimated_cost_cny=estimate_cny,
                actual_cost_cny=_usage_cost_cny(usage),
                real_network_requests=1,
                github_requests=0,
            )
    except ModelAdapterError as exc:
        result = GateAResult(
            status="failed",
            code=exc.code,
            message=str(exc),
            request_summary={"mode": "online", "model_requests": 1},
            protocol_summary={},
            usage_summary={"status": "unknown"},
            budget=live_budget.snapshot(),
            preflight_status="passed",
            estimated_cost_cny=estimate_cny,
            actual_cost_cny=None,
            real_network_requests=1 if attempted else 0,
            github_requests=0,
        )
    finally:
        client.close()
        authorization.update(
            {
                "status": "completed_attempt",
                "attempted": attempted,
                "result_code": result.code if "result" in locals() else "unknown",
                "result_status": result.status if "result" in locals() else "failed",
            }
        )
        _write_online_state(state_path, authorization)
    return result
