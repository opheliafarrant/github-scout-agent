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
from datetime import datetime, timezone

from github_scout_agent.agent_loop import AgentRunResult
from github_scout_agent.report_validator import (
    CODE_FABRICATED_README,
    CODE_IMMATURE_RECOMMENDATION,
    CODE_METADATA_ONLY_VERDICT,
    CODE_UNVERIFIED_RECOMMENDATION,
    Completeness,
    Finding,
)
from github_scout_agent.github_models import ReadmeEvidence, RepositoryRecord, SourceRef, ToolError, ToolResult
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

    budget = RequestBudget(max_candidates=3, max_model_calls=3, max_github_requests=8)
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
    # A Chinese recommendation request asks for a conclusion about projects, so it
    # needs the same README evidence an explicit "readme" keyword would demand.
    assert requires_native_tool_calling_evidence("推荐一个扫描件 PDF 表格结构化方案") is True
    assert requires_native_tool_calling_evidence("帮我挑一个生产可用的 RAG 框架") is True


def test_render_mvp_report_on_rate_limited_termination() -> None:
    result = AgentRunResult(
        status="terminated",
        termination_reason="rate_limited",
        final_text=(
            "GitHub API rate limit exceeded (HTTP 403). No repositories could be retrieved "
            "because the unauthenticated rate limit (60 requests/hour) was reached. "
            "Please configure a GITHUB_TOKEN or wait for the rate limit to reset."
        ),
        messages=[],
        tool_results=[
            ToolResult(
                tool_name="search_public_repositories",
                ok=False,
                error=ToolError(code="rate_limited", category="rate_limit", message="GitHub rate limit blocked the request", status_code=403),
                request_count=1,
            )
        ],
        events=[],
        budget={"github_requests_used": 1, "model_calls_used": 2},
        evidence_complete=False,
        evidence_gaps=["no candidates discovered yet — broaden the search or try different keywords"],
    )
    report = render_mvp_report("find tools", result)
    assert "- Status: terminated" in report
    assert "- Termination: rate_limited" in report
    assert "- Completeness: partial" in report
    assert "No accepted repository candidate was returned" in report
    assert "GitHub API rate limit exceeded (HTTP 403)" in report
    assert "rate_limited" in report


def test_render_mvp_report_names_the_budget_that_stopped_research() -> None:
    result = AgentRunResult(
        status="terminated",
        termination_reason="feedback_bytes",
        final_text="Evidence reviewed.",
        messages=[],
        tool_results=[],
        events=[],
        budget={"fatal_exhausted_dimensions": ["feedback_bytes"], "feedback_bytes_used": 600_000},
        evidence_complete=False,
        evidence_gaps=["prompt-size budget exhausted"],
    )

    report = render_mvp_report("find tools", result)

    assert "- Research stopped by budget: feedback_bytes" in report
    assert "- Termination: feedback_bytes" in report


