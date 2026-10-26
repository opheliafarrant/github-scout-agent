"""Minimal production application assembly and evidence-first MVP report."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from .agent_loop import AgentLoop, AgentLoopConfig, AgentRunResult
from .budget import RequestBudget
from .github_api import GitHubRestClient
from .model_client import OpenAICompatibleClient
from .tool_registry import build_github_tool_registry


def run_research_task(
    task: str,
    *,
    model: OpenAICompatibleClient,
    github: GitHubRestClient,
    budget: RequestBudget,
) -> AgentRunResult:
    """Assemble the existing model, fixed tools, and Agent Loop once."""

    return AgentLoop(
        model=model,
        tools=build_github_tool_registry(github),
        budget=budget,
        config=AgentLoopConfig(finalization_reserve_calls=1),
    ).run(task)


def _source_urls(value: Any) -> list[str]:
    refs = getattr(value, "source_refs", None)
    if isinstance(refs, list):
        return [ref.url for ref in refs if getattr(ref, "url", None)]
    source = getattr(value, "source", None)
    return [source.url] if source is not None and getattr(source, "url", None) else []


def _candidate_records(tool_results: Iterable[Any]) -> list[Any]:
    records: dict[str, Any] = {}
    for result in tool_results:
        data = result.data
        for item in getattr(data, "items", []) or []:
            records.setdefault(item.full_name.casefold(), item)
        if getattr(data, "full_name", None):
            records.setdefault(data.full_name.casefold(), data)
    return list(records.values())


def render_mvp_report(task: str, result: AgentRunResult) -> str:
    """Render an evidence-first report without inventing scores or facts."""

    lines = [
        "# GitHub Scout Agent MVP Report",
        "",
        f"- Task: {task}",
        f"- Status: {result.status}",
        f"- Termination: {result.termination_reason}",
        f"- Completeness: {'complete' if result.status == 'completed' else 'partial'}",
        "",
        "## Candidate evidence",
    ]
    candidates = _candidate_records(result.tool_results)
    if not candidates:
        lines.append("- No accepted repository candidate was returned.")
    for candidate in candidates:
        missing = ", ".join(candidate.missing_fields) if candidate.missing_fields else "none recorded"
        lines.extend(
            [
                f"- [{candidate.full_name}]({candidate.html_url})",
                f"  - Public: {candidate.is_public}; archived: {candidate.is_archived}",
                f"  - Stars: {candidate.stargazers_count if candidate.stargazers_count is not None else 'Unknown'}; forks: {candidate.forks_count if candidate.forks_count is not None else 'Unknown'}",
                f"  - Missing fields: {missing}",
                f"  - Sources: {', '.join(_source_urls(candidate)) or 'Unknown'}",
            ]
        )

    lines.extend(["", "## Tool and evidence trace"])
    for result_item in result.tool_results:
        data = result_item.data
        summary: dict[str, Any] = {"tool": result_item.tool_name, "ok": result_item.ok, "requests": result_item.request_count}
        if result_item.error:
            summary["error"] = result_item.error.model_dump()
        elif getattr(data, "status", None):
            summary["status"] = data.status
            summary["trust_label"] = getattr(data, "trust_label", None)
            summary["sources"] = _source_urls(data)
        elif getattr(data, "languages", None) is not None:
            summary["language_count"] = len(data.languages)
            summary["topic_count"] = len(data.topics)
            summary["sources"] = _source_urls(data)
        elif getattr(data, "incomplete_results", None) is not None:
            summary["candidate_count"] = len(data.items)
            summary["incomplete_results"] = data.incomplete_results
        lines.append(f"- `{result_item.tool_name}`: `{json.dumps(summary, ensure_ascii=False, sort_keys=True)}`")

    lines.extend(
        [
            "",
            "## Model explanation",
            result.final_text or "No final model explanation was returned.",
            "",
            "## Budget and termination evidence",
            f"```json\n{json.dumps(result.budget, ensure_ascii=False, sort_keys=True, indent=2)}\n```",
            "",
            "Model explanation is not an independent verification of repository facts; repository facts above come from validated tool results.",
        ]
    )
    return "\n".join(lines)
