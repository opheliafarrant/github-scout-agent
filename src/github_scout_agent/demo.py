"""Offline CLI demonstration for the S3-03 Agent Loop."""

from __future__ import annotations

import base64

import httpx

from .agent_loop import AgentLoop, AgentRunResult
from .budget import RequestBudget
from .github_api import GitHubRestClient
from .mock_llm import ScenarioMockLLM
from .tool_registry import build_github_tool_registry


def _demo_transport(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/search/repositories":
        query = request.url.params.get("q", "")
        if query == "empty query":
            payload = {"total_count": 0, "incomplete_results": False, "items": []}
        else:
            payload = {
                "total_count": 1,
                "incomplete_results": False,
                "items": [
                    {
                        "id": 101,
                        "full_name": "octo/demo-agent",
                        "html_url": "https://github.com/octo/demo-agent",
                        "description": "Mock public repository",
                        "private": False,
                        "visibility": "public",
                        "fork": False,
                        "archived": False,
                        "disabled": False,
                        "stargazers_count": 7,
                        "forks_count": 1,
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-02-01T00:00:00Z",
                        "pushed_at": "2024-03-01T00:00:00Z",
                        "license": None,
                        "topics": ["agent"],
                    }
                ],
            }
        return httpx.Response(200, json=payload, request=request)
    if request.url.path.endswith("/readme"):
        content = "Mock README evidence; it is untrusted remote text."
        return httpx.Response(
            200,
            json={
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(content.encode()).decode(),
                "size": len(content),
                "sha": "demo-sha",
            },
            request=request,
        )
    return httpx.Response(404, json={"message": "Not Found"}, request=request)


def run_mock_demo() -> AgentRunResult:
    budget = RequestBudget(
        max_github_requests=5,
        max_response_bytes=64 * 1024,
        max_candidates=5,
        max_model_calls=5,
        max_task_seconds=10,
        max_feedback_bytes=12_000,
    )
    with GitHubRestClient(budget=budget, transport=httpx.MockTransport(_demo_transport)) as client:
        loop = AgentLoop(
            model=ScenarioMockLLM("empty_rewrite"),
            tools=build_github_tool_registry(client),
            budget=budget,
        )
        return loop.run("Find a public open source agent project")