def test_acceptance_scenario_deep_evidence_research_and_exclusion() -> None:
    task = (
        "Find three open-source Python AI agents that can search GitHub repositories, "
        "analyze README files, and recommend open-source projects with evidence. "
        "Verify their capabilities from actual README documentation, compare their strengths "
        "and limitations, and exclude unrelated frameworks or financial platforms."
    )

    import base64

    def github_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/search/repositories":
            items = [
                {
                    "id": 101,
                    "full_name": "agent-scout/scout-agent",
                    "html_url": "https://github.com/agent-scout/scout-agent",
                    "private": False,
                    "visibility": "public",
                    "fork": False,
                    "archived": False,
                    "disabled": False,
                    "stargazers_count": 350,
                    "forks_count": 45,
                    "language": "Python",
                    "description": "Python AI agent for searching GitHub repos and analyzing documentation.",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "pushed_at": "2024-01-01T00:00:00Z",
                    "license": {"key": "mit", "name": "MIT License", "spdx_id": "MIT"},
                    "topics": ["agent", "github", "research"],
                },
                {
                    "id": 102,
                    "full_name": "doc-analyzer/repo-scout",
                    "html_url": "https://github.com/doc-analyzer/repo-scout",
                    "private": False,
                    "visibility": "public",
                    "fork": False,
                    "archived": False,
                    "disabled": False,
                    "stargazers_count": 210,
                    "forks_count": 20,
                    "language": "Python",
                    "description": "AI agent that inspects GitHub repositories and analyzes README files.",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "pushed_at": "2024-01-01T00:00:00Z",
                    "license": {"key": "apache-2.0", "name": "Apache-2.0", "spdx_id": "Apache-2.0"},
                    "topics": ["readme", "analyzer", "agent"],
                },
                {
                    "id": 103,
                    "full_name": "trade-ai/crypto-scout",
                    "html_url": "https://github.com/trade-ai/crypto-scout",
                    "private": False,
                    "visibility": "public",
                    "fork": False,
                    "archived": False,
                    "disabled": False,
                    "stargazers_count": 980,
                    "forks_count": 150,
                    "language": "Python",
                    "description": "Crypto algorithmic trading bot framework with GitHub data scraping.",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "pushed_at": "2024-01-01T00:00:00Z",
                    "license": {"key": "mit", "name": "MIT License", "spdx_id": "MIT"},
                    "topics": ["trading", "crypto", "bot"],
                },
                {
                    "id": 104,
                    "full_name": "recommender/code-scout",
                    "html_url": "https://github.com/recommender/code-scout",
                    "private": False,
                    "visibility": "public",
                    "fork": False,
                    "archived": False,
                    "disabled": False,
                    "stargazers_count": 180,
                    "forks_count": 15,
                    "language": "Python",
                    "description": "Agentic tool for recommending GitHub projects based on README evidence.",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "pushed_at": "2024-01-01T00:00:00Z",
                    "license": {"key": "mit", "name": "MIT License", "spdx_id": "MIT"},
                    "topics": ["recommendation", "scout", "agent"],
                },
            ]
            return httpx.Response(200, json={"total_count": len(items), "items": items}, request=request)

        if path.endswith("/readme"):
            parts = path.split("/")
            repo_name = f"{parts[2]}/{parts[3]}"
            if "crypto-scout" in repo_name:
                content = (
                    "# Crypto Scout Trading Framework\n\n"
                    "Automated high-frequency trading bot for Binance, Coinbase, and Uniswap. "
                    "Features backtesting, algorithmic execution, and financial portfolio management."
                )
            elif "scout-agent" in repo_name:
                content = (
                    "# Scout Agent\n\n"
                    "An open-source Python AI research agent designed to query the GitHub REST API, "
                    "download and parse repository README files, and synthesize evidence for open-source project selection."
                )
            elif "repo-scout" in repo_name:
                content = (
                    "# Repo Scout Documentation Analyzer\n\n"
                    "Autonomous agent that analyzes repository documentation, extracts functional requirements, "
                    "and validates claims against README specifications."
                )
            elif "code-scout" in repo_name:
                content = (
                    "# Code Scout Project Recommender\n\n"
                    "Evidence-backed GitHub project recommender agent. Evaluates repository READMEs and licenses "
                    "to compare libraries and recommend suitable projects."
                )
            else:
                content = "# Generic README"
            return httpx.Response(
                200,
                json={"encoding": "base64", "content": base64.b64encode(content.encode()).decode(), "size": len(content)},
                request=request,
            )

        return httpx.Response(404, json={"message": "Not Found"}, request=request)

    model_turns = 0

    def model_handler(request: httpx.Request) -> httpx.Response:
        nonlocal model_turns
        model_turns += 1

        if model_turns == 1:
            tool_calls = [{
                "id": "search-1",
                "type": "function",
                "function": {
                    "name": "search_public_repositories",
                    "arguments": json.dumps({"query": "github agent research analyze language:python"}),
                },
            }]
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": tool_calls}, "finish_reason": "tool_calls"}]},
                request=request,
            )

        if model_turns == 2:
            tool_calls = [
                {
                    "id": "readme-1",
                    "type": "function",
                    "function": {"name": "get_public_readme", "arguments": json.dumps({"owner": "agent-scout", "repo": "scout-agent"})},
                },
                {
                    "id": "readme-2",
                    "type": "function",
                    "function": {"name": "get_public_readme", "arguments": json.dumps({"owner": "trade-ai", "repo": "crypto-scout"})},
                },
                {
                    "id": "readme-3",
                    "type": "function",
                    "function": {"name": "get_public_readme", "arguments": json.dumps({"owner": "recommender", "repo": "code-scout"})},
                },
            ]
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": tool_calls}, "finish_reason": "tool_calls"}]},
                request=request,
            )

        if model_turns == 3:
            tool_calls = [
                {
                    "id": "readme-4",
                    "type": "function",
                    "function": {"name": "get_public_readme", "arguments": json.dumps({"owner": "doc-analyzer", "repo": "repo-scout"})},
                },
            ]
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": tool_calls}, "finish_reason": "tool_calls"}]},
                request=request,
            )

        explanation = (
            "## 1. Verified Facts & Candidate Verification\n\n"
            "- [agent-scout/scout-agent](https://github.com/agent-scout/scout-agent):\n"
            "  - Status: Verified Match (Python, MIT, 350 Stars)\n"
            "  - Verified Capabilities: Queries GitHub REST API, parses README files, synthesizes evidence for project selection.\n"
            "  - Strengths: Directly addresses GitHub research with evidence; active maintenance.\n"
            "  - Limitations: Read-only query mode.\n\n"
            "- [doc-analyzer/repo-scout](https://github.com/doc-analyzer/repo-scout):\n"
            "  - Status: Verified Match (Python, Apache-2.0, 210 Stars)\n"
            "  - Verified Capabilities: Autonomous agent that analyzes repository documentation and extracts functional requirements.\n"
            "  - Strengths: In-depth documentation parsing and requirement extraction.\n"
            "  - Limitations: Requires local model or API key configuration.\n\n"
            "- [recommender/code-scout](https://github.com/recommender/code-scout):\n"
            "  - Status: Verified Match (Python, MIT, 180 Stars)\n"
            "  - Verified Capabilities: Recommends GitHub projects based on README evidence and license comparison.\n"
            "  - Strengths: Transparent recommendation reports with evidence citations.\n"
            "  - Limitations: Focuses on libraries rather than full applications.\n\n"
            "- [trade-ai/crypto-scout](https://github.com/trade-ai/crypto-scout):\n"
            "  - Status: EXCLUDED (Financial Platform)\n"
            "  - Reason for Rejection: README documentation confirms this is an algorithmic crypto trading framework (Binance/Uniswap), "
            "completely unrelated to GitHub repository research or project recommendation.\n\n"
            "## 2. Comparison & Trade-offs\n\n"
            "| Project | Core Strength | Key Limitation | Best For |\n"
            "| :--- | :--- | :--- | :--- |\n"
            "| agent-scout/scout-agent | Search + README synthesis | REST GET-only | General GitHub scout tasks |\n"
            "| doc-analyzer/repo-scout | Deep doc analysis | Higher token usage | Deep requirement validation |\n"
            "| recommender/code-scout | Project recommendations | Library focus | Tech stack evaluation |\n\n"
            "## 3. Evidence Gaps & Unknowns\n\n"
            "- None for the three qualifying candidates. All three had their README files retrieved and verified.\n\n"
            "## 4. Final Recommendation\n\n"
            "Recommend `agent-scout/scout-agent` as the primary technical choice for end-to-end repository research, "
            "complemented by `doc-analyzer/repo-scout` for deep document parsing."
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": explanation}, "finish_reason": "stop"}]},
            request=request,
        )

    budget = RequestBudget(max_candidates=4, max_github_requests=10, max_model_calls=5, max_feedback_bytes=50_000)
    with OpenAICompatibleClient(
        base_url="https://model.example.test/v1",
        api_key=SecretStr("acceptance-test-key"),
        model="mock-model",
        budget=budget,
        transport=httpx.MockTransport(model_handler),
    ) as model, GitHubRestClient(budget=budget, transport=httpx.MockTransport(github_handler), max_retries=0) as github:
        result = run_research_task(task, model=model, github=github, budget=budget)

    report = render_mvp_report(task, result)

    assert result.status == "completed"
    assert result.termination_reason == "model_finalized"
    assert result.evidence_complete is True
    assert "- Completeness: complete" in report
    assert "agent-scout/scout-agent" in report
    assert "doc-analyzer/repo-scout" in report
    assert "recommender/code-scout" in report
    assert "EXCLUDED (Financial Platform)" in report
    assert "trade-ai/crypto-scout" in report
    assert "Comparison & Trade-offs" in report
    assert "get_public_readme" in report
    assert budget.github_requests_used == 5


