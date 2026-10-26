from __future__ import annotations

import httpx

from github_scout_agent.github_api import GitHubRestClient
from github_scout_agent.tool_registry import build_github_tool_registry


EXPECTED_TOOLS = {
    "search_public_repositories",
    "get_public_repository",
    "get_public_readme",
    "get_repository_license",
    "get_repository_languages_and_topics",
}


def test_registry_has_exactly_the_five_predefined_tools() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500, request=request))
    with GitHubRestClient(transport=transport) as client:
        registry = build_github_tool_registry(client)
        schemas = registry.schemas()

    assert set(registry.names()) == EXPECTED_TOOLS
    assert {schema["function"]["name"] for schema in schemas} == EXPECTED_TOOLS
    assert all(schema["type"] == "function" for schema in schemas)
    assert all(schema["function"]["strict"] is True for schema in schemas)


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
