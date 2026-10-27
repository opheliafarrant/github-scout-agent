from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from github_scout_agent.application import (
    infer_required_language,
    infer_required_languages,
    render_mvp_report,
    requires_native_tool_calling_evidence,
    run_research_task,
)
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


def test_report_uses_evidence_status_not_model_completion_alone() -> None:
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text="The model says complete.",
        evidence_complete=False,
        evidence_gaps=["README not checked: octo/demo"],
        budget={},
    )

    report = render_mvp_report("find Python tool calling repositories", result)

    assert "Completeness: partial" in report
    assert "README not checked: octo/demo" in report


def test_task_language_inference_is_not_global() -> None:
    assert infer_required_language("find small Python agent repositories") == "Python"
    assert infer_required_language("find native C++ runtimes") == "C++"
    assert infer_required_language("find small agent repositories") is None


def test_language_inference_ignores_english_go_and_swift_collisions() -> None:
    assert infer_required_languages("Go find small agent repositories") == ()
    assert infer_required_languages("Please go ahead and search for agent frameworks") == ()
    assert infer_required_languages("Let us go through GitHub agents") == ()
    assert infer_required_languages("Go agent repositories") == ("Go",)
    assert infer_required_languages("A swift agent framework") == ()
    assert infer_required_languages("Swift and lightweight agents") == ()
    assert infer_required_languages("Go and find Python agents") == ("Python",)
    assert infer_required_languages("Swift and lightweight Python agents") == ("Python",)
    assert infer_required_languages("Find repositories written in Swift") == ("Swift",)
    assert infer_required_languages("Go language agent repositories") == ("Go",)


def test_multiple_language_constraints_preserve_all_requested_languages() -> None:
    assert infer_required_languages("Find TypeScript or Python agents") == ("TypeScript", "Python")


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("find agents in go", ("Go",)),
        ("python or go", ("Python", "Go")),
        ("agents in swift", ("Swift",)),
        ("python or swift", ("Python", "Swift")),
        ("agents in Go or Python", ("Go", "Python")),
        ("tools in swift", ("Swift",)),
        ("Go, Swift or Python agents", ("Go", "Swift", "Python")),
    ],
)
def test_language_prepositions_and_language_lists(task, expected) -> None:
    assert infer_required_languages(task) == expected


@pytest.mark.parametrize(
    ("task", "expected_languages"),
    [
        ("find Python agents", ("Python",)),
        ("find TypeScript or Python agents", ("TypeScript", "Python")),
        ("python or go", ("Python", "Go")),
    ],
)
def test_task_language_constraints_reach_production_search(task, expected_languages) -> None:
    model_requests = []
    github_requests = []

    def model_handler(request):
        model_requests.append(json.loads(request.content))
        message = {"role": "assistant", "content": "Compared returned metadata; unverified features remain unknown."}
        if len(model_requests) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "qualified-search",
                    "type": "function",
                    "function": {
                        "name": "search_public_repositories",
                        "arguments": json.dumps({"query": "agent"}),
                    },
                }],
            }
        return httpx.Response(200, json={"choices": [{"message": message}]}, request=request)

    def github_handler(request):
        github_requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.github.com"
        assert request.url.params["per_page"] == "5"
        items = [{
            "full_name": f"octo/{language.lower() if language else 'unknown'}",
            "html_url": f"https://github.com/octo/{language.lower() if language else 'unknown'}",
            "private": False,
            "visibility": "public",
            "language": language,
            "description": "Python Go Swift TypeScript mentioned here are not language evidence.",
        } for language in ("Python", "TypeScript", "Go", "Swift", None)]
        return httpx.Response(200, json={"items": items, "total_count": len(items)}, request=request)

    budget = RequestBudget(max_candidates=3, max_model_calls=5, max_github_requests=8)
    with OpenAICompatibleClient(
        base_url="https://model.example.test/v1", model="mock-model",
        api_key=SecretStr("offline-language-test-key"), budget=budget,
        transport=httpx.MockTransport(model_handler),
    ) as model, GitHubRestClient(
        budget=budget, max_retries=0, transport=httpx.MockTransport(github_handler),
    ) as github:
        # Production constructs a client before the application assigns task constraints.
        assert github.required_languages == ()
        result = run_research_task(task, model=model, github=github, budget=budget)

    search = result.tool_results[0].data
    assert set(item.language for item in search.items) == set(expected_languages)
    assert search.required_languages == list(expected_languages)
    assert search.excluded_language_count == 4 - len(expected_languages)
    assert [item.language for item in search.unverified_items] == [None]
    assert result.status == "completed"
    assert result.evidence_complete is True
    assert budget.candidates_used == 0  # search no longer auto-accepts; acceptance at investigation time
    assert budget.github_requests_used == len(github_requests) == 1
    assert budget.model_calls_used == len(model_requests) == 2
    feedback = next(m for m in model_requests[1]["messages"] if m["role"] == "tool")
    assert feedback["tool_call_id"] == "qualified-search"
    assert set(item["language"] for item in json.loads(feedback["content"])["data"]["items"]) == set(expected_languages)


def test_requires_readme_evidence_on_readme_keywords() -> None:
    assert requires_native_tool_calling_evidence("find tools with detailed README documentation") is True
    assert requires_native_tool_calling_evidence("find tools with python documentation") is True
    assert requires_native_tool_calling_evidence("find small python CLI tools") is False

