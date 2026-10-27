from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from github_scout_agent.github_api import GitHubRestClient
from github_scout_agent.tool_registry import ToolDefinition, ToolRegistry, build_github_tool_registry


EXPECTED_TOOLS = {
    "search_public_repositories",
    "get_public_repository",
    "get_public_readme",
    "replace_candidate_with_readme",
    "get_repository_license",
    "get_repository_languages_and_topics",
}


def test_registry_has_exactly_the_six_predefined_tools() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500, request=request))
    with GitHubRestClient(transport=transport) as client:
        registry = build_github_tool_registry(client)
        schemas = registry.schemas()

    assert set(registry.names()) == EXPECTED_TOOLS
    assert {schema["function"]["name"] for schema in schemas} == EXPECTED_TOOLS
    assert all(schema["type"] == "function" for schema in schemas)
    assert all(schema["function"]["strict"] is True for schema in schemas)


def test_registry_estimates_composite_request_cost_without_network() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, request=request)

    with GitHubRestClient(transport=httpx.MockTransport(handler)) as client:
        registry = build_github_tool_registry(client)
        assert registry.estimated_request_count(
            "get_repository_languages_and_topics", {"owner": "octo", "repo": "demo"}
        ) == 2
        assert registry.estimated_request_count(
            "get_public_readme", {"owner": "octo", "repo": "demo"}
        ) == 1
        assert registry.estimated_request_count("unknown_tool", {}) == 0
        assert registry.estimated_request_count("get_public_readme", "not-json") == 0

    assert requests == []


def test_unknown_tool_invalid_json_and_invalid_arguments_never_reach_transport() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, request=request)

    with GitHubRestClient(transport=httpx.MockTransport(handler)) as client:
        registry = build_github_tool_registry(client)
        unknown = registry.dispatch("run_shell", {})
        invalid_json = registry.dispatch("get_public_repository", "not-json")
        invalid_args = registry.dispatch("get_public_repository", {"owner": "../x", "repo": "demo"})

    assert unknown.error.code == "unknown_tool"
    assert invalid_json.error.code == "invalid_json_arguments"
    assert invalid_args.error.code == "invalid_arguments"
    assert requests == []


def test_internal_value_error_is_not_reclassified_as_github_response_error() -> None:
    class EmptyArguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

    def internal_failure(arguments: BaseModel) -> object:
        del arguments
        raise ValueError("internal programming failure")

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="internal_failure",
        description="test",
        arguments_model=EmptyArguments,
        handler=internal_failure,
    ))

    with pytest.raises(ValueError, match="internal programming failure"):
        registry.dispatch("internal_failure", {})


def test_request_budget_cannot_be_overridden_by_tool_arguments() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/languages"):
            return httpx.Response(200, json={"Python": 1}, request=request)
        return httpx.Response(200, json={"names": ["demo"]}, request=request)

    from github_scout_agent.budget import RequestBudget

    budget = RequestBudget(max_github_requests=1)
    with GitHubRestClient(budget=budget, transport=httpx.MockTransport(handler)) as client:
        registry = build_github_tool_registry(client)
        result = registry.dispatch(
            "get_repository_languages_and_topics",
            {"owner": "octo", "repo": "demo"},
        )

    assert result.ok is False
    assert result.error.code == "budget_exceeded"
    assert result.request_count == 1
    assert len(requests) == 1
    assert budget.github_requests_used == 1


def test_token_is_only_used_as_request_header_and_not_returned() -> None:
    token = "test-token-not-a-credential"
    seen_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("authorization"))
        payload = {"Python": 1} if request.url.path.endswith("/languages") else {"names": []}
        return httpx.Response(200, json=payload, request=request)

    with GitHubRestClient(token=token, transport=httpx.MockTransport(handler)) as client:
        result = build_github_tool_registry(client).dispatch(
            "get_repository_languages_and_topics", {"owner": "octo", "repo": "demo"}
        )

    assert result.ok is True
    assert seen_headers == [f"Bearer {token}", f"Bearer {token}"]
    assert token not in result.model_dump_json()