def test_ungrounded_candidate_claims_trigger_evidence_alert_and_partial_completeness() -> None:
    explanation_with_hallucination = (
        "## 1. Verified Facts & Candidate Verification\n\n"
        "- [octo/demo](https://github.com/octo/demo):\n"
        "  - Status: Verified Match (Python, 10 Stars)\n"
        "  - Verified Capabilities: Demo agent capability.\n\n"
        "- [langchain-ai/langchain](https://github.com/langchain-ai/langchain):\n"
        "  - Status: Verified Match (Python, 90000 Stars)\n"
        "  - Verified Capabilities: Famous RAG framework.\n\n"
        "## 4. Final Recommendation\n\n"
        "Recommend langchain-ai/langchain as the primary choice."
    )

    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text=explanation_with_hallucination,
        tool_results=[],
        budget={"accepted_candidate_count": 1, "accepted_candidate_ids": ["octo/demo"]},
        evidence_complete=True,
    )
    report = render_mvp_report("find RAG frameworks", result)
    assert "- Completeness: partial" in report
    assert "- Evidence Grounding Alert: Unverified repository claims detected (langchain-ai/langchain)" in report


def test_grounded_candidates_pass_without_alert() -> None:
    explanation_clean = (
        "## 1. Verified Facts & Candidate Verification\n\n"
        "- [octo/demo](https://github.com/octo/demo):\n"
        "  - Status: Verified Match (Python, 10 Stars)\n"
        "  - Verified Capabilities: Demo agent capability.\n\n"
        "## 4. Final Recommendation\n\n"
        "Recommend octo/demo."
    )

    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text=explanation_clean,
        tool_results=[],
        budget={"accepted_candidate_count": 1, "accepted_candidate_ids": ["octo/demo"]},
        evidence_complete=True,
    )
    report = render_mvp_report("find frameworks", result)
    assert "- Completeness: complete" in report
    assert "Evidence Grounding Alert" not in report


