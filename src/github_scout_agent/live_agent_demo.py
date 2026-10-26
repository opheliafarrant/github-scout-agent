"""Explicit S4-02 real GitHub + Mock LLM Agent Loop demo."""

from __future__ import annotations

import os
from typing import Literal

import httpx

from .agent_loop import AgentLoop, AgentRunResult
from .budget import RequestBudget
from .github_api import GitHubRestClient
from .mock_llm import ScenarioMockLLM
from .tool_registry import build_github_tool_registry


Scenario = Literal["search", "repository"]


def create_live_agent_loop(client: GitHubRestClient, scenario: Scenario, budget: RequestBudget) -> AgentLoop:
    if scenario == "search":
        model = ScenarioMockLLM(
            "readme_followup",
            initial_query="repo:octocat/Hello-World is:public",
        )
    else:
        model = ScenarioMockLLM("repository_followup")
    return AgentLoop(model=model, tools=build_github_tool_registry(client), budget=budget)


def run_live_agent_demo(scenario: Scenario) -> tuple[AgentRunResult, list[dict[str, object]]]:
    token = os.environ.get("GITHUB_TOKEN") or None
    budget = RequestBudget(
        max_github_requests=8,
        max_response_bytes=256 * 1024,
        max_candidates=5,
        max_model_calls=4,
        max_task_seconds=60,
        max_feedback_bytes=32 * 1024,
    )
    task = (
        "Search the public GitHub repository target and supplement one accepted candidate README."
        if scenario == "search"
        else "Inspect the specified public repository and supplement its README evidence."
    )
    with GitHubRestClient(
        token=token,
        budget=budget,
        timeout=10.0,
        max_retries=1,
        max_retry_delay=2.0,
    ) as client:
        loop = create_live_agent_loop(client, scenario, budget)
        result = loop.run(task)
        return result, list(client.request_log)


def summarize_live_result(result: AgentRunResult, request_log: list[dict[str, object]]) -> dict[str, object]:
    evidence: list[dict[str, object]] = []
    for tool_result in result.tool_results:
        item: dict[str, object] = {
            "tool_name": tool_result.tool_name,
            "ok": tool_result.ok,
            "request_count": tool_result.request_count,
        }
        data = tool_result.data
        if hasattr(data, "full_name"):
            item.update(
                full_name=data.full_name,
                html_url=data.html_url,
                is_public=data.is_public,
                archived=data.is_archived,
                missing_fields=data.missing_fields,
            )
        elif hasattr(data, "status"):
            item.update(status=data.status, truncated=getattr(data, "truncated", None), trust_label=getattr(data, "trust_label", None))
        elif hasattr(data, "items"):
            item.update(
                candidates=[candidate.full_name for candidate in data.items],
                incomplete_results=data.incomplete_results,
                excluded_non_public_count=data.excluded_non_public_count,
            )
        elif hasattr(data, "languages"):
            item.update(language_count=len(data.languages), topic_count=len(data.topics))
        if tool_result.error:
            item["error"] = tool_result.error.model_dump()
        evidence.append(item)
    return {
        "status": result.status,
        "termination_reason": result.termination_reason,
        "budget": result.budget,
        "tool_results": evidence,
        "request_log": request_log,
        "event_count": len(result.events),
    }
