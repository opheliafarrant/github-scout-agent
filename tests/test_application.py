from __future__ import annotations

import json

import httpx
from pydantic import SecretStr

from github_scout_agent.application import render_mvp_report, run_research_task
from github_scout_agent.agent_loop import AgentRunResult
from github_scout_agent.github_models import ToolError, ToolResult
from github_scout_agent.budget import RequestBudget
from github_scout_agent.github_api import GitHubRestClient
from github_scout_agent.model_client import OpenAICompatibleClient


def test_production_application_assembly_works_with_mock_transports() -> None:
    model_calls = 0

    def model_handler(request: httpx.Request) -> httpx.Response:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "run-search",
                    "type": "function",
                    "function": {"name": "search_public_repositories", "arguments": json.dumps({"query": "x"})},
                }],
            }
            return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "tool_calls"}]}, request=request)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Evidence reviewed."}, "finish_reason": "stop"}]}, request=request)

    def github_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "items": [{
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
                }],
            },
            request=request,
        )

    budget = RequestBudget(max_model_calls=3, max_github_requests=3, max_candidates=3, max_feedback_bytes=20_000)
    with OpenAICompatibleClient(
        base_url="https://model.example.test/v1",
        api_key=SecretStr("application-test-key"),
        model="mock-model",
        budget=budget,
        transport=httpx.MockTransport(model_handler),
    ) as model, GitHubRestClient(budget=budget, transport=httpx.MockTransport(github_handler)) as github:
        result = run_research_task("find x", model=model, github=github, budget=budget)

    report = render_mvp_report("find x", result)
    assert result.status == "completed"
    assert "octo/demo" in report
    assert "https://github.com/octo/demo" in report
    assert "api.github.com/search/repositories" in report
    assert "Evidence reviewed." in report
    assert "model_calls_used" in report
    assert model_calls == 2


def test_partial_result_report_is_explicitly_marked() -> None:
    model_calls = 0

    def model_handler(request: httpx.Request) -> httpx.Response:
        nonlocal model_calls
        model_calls += 1
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "partial-search",
                            "type": "function",
                            "function": {"name": "search_public_repositories", "arguments": json.dumps({"query": "partial"})},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }]
            },
            request=request,
        )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total_count": 0, "items": []}, request=request)

    budget = RequestBudget(max_model_calls=1, max_github_requests=1, max_candidates=3, max_feedback_bytes=20_000)
    with OpenAICompatibleClient(
        base_url="https://model.example.test/v1",
        api_key=SecretStr("partial-report-test-key"),
        model="mock-model",
        budget=budget,
        transport=httpx.MockTransport(model_handler),
    ) as model, GitHubRestClient(budget=budget, transport=httpx.MockTransport(handler)) as github:
        result = run_research_task("partial", model=model, github=github, budget=budget)

    report = render_mvp_report("partial", result)
    assert result.status == "terminated"
    assert "Completeness: partial" in report
    assert model_calls == 1


def test_finalized_report_with_skipped_evidence_is_marked_partial() -> None:
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text="Evidence-constrained recommendation.",
        tool_results=[
            ToolResult(
                tool_name="get_repository_languages_and_topics",
                ok=False,
                error=ToolError(
                    code="github_requests_insufficient",
                    category="budget",
                    message="only one request remained",
                ),
            )
        ],
        budget={},
    )

    report = render_mvp_report("partial evidence", result)

    assert "Status: completed" in report
    assert "Completeness: partial" in report