def test_uninvestigated_mention_in_evidence_gaps_does_not_trigger_false_positive_alert() -> None:
    explanation_with_gap_mention = (
        "## 1. Verified Facts & Candidate Verification\n\n"
        "- [octo/demo](https://github.com/octo/demo):\n"
        "  - Status: Verified Match (Python, 10 Stars)\n"
        "  - Verified Capabilities: Demo agent capability.\n\n"
        "## 3. Evidence Gaps & Unknowns\n\n"
        "- Note: Well-known frameworks such as langchain-ai/langchain and run-llama/llama_index were not investigated in this session.\n\n"
        "## 4. Final Recommendation\n\n"
        "Recommend octo/demo based on verified evidence."
    )

    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text=explanation_with_gap_mention,
        tool_results=[],
        budget={"accepted_candidate_count": 1, "accepted_candidate_ids": ["octo/demo"]},
        evidence_complete=True,
    )
    report = render_mvp_report("find frameworks", result)
    assert "- Completeness: complete" in report
    assert "Evidence Grounding Alert" not in report


def test_search_tool_description_guides_maturity_sorting() -> None:
    from github_scout_agent.tool_registry import build_github_tool_registry
    from github_scout_agent.github_api import GitHubRestClient
    budget = RequestBudget()
    with GitHubRestClient(budget=budget) as github:
        registry = build_github_tool_registry(github)
        schemas = registry.schemas()
        search_schema = next(s for s in schemas if s["function"]["name"] == "search_public_repositories")
        desc = search_schema["function"]["description"]
        assert "sort='stars'" in desc
        assert "stars:>50" in desc




# -- T63 report grounding gate --------------------------------------------
# The rendered report must downgrade on claims the session cannot support,
# including Chinese reports and bare brand names with no inspected repository.

def test_chinese_ungrounded_recommendation_triggers_alert_and_partial() -> None:
    final_text = (
        "## 1. 已核实事实\n\n"
        "- [octo/demo](https://github.com/octo/demo):\n"
        "  - 状态：完全匹配（Python，10 Stars）\n\n"
        "## 4. 最终推荐\n\n"
        "推荐 https://github.com/langchain-ai/langchain 作为首选方案。\n"
    )
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text=final_text,
        tool_results=[],
        budget={"accepted_candidate_count": 1, "accepted_candidate_ids": ["octo/demo"]},
        evidence_complete=True,
    )
    report = render_mvp_report("寻找 RAG 框架", result)
    assert "- Completeness: partial" in report
    assert (
        "- Evidence Grounding Alert: Unverified repository claims detected (langchain-ai/langchain)"
        in report
    )


def test_verdict_without_inspected_repository_triggers_attribution_alert() -> None:
    final_text = (
        "## 1. 已核实事实\n\n"
        "- [octo/demo](https://github.com/octo/demo): 状态：完全匹配\n\n"
        "## 4. 最终推荐\n\n"
        "- Genie: Verified Match\n"
    )
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text=final_text,
        tool_results=[],
        budget={"accepted_candidate_count": 1, "accepted_candidate_ids": ["octo/demo"]},
        evidence_complete=True,
    )
    report = render_mvp_report("find frameworks", result)
    assert "- Completeness: partial" in report
    assert "Evidence Attribution Alert: Claims asserted without any repository inspected" in report
    assert "(Genie)" in report


