from __future__ import annotations

import base64

import httpx

from github_scout_agent.budget import RequestBudget
from github_scout_agent.github_api import GitHubRestClient
from github_scout_agent.live_agent_demo import create_live_agent_loop
from github_scout_agent.tool_registry import build_github_tool_registry


def _repo() -> dict[str, object]:
    return {
        "id": 1,
        "full_name": "octo/demo",
        "html_url": "https://github.com/octo/demo",
        "private": False,
        "visibility": "public",
        "fork": False,
        "archived": False,
        "disabled": False,
        "stargazers_count": 1,
        "forks_count": 1,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "pushed_at": "2024-01-01T00:00:00Z",
        "license": None,
        "topics": [],
    }


def test_search_scenario_wires_mock_llm_loop_to_mock_github() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/repositories":
            return httpx.Response(200, json={"total_count": 1, "items": [_repo()]}, request=request)
        if request.url.path.endswith("/readme"):
            text = "mock evidence"
            return httpx.Response(
                200,
                json={"encoding": "base64", "content": base64.b64encode(text.encode()).decode(), "size": len(text)},
                request=request,
            )
        return httpx.Response(404, json={"message": "Not Found"}, request=request)

    budget = RequestBudget(max_github_requests=8, max_model_calls=4, max_candidates=5, max_feedback_bytes=20_000)
    with GitHubRestClient(budget=budget, transport=httpx.MockTransport(handler)) as client:
        loop = create_live_agent_loop(client, "search", budget)
        result = loop.run("search")

    assert result.status == "completed"
    assert [result_item.tool_name for result_item in result.tool_results] == [
        "search_public_repositories",
        "get_public_readme",
    ]
    assert result.tool_results[1].data.trust_label == "untrusted_remote_text"


def test_repository_scenario_wires_detail_then_readme_without_real_network() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/readme"):
            return httpx.Response(404, json={"message": "Not Found"}, request=request)
        if request.url.path.endswith("/Hello-World"):
            return httpx.Response(200, json={**_repo(), "full_name": "octocat/Hello-World"}, request=request)
        return httpx.Response(404, json={"message": "Not Found"}, request=request)

    budget = RequestBudget(max_github_requests=8, max_model_calls=4, max_candidates=5, max_feedback_bytes=20_000)
    with GitHubRestClient(budget=budget, transport=httpx.MockTransport(handler)) as client:
        loop = create_live_agent_loop(client, "repository", budget)
        result = loop.run("inspect")

    assert result.status == "completed"
    assert [result_item.tool_name for result_item in result.tool_results] == [
        "get_public_repository",
        "get_public_readme",
    ]
    assert result.tool_results[1].data.status == "missing"
    assert paths == ["/repos/octocat/Hello-World", "/repos/octocat/Hello-World/readme"]
