"""Minimal production application assembly and evidence-first MVP report."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from .agent_loop import AgentLoop, AgentLoopConfig, AgentRunResult
from .budget import RequestBudget
from .events import EventRecorder
from .evidence_ledger import EvidenceLedger, build_ledger
from .github_api import GitHubRestClient, normalize_language_name
from .model_client import OpenAICompatibleClient
from .report_validator import (
    CODE_FABRICATED_README,
    CODE_IMMATURE_RECOMMENDATION,
    CODE_METADATA_ONLY_VERDICT,
    CODE_UNATTRIBUTED_VERDICT,
    CODE_UNGROUNDED_REPOSITORY,
    CODE_UNVERIFIED_RECOMMENDATION,
    CODE_ZERO_RESULT_WITHOUT_README,
    Finding,
    validate_report,
)
from .tool_registry import build_github_tool_registry


def run_research_task(
    task: str,
    *,
    model: OpenAICompatibleClient,
    github: GitHubRestClient,
    budget: RequestBudget,
    events: EventRecorder | None = None,
    require_readme_override: bool | None = None,
) -> AgentRunResult:
    """Assemble the existing model, fixed tools, and Agent Loop once."""

    required_languages = infer_required_languages(task)
    github.required_languages = required_languages
    github.required_language = required_languages[0] if len(required_languages) == 1 else None
    require_readme = (
        require_readme_override
        if require_readme_override is not None
        else requires_native_tool_calling_evidence(task)
    )

    return AgentLoop(
        model=model,
        tools=build_github_tool_registry(github),
        budget=budget,
        events=events,
        config=AgentLoopConfig(
            finalization_reserve_calls=1,
            required_languages=required_languages,
            require_candidate_readme=require_readme,
        ),
    ).run(task)


def infer_required_language(task: str) -> str | None:
    """Compatibility helper: return a language only when the task names one."""

    languages = infer_required_languages(task)
    return languages[0] if len(languages) == 1 else None


def infer_required_languages(task: str) -> tuple[str, ...]:
    patterns = (
        ("Python", r"\bpython(?:\s*\d+(?:\.\d+)*)?\b"),
        ("C++", r"(?<!\w)c\+\+(?:\s*\d+)?(?!\w)|\bcpp\b|\bcxx\b"),
        ("TypeScript", r"\btypescript\b|\bts\b"),
        ("JavaScript", r"\bjavascript\b|\bjs\b"),
        ("Go", r"\bgolang\b|\bgo\s+(?:language|programming language)\b|\bgo(?:\s+(?:agent|agents|server|servers|library|libraries|sdk|framework|frameworks|runtime|runtimes|tool|tools)){0,2}\s+(?:repositories|repository|repos|projects|codebases|sdks|libraries|frameworks|packages|applications|apps|tools|runtimes)\b|\bin\s+go\b|\busing\s+go\b"),
        ("Rust", r"\brust\b"),
        ("Java", r"\bjava\b"),
        ("C#", r"\bc#\b|\bcsharp\b"),
        ("Ruby", r"\bruby\b"),
        ("PHP", r"\bphp\b"),
        ("Swift", r"\bswift[- ]language\b|\bswift\s+(?:programming language|language|repositories|repos|projects|codebases|sdks|libraries|frameworks|packages|applications|apps|tools|runtimes)\b|\bin\s+swift\b|\busing\s+swift\b"),
        ("Kotlin", r"\bkotlin\b"),
    )
    lowered = task.casefold()
    matches = [
        (match.start(), language)
        for language, pattern in patterns
        if (match := re.search(pattern, lowered)) is not None
    ]
    # A conjunction qualifies Go/Swift only when it joins actual language names.
    # Bare "Swift and lightweight" and "go and find Python" stay unqualified.
    language_tokens = list(re.finditer(
        r"(?<!\w)(?:python|c\+\+|cpp|cxx|typescript|ts|javascript|js|golang|go|rust|java|c#|csharp|ruby|php|swift|kotlin)(?!\w)",
        lowered,
    ))
    for previous, current in zip(language_tokens, language_tokens[1:]):
        if re.fullmatch(r"\s*(?:,|/|\band\b|\bor\b|,\s*(?:and|or))\s*", lowered[previous.end():current.start()]):
            matches.extend(
                (token.start(), normalize_language_name(token.group()))
                for token in (previous, current)
            )
    matches.sort()
    return tuple(dict.fromkeys(language for _, language in matches))


def requires_native_tool_calling_evidence(task: str) -> bool:
    lowered = task.casefold()
    return any(
        term in lowered
        for term in (
            "tool calling",
            "function calling",
            "native tool",
            "mcp",
            "readme",
            "documentation",
            "evidence",
            "verify",
            "verification",
            "capabilities",
            "capability",
            "research",
            "deep research",
            "compare",
            "comparison",
            "analyze",
            "analysis",
            "strengths",
            "limitations",
            "recommend",
            "recommendation",
            "evaluate",
            "evaluation",
            "推荐",
            "建议",
            "挑选",
            "选一个",
            "哪个",
            "方案",
            "适合",
            "生产可用",
            "文档",
            "证据",
            "验证",
            "核实",
            "调研",
            "研究",
            "对比",
            "比较",
            "选型",
            "评估",
            "分析",
            "特性",
        )
    )


def _source_urls(value: Any) -> list[str]:
    refs = getattr(value, "source_refs", None)
    if isinstance(refs, list):
        return [ref.url for ref in refs if getattr(ref, "url", None)]
    source = getattr(value, "source", None)
    return [source.url] if source is not None and getattr(source, "url", None) else []


_README_SOURCE_RE = re.compile(r"/repos/([^/]+)/([^/]+)/readme", re.IGNORECASE)
_IMMATURE_STARS = 50
_IMMATURE_DAYS = 183


def _readme_status_by_repo(tool_results: Iterable[Any]) -> dict[str, str]:
    """Map "owner/repo" to the README status this session actually retrieved.

    The report has to distinguish a candidate whose README was read from one that
    was only ever seen in a search result: a reader cannot tell them apart while
    every row renders identically.
    """

    statuses: dict[str, str] = {}
    for result in tool_results:
        if result.tool_name != "get_public_readme" or not result.ok:
            continue
        data = result.data
        target: str | None = None
        for url in _source_urls(data):
            match = _README_SOURCE_RE.search(url)
            if match:
                target = f"{match.group(1)}/{match.group(2)}".casefold()
                break
        if target is None:
            continue
        statuses[target] = str(getattr(data, "status", "unknown"))
    return statuses


def _maturity_note(candidate: Any, now: datetime) -> str | None:
    """Evidence-level note for a candidate that no production shortlist should carry."""

    stars = candidate.stargazers_count
    created = candidate.created_at
    if stars is None or created is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = (now - created).days
    if stars >= _IMMATURE_STARS or age_days >= _IMMATURE_DAYS:
        return None
    return f"{stars} star(s), {age_days} days old"


def _evidence_level_line(candidate: Any, statuses: dict[str, str]) -> str:
    status = statuses.get(candidate.full_name.casefold())
    if status is None:
        return "  - Evidence level: metadata only — README not requested in this session (not investigated)"
    return f"  - Evidence level: README {status} (fetched in this session)"


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


def _readme_statuses(result: AgentRunResult) -> dict[str, str]:
    """README status per repository: the tool trace, refined by recorded evidence."""

    statuses = _readme_status_by_repo(result.tool_results)
    for item in result.evidence:
        if item.readme_status:
            statuses[item.full_name.casefold()] = item.readme_status
    return statuses


def _report_is_complete(result: AgentRunResult, findings: list[Finding]) -> bool:
    """Report 'complete' only when evidence gathering passes its own check,
    at least one candidate has been accepted through investigation,
    and the final text makes no claim the session cannot support."""
    return (
        result.status == "completed"
        and result.evidence_complete is True
        and not findings
        and int(result.budget.get("accepted_candidate_count", 0)) > 0
    )


def _result_ledger(result: AgentRunResult) -> EvidenceLedger:
    """The ledger a result carries, rebuilt for results that predate it."""

    ledger = build_ledger(result.tool_results)
    for item in result.evidence:
        ledger.repositories[item.full_name.casefold()] = item
    for slug in result.investigated_repositories:
        if not ledger.get(slug):
            ledger.mark_investigated(slug)
    if not result.evidence and not result.investigated_repositories:
        raw_accepted = result.budget.get("accepted_candidate_ids", [])
        accepted_iter: Iterable[Any] = raw_accepted if isinstance(raw_accepted, Iterable) else ()
        for candidate_id in accepted_iter:
            value = str(candidate_id or "")
            if value and not value.startswith("__anonymous_"):
                ledger.mark_investigated(value)
    return ledger


_ALERTS: dict[str, tuple[str, str]] = {
    CODE_UNGROUNDED_REPOSITORY: (
        "Evidence Grounding Alert",
        "Unverified repository claims detected ({subjects}). These projects were not "
        "inspected with session tools and are based on unverified model assumptions.",
    ),
    CODE_UNATTRIBUTED_VERDICT: (
        "Evidence Attribution Alert",
        "Claims asserted without any repository inspected in this session ({subjects}). "
        "The claim names no inspected repository, so it cannot be traced to tool evidence.",
    ),
    CODE_UNVERIFIED_RECOMMENDATION: (
        "Evidence Level Alert",
        "Repositories recommended from search metadata only ({subjects}). GitHub "
        "search returned them in this session, but no README was read, so their "
        "capability claims remain unverified.",
    ),
    CODE_IMMATURE_RECOMMENDATION: (
        "Maturity Alert",
        "Immature repositories recommended without a caveat ({subjects}). Fewer than 50 "
        "stars and less than 6 months old: treat as unproven for production use.",
    ),
    CODE_METADATA_ONLY_VERDICT: (
        "Verdict Evidence Alert",
        "'Verified Match' was claimed for repositories whose README was never requested "
        "({subjects}). Metadata proves a licence and a star count, not that the library "
        "implements the required capability; treat those rows as 'Partial Match (metadata only)'.",
    ),
    CODE_FABRICATED_README: (
        "Evidence Fabrication Alert",
        "The report describes tool output this session never received ({detail}). "
        "Statements based on that output are unverified.",
    ),
    CODE_ZERO_RESULT_WITHOUT_README: (
        "Zero-result Alert",
        "The report declares no qualifying candidate ({detail}) although no README was "
        "fetched in this session: the requirement was never actually tested.",
    ),
}


def _alert_line(finding: Finding) -> str | None:
    """The report banner for one finding, or None for findings with no banner."""

    template = _ALERTS.get(finding.code)
    if template is None:
        return None
    label, body = template
    return f"- {label}: {body.format(subjects=', '.join(finding.subjects), detail=finding.message)}"


def _resolve_findings(result: AgentRunResult) -> list[Finding]:
    """Findings the loop recorded, or the rules re-run over a hand-built result."""

    if result.findings:
        return list(result.findings)
    return list(validate_report(result.final_text, _result_ledger(result)).findings)


def render_mvp_report(task: str, result: AgentRunResult) -> str:
    """Render an evidence-first report without inventing scores or facts."""

    findings = _resolve_findings(result)
    if result.completeness is not None:
        # The run graded its own coverage from the ledger; results built by hand
        # (older fixtures) fall back to the alert-only rule below.
        is_complete = result.completeness.complete
        completeness_detail = "; ".join(result.completeness.reasons)
    else:
        is_complete = _report_is_complete(result, findings)
        completeness_detail = ""

    lines = [
        "# GitHub Scout Agent MVP Report",
        "",
        f"- Task: {task}",
        f"- Status: {result.status}",
        f"- Termination: {result.termination_reason}",
        "- Completeness: "
        + ("complete" if is_complete else "partial")
        + (f" ({completeness_detail})" if completeness_detail else ""),
    ]
    for finding in findings:
        alert = _alert_line(finding)
        if alert is not None:
            lines.append(alert)
    for note in result.policy_notes:
        lines.append(f"- Evidence Policy Note: {note}")
    raw_fatal = result.budget.get("fatal_exhausted_dimensions", [])
    fatal_iter: Iterable[Any] = raw_fatal if isinstance(raw_fatal, Iterable) else ()
    stopped_by = [str(dimension) for dimension in fatal_iter]
    if stopped_by:
        lines.append(f"- Research stopped by budget: {', '.join(stopped_by)}")
    lines.extend(
        [
            "",
            "## Candidate evidence",
        ]
    )
    candidates = _candidate_records(result.tool_results)
    if not candidates:
        lines.append("- No accepted repository candidate was returned.")
    readme_statuses = _readme_statuses(result)
    now = datetime.now(timezone.utc)
    # A README-verified candidate and a raw search hit must not look identical;
    # list investigated rows first so the shortlist reads as what it is.
    candidates = sorted(candidates, key=lambda item: 0 if item.full_name.casefold() in readme_statuses else 1)
    for candidate in candidates:
        missing = ", ".join(candidate.missing_fields) if candidate.missing_fields else "none recorded"
        maturity_note = _maturity_note(candidate, now)
        lines.extend(
            [
                f"- [{candidate.full_name}]({candidate.html_url})",
                f"  - Public: {candidate.is_public}; archived: {candidate.is_archived}",
                f"  - Language: {candidate.language or 'Unknown'}",
                f"  - Stars: {candidate.stargazers_count if candidate.stargazers_count is not None else 'Unknown'}; forks: {candidate.forks_count if candidate.forks_count is not None else 'Unknown'}",
                f"  - Created: {candidate.created_at.date().isoformat() if candidate.created_at else 'Unknown'}; last push: {candidate.pushed_at.date().isoformat() if candidate.pushed_at else 'Unknown'}",
                f"  - Missing fields: {missing}",
                f"  - Sources: {', '.join(_source_urls(candidate)) or 'Unknown'}",
                _evidence_level_line(candidate, readme_statuses),
            ]
        )
        if maturity_note:
            lines.append(
                f"  - Maturity: immature ({maturity_note}) — below a production shortlist; "
                "treat as unproven regardless of how it ranks in search"
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
            summary["required_languages"] = getattr(data, "required_languages", [])
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