def test_report_uses_recorded_investigated_whitelist_over_derived_candidates() -> None:
    final_text = (
        "## 4. Final Recommendation\n\n"
        "Recommend https://github.com/octo/demo as the primary choice.\n"
    )
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text=final_text,
        tool_results=[],
        budget={"accepted_candidate_count": 1},
        evidence_complete=True,
        investigated_repositories=["octo/demo"],
    )
    report = render_mvp_report("find frameworks", result)
    assert "- Completeness: complete" in report
    assert "Evidence Grounding Alert" not in report

def test_maturity_alert_downgrades_report_to_partial() -> None:
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text=(
            "## 4. Final Recommendation\n\n"
            "- [octo/toy](https://github.com/octo/toy): Status: Verified Match\n"
        ),
        tool_results=[],
        budget={"accepted_candidate_count": 1},
        evidence_complete=True,
        investigated_repositories=["octo/toy"],
        findings=[Finding(code=CODE_IMMATURE_RECOMMENDATION, subjects=("octo/toy",))],
    )

    report = render_mvp_report("find frameworks", result)

    assert "- Completeness: partial" in report
    assert (
        "Maturity Alert: Immature repositories recommended without a caveat (octo/toy)" in report
    )
    assert "Fewer than 50 stars and less than 6 months old" in report


def test_fabricated_evidence_claim_downgrades_report_and_renders_alert() -> None:
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text="The README content returned null for every candidate.",
        tool_results=[],
        budget={"accepted_candidate_count": 1},
        evidence_complete=False,
        investigated_repositories=["octo/toy"],
        findings=[Finding(code=CODE_FABRICATED_README)],
    )

    report = render_mvp_report("find frameworks", result)

    assert "- Completeness: partial" in report
    assert "Evidence Fabrication Alert" in report
    assert "never received" in report


def test_report_renders_policy_notes_and_candidate_maturity_dates() -> None:
    record = RepositoryRecord(
        full_name="octo/toy",
        html_url="https://github.com/octo/toy",
        stargazers_count=3,
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        pushed_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text="Evidence reviewed.",
        tool_results=[
            ToolResult(tool_name="get_public_repository", ok=True, data=record, request_count=1)
        ],
        budget={"accepted_candidate_count": 1},
        evidence_complete=True,
        investigated_repositories=["octo/toy"],
        policy_notes=[
            "The task asked for answers without tool evidence; this run applied the evidence-first policy instead."
        ],
    )

    report = render_mvp_report("find frameworks", result)

    assert "  - Created: 2026-10-01; last push: 2026-10-20" in report
    assert "- Evidence Policy Note: The task asked for answers without tool evidence" in report



def test_metadata_only_verdict_downgrades_report_and_renders_alert() -> None:
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text=(
            "## octo/tool\n- Qualification Verdict: Verified Match\n\n"
            "## Final Recommendation\n1. Use octo/tool\n"
        ),
        tool_results=[],
        budget={"accepted_candidate_count": 1},
        evidence_complete=False,
        investigated_repositories=["octo/tool"],
        findings=[Finding(code=CODE_METADATA_ONLY_VERDICT, subjects=("octo/tool",))],
    )

    report = render_mvp_report("find a table extractor", result)

    assert "- Completeness: partial" in report
    assert "Verdict Evidence Alert" in report
    assert "README was never requested (octo/tool)" in report
    assert "Partial Match (metadata only)" in report


def test_candidate_evidence_separates_readme_verified_rows_from_search_hits() -> None:
    verified = RepositoryRecord(
        full_name="deepset-ai/haystack",
        html_url="https://github.com/deepset-ai/haystack",
        stargazers_count=26876,
        created_at=datetime(2019, 11, 14, tzinfo=timezone.utc),
    )
    toy = RepositoryRecord(
        full_name="someone/toy-rag",
        html_url="https://github.com/someone/toy-rag",
        stargazers_count=1,
        created_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
    )
    readme = ReadmeEvidence(
        status="truncated",
        content="RAG framework",
        source=SourceRef(
            url="https://api.github.com/repos/deepset-ai/haystack/readme",
            captured_at=datetime(2026, 10, 29, tzinfo=timezone.utc),
        ),
    )
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text="Compared the README.",
        tool_results=[
            ToolResult(tool_name="get_public_repository", ok=True, data=toy, request_count=1),
            ToolResult(tool_name="get_public_readme", ok=True, data=readme, request_count=1),
            ToolResult(tool_name="get_public_repository", ok=True, data=verified, request_count=1),
        ],
        budget={"accepted_candidate_count": 1},
        evidence_complete=True,
        investigated_repositories=["deepset-ai/haystack"],
    )

    report = render_mvp_report("pick a production RAG framework", result)

    assert "  - Evidence level: README truncated (fetched in this session)" in report
    assert "  - Evidence level: metadata only — README not requested in this session (not investigated)" in report
    # The investigated candidate is listed before a raw search hit.
    assert report.index("deepset-ai/haystack") < report.index("someone/toy-rag")
    assert "  - Maturity: immature (1 star(s)" in report


