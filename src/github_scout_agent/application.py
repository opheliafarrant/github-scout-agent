"""Minimal production application assembly and evidence-first MVP report."""

from __future__ import annotations

import json
import re
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

    required_language = infer_required_language(task)
    github.required_language = required_language

    return AgentLoop(
        model=model,
        tools=build_github_tool_registry(github),
        budget=budget,
        config=AgentLoopConfig(
            finalization_reserve_calls=1,
            required_language=required_language,
            require_candidate_readme=requires_native_tool_calling_evidence(task),
        ),
    ).run(task)


def infer_required_language(task: str) -> str | None:
    patterns = (
        (r"\bpython(?:\s*\d+(?:\.\d+)*)?\b", "Python"),
        (r"(?<!\w)c\+\+(?:\s*\d+)?(?!\w)|\bcpp\b|\bcxx\b", "C++"),
        (r"\btypescript\b|\bts\b", "TypeScript"),
        (r"\bjavascript\b|\bjs\b", "JavaScript"),
        (r"\bgolang\b|\bgo\b", "Go"),
        (r"\brust\b", "Rust"),
        (r"\bjava\b", "Java"),
        (r"\bc#\b|\bcsharp\b", "C#"),
        (r"\bruby\b", "Ruby"),
        (r"\bphp\b", "PHP"),
        (r"\bswift\b", "Swift"),
        (r"\bkotlin\b", "Kotlin"),
    )
    lowered = task.casefold()
    for pattern, language in patterns:
        if re.search(pattern, lowered):
            return language
    return None


def requires_native_tool_calling_evidence(task: str) -> bool:
    lowered = task.casefold()
    return any(term in lowered for term in ("tool calling", "function calling", "native tool", "mcp"))


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
            key = item.full_name.casefold()
            current = records.get(key)
            if current is None or len(item.missing_fields) < len(current.missing_fields):
                records[key] = item
        if getattr(data, "full_name", None):
            key = data.full_name.casefold()
            current = records.get(key)
            if current is None or len(data.missing_fields) < len(current.missing_fields):
                records[key] = data
    return list(records.values())


def _report_is_complete(result: AgentRunResult) -> bool:
    if result.status != "completed":
        return False
    if result.evidence_complete is False:
        return False
    incomplete_evidence_codes = {
        "github_requests_insufficient",
        "not_executed_before_finalization",
    }
    return not any(
        item.error is not None and item.error.code in incomplete_evidence_codes
        for item in result.tool_results
    )


def render_mvp_report(task: str, result: AgentRunResult) -> str:
    """Render an evidence-first report without inventing scores or facts."""

    lines = [
        "# GitHub Scout Agent MVP Report",
        "",
        f"- Task: {task}",
        f"- Status: {result.status}",
        f"- Termination: {result.termination_reason}",
        f"- Completeness: {'complete' if _report_is_complete(result) else 'partial'}",
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
                f"  - Language: {candidate.language or 'Unknown'}",
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
            summary["unverified_candidate_count"] = len(getattr(data, "unverified_items", []) or [])
            summary["excluded_language_count"] = getattr(data, "excluded_language_count", 0)
            summary["required_language"] = getattr(data, "required_language", None)
        lines.append(f"- `{result_item.tool_name}`: `{json.dumps(summary, ensure_ascii=False, sort_keys=True)}`")

    if result.evidence_gaps:
        lines.extend(["", "## Evidence gaps"])
        lines.extend(f"- {gap}" for gap in result.evidence_gaps)

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