def test_candidate_evidence_does_not_flag_mature_repositories_as_immature() -> None:
    mature = RepositoryRecord(
        full_name="run-llama/llama_index",
        html_url="https://github.com/run-llama/llama_index",
        stargazers_count=52726,
        created_at=datetime(2022, 11, 2, tzinfo=timezone.utc),
    )
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text="Compared metadata.",
        tool_results=[
            ToolResult(tool_name="get_public_repository", ok=True, data=mature, request_count=1)
        ],
        budget={"accepted_candidate_count": 1},
        evidence_complete=True,
        investigated_repositories=["run-llama/llama_index"],
    )

    report = render_mvp_report("pick a production RAG framework", result)

    assert "  - Maturity: immature" not in report
    assert "  - Evidence level: metadata only" in report


def test_renderer_renders_the_findings_the_run_recorded() -> None:
    # One channel: the loop decides what is wrong with the report, the renderer
    # prints that decision. Re-deriving the verdict here is how the report and the
    # completeness flag drifted apart in the first place.
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text="## Final Recommendation\n\n1. Use octo/demo\n",
        tool_results=[],
        budget={"accepted_candidate_count": 1},
        evidence_complete=False,
        investigated_repositories=["octo/demo"],
        findings=[Finding(code=CODE_IMMATURE_RECOMMENDATION, subjects=("octo/demo",))],
    )

    report = render_mvp_report("find agents", result)

    assert "- Completeness: partial" in report
    assert "Maturity Alert: Immature repositories recommended without a caveat (octo/demo)" in report
    assert "Evidence Grounding Alert" not in report


def test_renderer_renders_the_unverified_recommendation_alert() -> None:
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text="## Final Recommendation\n\n1. Use octo/demo\n",
        tool_results=[],
        budget={"accepted_candidate_count": 1},
        evidence_complete=False,
        investigated_repositories=["octo/demo"],
        findings=[
            Finding(code=CODE_UNVERIFIED_RECOMMENDATION, subjects=("octo/demo",))
        ],
    )

    report = render_mvp_report("find agents", result)

    assert "- Completeness: partial" in report
    assert "Evidence Level Alert: Repositories recommended from search metadata only (octo/demo)" in report


def test_renderer_prints_why_a_graded_report_is_partial() -> None:
    # A complete-looking header over a metadata-only shortlist is the failure
    # mode: the body was honest, the label was not. The reason must travel with
    # the label, not live only in a policy note further down.
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text="## Final Recommendation\n\n- octo/demo: Partial Match (metadata only)\n",
        tool_results=[],
        budget={"accepted_candidate_count": 1, "max_candidates": 3},
        evidence_complete=True,
        investigated_repositories=["octo/demo"],
        requested_options=12,
        completeness=Completeness(
            complete=False,
            reasons=(
                "the task asked for 12 options while the candidate budget compares at most 3 (system limit)",
            ),
        ),
    )

    report = render_mvp_report("find 12 agents", result)

    assert (
        "- Completeness: partial (the task asked for 12 options while the candidate "
        "budget compares at most 3 (system limit))"
    ) in report


def test_renderer_keeps_a_verified_run_label_complete() -> None:
    result = AgentRunResult(
        status="completed",
        termination_reason="model_finalized",
        final_text="## Final Recommendation\n\n- octo/demo: Verified Match\n",
        tool_results=[],
        budget={"accepted_candidate_count": 1, "max_candidates": 3},
        evidence_complete=True,
        investigated_repositories=["octo/demo"],
        requested_options=1,
        completeness=Completeness(complete=True, reasons=()),
    )

    report = render_mvp_report("find one agent", result)

    assert "- Completeness: complete" in report
    assert "- Completeness: partial" not in report
