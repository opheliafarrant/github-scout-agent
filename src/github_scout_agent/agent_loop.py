"""A small synchronous, feedback-driven Agent Loop for S3-03."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .agent_models import AgentMessage, AssistantResponse, ModelCallError, ModelProtocolError, ToolCall
from .budget import BudgetExceeded, RequestBudget
from .events import AgentEvent, EventRecorder
from .github_models import ToolError, ToolResult
from .tool_registry import ToolRegistry


class ModelAdapter(Protocol):
    def complete(
        self,
        messages: list[AgentMessage],
        tools: list[dict[str, Any]],
        *,
        tool_choice: str = "auto",
    ) -> AssistantResponse:
        ...


@dataclass(frozen=True)
class AgentLoopConfig:
    max_protocol_retries: int = 1
    max_repeated_actions: int = 1
    max_rate_limit_retries: int = 2
    finalization_reserve_calls: int = 0
    required_language: str | None = None
    required_languages: tuple[str, ...] = ()
    require_candidate_readme: bool = False


class AgentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    termination_reason: str
    final_text: str | None = None
    messages: list[AgentMessage] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    events: list[AgentEvent] = Field(default_factory=list)
    budget: dict[str, object]
    evidence_complete: bool | None = None
    evidence_gaps: list[str] = Field(default_factory=list)
    feedback_trace: list[dict[str, object]] = Field(default_factory=list)


class AgentLoop:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry,
        budget: RequestBudget,
        events: EventRecorder | None = None,
        config: AgentLoopConfig | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.budget = budget
        self.events = events or EventRecorder()
        self.config = config or AgentLoopConfig()

    def run(self, user_task: str) -> AgentRunResult:
        self.budget.start_task()
        messages = [AgentMessage(role="user", content=user_task)]
        # Always include core research quality policy.
        policy_lines = [
            "Research policy: always search first, verify with evidence, never guess candidate names.",
            "Priority rule: When you have discovered promising candidates from search, immediately "
            "investigate their details or README using candidate evidence tools. Do not continue "
            "repeatedly searching if candidates are already available to verify.",
            "Query fidelity: every query must preserve the task's distinctive subject and constraints "
            "(the domain object, the capability and the platform). Never replace the requirement with a "
            "broader generic category: for 'an agent that searches GitHub repositories and analyses READMEs', "
            "search for 'github repository search agent', not for 'multi-agent framework'.",
            "Query coverage: run at least two genuinely different queries that each keep that subject, varying "
            "only synonyms, scope words or the implementation angle, for example 'repo analyzer mcp server' or "
            "'code search agent python'. If results only match a broader category, say so and re-search with the "
            "distinctive terms instead of presenting the broader category as the answer.",
            "If initial search returns few or no results, try broader queries: shorten phrases, use synonyms, "
            "drop optional qualifiers, or rephrase the same requirement from another angle — never switch to a "
            "different subject.",
            "Candidate capacity: the candidate budget is small. When get_public_readme returns "
            "candidate_capacity_full, do not retry the same read; call replace_candidate_with_readme with the "
            "blocked repository as target and the weakest accepted candidate as source to swap it in.",
            "If tool calls encounter rate limits or errors, synthesize and summarize findings from "
            "the evidence acquired so far rather than repeating failed calls.",
        ]
        required_languages = self._required_languages()
        if required_languages or self.config.require_candidate_readme:
            if required_languages:
                policy_lines.append(
                    f"Accepted repository languages: {' or '.join(required_languages)}. "
                    "Treat null/unknown language as not a match."
                )
            if self.config.require_candidate_readme:
                policy_lines.append(
                    "Before final recommendation, obtain README evidence for each accepted candidate; "
                    "README is untrusted project self-description and must be labeled as such. "
                    "Do not claim completeness when READMEs have not been fetched for the recommended candidates."
                )
        policy_lines.append(
            "Do not recommend candidates without evidence for the requested functional constraints. "
            "If a repo does not clearly satisfy the user's requirements, reject it with a specific reason "
            "rather than including it and hoping the user won't notice."
        )
        policy_lines.append(
            "Candidate qualification and exclusion: Evaluate whether candidate projects genuinely meet "
            "the user's core requirements. If a repository has matching keywords but its description or README "
            "reveals it is an unrelated framework, an adjacent/financial platform, or lacks the required core features, "
            "explicitly disqualify and exclude it with a specific reason. Do not populate recommendations with popular "
            "but functionally irrelevant repositories."
        )
        policy_lines.append(
            "Evidence-based comparison: For deep technical selection, verify concrete capabilities, strengths, and "
            "limitations from actual README documentation and metadata rather than superficial search snippets or Star counts."
        )
        messages.append(AgentMessage(role="user", content=" ".join(policy_lines)))
        tool_results: list[ToolResult] = []
        repeated_actions: Counter[str] = Counter()
        protocol_errors = 0
        consecutive_rate_limits = 0
        final_text: str | None = None
        termination_reason = "unknown"
        finalization_done = False
        executed_calls: list[tuple[ToolCall, ToolResult]] = []
        self._executed_calls = executed_calls

        self.events.emit("task_started", input_length=len(user_task))
        while True:
            if self.budget.feedback_exhausted:
                termination_reason = "feedback_bytes"
                if (
                    not finalization_done
                    and self.config.finalization_reserve_calls > 0
                    and self._has_candidates(tool_results)
                    and self._finalization_call_available()
                ):
                    finalization_done = True
                    return self._finalize(
                        messages, tool_results, final_text, stop_reason=termination_reason
                    )
                self.events.emit("task_terminated", reason=termination_reason, budget=self.budget.snapshot())
                return self._result("terminated", termination_reason, final_text, messages, tool_results)
            if (
                not finalization_done
                and self.config.finalization_reserve_calls > 0
                and self._has_evidence(tool_results)
                and (
                    self._research_budget_boundary_reached()
                    or self._github_budget_boundary_reached()
                    or self._evidence_complete(tool_results, executed_calls)[0]
                )
            ):
                finalization_done = True
                return self._finalize(messages, tool_results, final_text)
            try:
                self.budget.reserve_model_call()
            except BudgetExceeded as exc:
                termination_reason = exc.dimension
                self.events.emit("task_terminated", reason=termination_reason, budget=self.budget.snapshot())
                return self._result("terminated", termination_reason, final_text, messages, tool_results)

            self.events.emit("model_call_started", model_call=self.budget.model_calls_used)
            try:
                response = self.model.complete(messages=list(messages), tools=self.tools.schemas())
            except (ModelCallError, ModelProtocolError) as exc:
                termination_reason = exc.code if isinstance(exc, ModelCallError) else "model_call_error"
                self.events.emit(
                    "task_terminated",
                    reason=termination_reason,
                    error_code=getattr(exc, "code", "model_call_error"),
                    error_category=getattr(exc, "category", "protocol"),
                    error=str(exc),
                )
                return self._result("terminated", termination_reason, final_text, messages, tool_results)
            self.events.emit(
                "model_call_finished",
                has_tool_calls=bool(response.tool_calls),
                tool_call_count=len(response.tool_calls),
                usage=response.usage.model_dump() if response.usage else {"status": "unknown"},
            )

            message = response.as_message()
            if response.content:
                message.content = self._clip_feedback_text(
                    response.content, self.budget, source="model", label="assistant"
                )
                self.budget.record_feedback_size(len(message.content.encode("utf-8")), source="model", label="assistant")

            messages.append(message)
            if not response.tool_calls:
                final_text = response.content
                evidence_ok, evidence_gaps = self._evidence_complete(tool_results, executed_calls)
                if (
                    not finalization_done
                    and self.config.finalization_reserve_calls > 0
                    and self._finalization_call_available()
                    and not evidence_ok
                ):
                    if any("no candidates" in gap for gap in evidence_gaps):
                        prompt = (
                            "No candidates have been discovered. Try broader or alternative search queries "
                            "with different keywords. Consider removing overly restrictive language filters, "
                            "shortening multi-word phrases, or using synonyms before concluding."
                        )
                    else:
                        prompt = (
                            "The evidence is incomplete: " + "; ".join(evidence_gaps) + ". "
                            "Continue to gather the missing evidence before producing a recommendation."
                        )
                    messages.append(AgentMessage(role="user", content=prompt))
                    self.events.emit("evidence_incomplete_continue", gaps=evidence_gaps)
                    continue
                termination_reason = "model_completed"
                self.events.emit("task_completed", reason=termination_reason)
                return self._result("completed", termination_reason, final_text, messages, tool_results)

            should_stop = False
            finalize_after_batch = False
            response_call_ids: set[str] = set()
            for index, call in enumerate(response.tool_calls):
                self.events.emit("tool_call_requested", tool_call_id=call.id, tool_name=call.name)
                signature = self._action_signature(call)
                repeated_actions[signature] += 1
                if call.id in response_call_ids:
                    result = self._blocked_result(call, "duplicate_tool_call_id", "duplicate tool_call_id blocked")
                    should_stop = True
                    termination_reason = "protocol_error"
                elif repeated_actions[signature] > self.config.max_repeated_actions:
                    result = self._blocked_result(call, "repeated_action_blocked", "repeated tool action blocked")
                    should_stop = True
                    termination_reason = "repeated_action_blocked"
                else:
                    result, finalize_after_batch = self._dispatch_batch_call(call, tool_results)
                response_call_ids.add(call.id)
                tool_results.append(result)
                executed_calls.append((call, result))
                messages.append(self._tool_message(call.id, result, self.budget))
                self.events.emit(
                    "tool_finished",
                    tool_call_id=call.id,
                    tool_name=call.name,
                    ok=result.ok,
                    error_code=result.error.code if result.error else None,
                    request_count=result.request_count,
                    result_bytes=len(result.model_dump_json().encode("utf-8")),
                    feedback_bytes=int(self.budget.feedback_records[-1]["bytes"]) if self.budget.feedback_records else 0,
                    feedback_bytes_used=self.budget.feedback_bytes_used,
                )
                self.events.emit("budget_updated", **self.budget.snapshot())

                if result.ok:
                    consecutive_rate_limits = 0
                elif result.error and (result.error.code == "rate_limited" or result.error.category == "rate_limit"):
                    consecutive_rate_limits += 1
                    if consecutive_rate_limits >= self.config.max_rate_limit_retries:
                        should_stop = True
                        termination_reason = "rate_limited"

                if result.error and result.error.category in {"protocol", "input"}:
                    protocol_errors += 1
                    if protocol_errors > self.config.max_protocol_retries:
                        should_stop = True
                        termination_reason = "protocol_retries_exhausted"
                if result.error and result.error.category == "budget":
                    should_stop = True
                    termination_reason = (
                        "research_budget_boundary"
                        if finalize_after_batch
                        else "github_requests"
                        if result.error.code == "github_requests_insufficient"
                        else result.error.code
                    )
                if self.budget.fatal_exhausted_dimensions:
                    should_stop = True
                    termination_reason = self.budget.fatal_exhausted_dimensions[-1]
                if should_stop:
                    blocked_code = (
                        "not_executed_before_finalization"
                        if finalize_after_batch
                        else "not_executed_after_stop"
                    )
                    blocked_message = (
                        "tool was not executed because research stopped before finalization"
                        if finalize_after_batch
                        else "tool was not executed after safe stop"
                    )
                    for remaining in response.tool_calls[index + 1 :]:
                        blocked = self._blocked_result(remaining, blocked_code, blocked_message)
                        tool_results.append(blocked)
                        messages.append(self._tool_message(remaining.id, blocked, self.budget))
                        self.events.emit(
                            "tool_blocked",
                            tool_call_id=remaining.id,
                            tool_name=remaining.name,
                            error_code=blocked_code,
                        )
                    break

            if finalize_after_batch:
                return self._finalize(messages, tool_results, final_text, stop_reason="github_requests")
            if should_stop:
                if (
                    not finalization_done
                    and self.config.finalization_reserve_calls > 0
                    and self._has_candidates(tool_results)
                    and self._finalization_call_available()
                ):
                    finalization_done = True
                    return self._finalize(messages, tool_results, final_text, stop_reason=termination_reason)
                if not termination_reason or termination_reason == "unknown":
                    termination_reason = "safe_stop"
                if termination_reason == "rate_limited" and not final_text:
                    final_text = (
                        "GitHub API rate limit exceeded (HTTP 403). No repositories could be retrieved "
                        "because the unauthenticated rate limit (60 requests/hour) was reached. "
                        "Please configure a GITHUB_TOKEN or wait for the rate limit to reset."
                    )
                self.events.emit("task_terminated", reason=termination_reason, budget=self.budget.snapshot())
                return self._result("terminated", termination_reason, final_text, messages, tool_results)

    def _dispatch_call(self, call: ToolCall) -> ToolResult:
        try:
            self.budget.check_task_time()
        except BudgetExceeded as exc:
            return self._blocked_result(call, exc.dimension, str(exc))

        if not self.tools.candidate_access_allowed(call.name, call.arguments):
            return self._blocked_result(
                call,
                "candidate_not_accepted",
                "candidate capacity is full; evidence access is limited to accepted candidates",
            )
        result = self.tools.dispatch(call.name, call.arguments)
        if result.error and result.error.code in {
            "unknown_tool",
            "invalid_json_arguments",
            "invalid_arguments",
        }:
            self.events.emit(
                "tool_call_rejected",
                tool_call_id=call.id,
                tool_name=call.name,
                error_code=result.error.code,
            )
        else:
            self.events.emit("tool_call_validated", tool_call_id=call.id, tool_name=call.name)
        return result

    def _dispatch_batch_call(
        self,
        call: ToolCall,
        tool_results: list[ToolResult],
    ) -> tuple[ToolResult, bool]:
        """Dispatch one call, stopping a batch before a known request overrun."""

        if not self.tools.candidate_access_allowed(call.name, call.arguments):
            return self._dispatch_call(call), False

        estimated_requests = self.tools.estimated_request_count(call.name, call.arguments)
        if estimated_requests and estimated_requests > self.budget.github_requests_remaining:
            can_finalize = self._has_evidence(tool_results) and self._finalization_call_available()
            return (
                self._blocked_result(
                    call,
                    "github_requests_insufficient",
                    (
                        f"tool requires {estimated_requests} GitHub requests but only "
                        f"{self.budget.github_requests_remaining} remain"
                    ),
                ),
                can_finalize,
            )
        return self._dispatch_call(call), False

    def _research_budget_boundary_reached(self) -> bool:
        if self.budget.max_model_calls is None:
            return False
        research_limit = max(self.budget.max_model_calls - self.config.finalization_reserve_calls, 0)
        return self.budget.model_calls_used >= research_limit

    def _required_languages(self) -> tuple[str, ...]:
        values = list(self.config.required_languages)
        if self.config.required_language:
            values.append(self.config.required_language)
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip()
            key = normalized.casefold()
            if normalized and key not in seen:
                seen.add(key)
                result.append(normalized)
        return tuple(result)

    def _github_budget_boundary_reached(self) -> bool:
        return self.budget.github_requests_remaining == 0

    def _finalization_call_available(self) -> bool:
        if self.config.finalization_reserve_calls <= 0:
            return False
        return (
            self.budget.max_model_calls is None
            or self.budget.model_calls_used < self.budget.max_model_calls
        )

    @staticmethod
    def _has_evidence(tool_results: list[ToolResult]) -> bool:
        evidence_tools = {
            "get_public_repository",
            "get_public_readme",
            "get_repository_license",
            "get_repository_languages_and_topics",
        }
        return any(
            result.ok
            and (
                result.tool_name in evidence_tools
                or result.tool_name == "search_public_repositories"
            )
            for result in tool_results
        )

    @staticmethod
    def _has_candidates(tool_results: list[ToolResult]) -> bool:
        for result in tool_results:
            if not result.ok:
                continue
            data = result.data
            if bool(getattr(data, "items", None)):
                return True
            if getattr(data, "full_name", None) is not None:
                return True
        return False

    def _evidence_complete(
        self,
        tool_results: list[ToolResult],
        executed_calls: list[tuple[ToolCall, ToolResult]],
    ) -> tuple[bool, list[str]]:
        required_languages = self._required_languages()
        required_language_keys = {language.casefold() for language in required_languages}

        search_candidates: dict[str, Any] = {}
        investigated_candidates: dict[str, Any] = {}
        for result in tool_results:
            if not result.ok:
                continue
            data = result.data
            for item in getattr(data, "items", []) or []:
                full_name = getattr(item, "full_name", None)
                if isinstance(full_name, str):
                    search_candidates[full_name.casefold()] = item
            if getattr(data, "full_name", None):
                full_name = getattr(data, "full_name", None)
                if isinstance(full_name, str):
                    investigated_candidates[full_name.casefold()] = data

        if not search_candidates and not investigated_candidates:
            return False, ["no candidates discovered yet — broaden the search or try different keywords"]

        target_candidates: dict[str, Any] = dict(investigated_candidates)
        max_c = self.budget.max_candidates
        for identity, record in search_candidates.items():
            if len(target_candidates) >= max_c:
                break
            if identity not in target_candidates:
                target_candidates[identity] = record

        readme_attempts: set[str] = set()
        readme_available: set[str] = set()
        language_verified: set[str] = set()
        replaced_away: set[str] = set()
        capacity_blocked: set[str] = set()
        for call, result in executed_calls:
            try:
                arguments = json.loads(call.arguments)
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                continue
            identity = f"{arguments.get('owner')}/{arguments.get('repo')}".casefold()
            if call.name == "get_public_readme" and identity in target_candidates:
                readme_attempts.add(identity)
                if result.ok and getattr(result.data, "status", None) in {"available", "truncated"}:
                    readme_available.add(identity)
                elif getattr(getattr(result, "error", None), "code", None) == "candidate_capacity_full":
                    # The candidate was never admitted to the evidence budget,
                    # so it cannot be a gated evidence gap.
                    capacity_blocked.add(identity)
            if call.name == "replace_candidate_with_readme":
                # The replacement returns README evidence for the target and
                # retires the source candidate in a single call.
                source = f"{arguments.get('source_owner')}/{arguments.get('source_repo')}".casefold()
                target = f"{arguments.get('target_owner')}/{arguments.get('target_repo')}".casefold()
                readme_attempts.discard(source)
                readme_available.discard(source)
                if result.ok:
                    replaced_away.add(source)
                readme_attempts.add(target)
                if result.ok and getattr(result.data, "status", None) in {"available", "truncated"}:
                    readme_available.add(target)
            if call.name == "get_public_repository" and result.ok and identity in target_candidates:
                language = getattr(result.data, "language", None)
                if isinstance(language, str) and language.casefold() in required_language_keys:
                    language_verified.add(identity)
            if call.name == "get_repository_languages_and_topics" and result.ok and identity in target_candidates:
                languages = getattr(result.data, "languages", {}) or {}
                if any(
                    isinstance(name, str) and name.casefold() in required_language_keys
                    for name in languages
                ):
                    language_verified.add(identity)

        # A candidate retired by replace_candidate_with_readme is no longer
        # recommended, so it must not keep a stale "README unavailable" gap.
        # The same holds for a candidate the budget never admitted.
        out_of_scope = replaced_away | capacity_blocked
        target_candidates = {
            identity: record for identity, record in target_candidates.items() if identity not in out_of_scope
        }

        gaps: list[str] = []
        for identity, record in target_candidates.items():
            if required_languages:
                language = getattr(record, "language", None)
                if not (
                    isinstance(language, str)
                    and language.casefold() in required_language_keys
                ) and identity not in language_verified:
                    gaps.append(f"language not verified: {identity}")
            if self.config.require_candidate_readme:
                if identity not in readme_attempts:
                    gaps.append(f"README not checked: {identity}")
                elif identity not in readme_available:
                    gaps.append(f"README evidence unavailable: {identity}")
        return not gaps, gaps

    def _finalize(
        self,
        messages: list[AgentMessage],
        tool_results: list[ToolResult],
        final_text: str | None,
        *,
        stop_reason: str | None = None,
    ) -> AgentRunResult:
        try:
            self.budget.reserve_model_call()
        except BudgetExceeded as exc:
            self.events.emit("task_terminated", reason=exc.dimension, budget=self.budget.snapshot())
            return self._result("terminated", exc.dimension, final_text, messages, tool_results)
        prompt_lines = [
            "Produce the final evidence-constrained comparison and recommendation report now.",
            "Do not call tools. Structure your response in clearly labeled sections:",
            "1) Verified Facts & Candidate Verification — for each candidate investigated, list the repository full name, "
            "GitHub URL, verified core capabilities from README/metadata, strengths, and limitations. Clearly indicate its "
            "qualification verdict: Verified Match, Partial Match, or Excluded (with specific reason for rejection);",
            "2) Comparison & Trade-offs — compare the qualifying candidates directly on capabilities, architecture, "
            "and maturity to help the user make an informed technical choice;",
            "3) Evidence Gaps & Unknowns — document what was requested but could not be verified from available tools or READMEs;",
            "4) Final Recommendation — your concrete, evidence-backed recommendation for technical adoption. "
            "If fewer candidates qualify than requested, recommend only qualifying ones and explain why others were excluded. "
            "If no candidates qualify, state this clearly with the search evidence; do not fabricate recommendations.",
        ]
        if stop_reason == "rate_limited":
            prompt_lines.append(
                "Note: Further repository research was stopped due to upstream GitHub API rate limiting. "
                "Synthesize and present findings from the evidence acquired prior to rate limiting, "
                "and clearly document the rate limit in Evidence Gaps."
            )
        elif stop_reason == "repeated_action_blocked":
            prompt_lines.append(
                "Note: Further search was stopped because repeated identical queries were blocked. "
                "Synthesize and present findings from the evidence acquired so far."
            )
        elif stop_reason == "feedback_bytes":
            prompt_lines.append(
                "Note: Further research stopped because the prompt-size budget was exhausted; "
                "some tool results were clipped. Synthesize from the evidence acquired so far and "
                "state the truncation in Evidence Gaps."
            )
        elif stop_reason in {"github_requests", "research_budget_boundary"}:
            prompt_lines.append(
                "Note: Further research reached the request budget boundary. "
                "Synthesize and present findings from the evidence acquired so far."
            )
        final_instruction = AgentMessage(
            role="user",
            content=" ".join(prompt_lines),
        )
        messages.append(final_instruction)
        self.events.emit("finalization_started", model_call=self.budget.model_calls_used, stop_reason=stop_reason)
        try:
            response = self.model.complete(
                messages=list(messages),
                tools=self.tools.schemas(),
                tool_choice="none",
            )
        except ModelCallError as exc:
            self.events.emit("task_terminated", reason=exc.code, error_category=exc.category)
            return self._result("terminated", exc.code, final_text, messages, tool_results)
        except ModelProtocolError:
            self.events.emit("task_terminated", reason="model_call_error", error_category="protocol")
            return self._result("terminated", "model_call_error", final_text, messages, tool_results)
        self.events.emit(
            "finalization_finished",
            has_tool_calls=bool(response.tool_calls),
            usage=response.usage.model_dump() if response.usage else {"status": "unknown"},
        )
        if response.tool_calls:
            self.events.emit("task_terminated", reason="finalization_tool_call")
            return self._result("terminated", "finalization_tool_call", final_text, messages, tool_results)
        if response.content:
            messages.append(response.as_message())
            self.events.emit("task_completed", reason="model_finalized")
            return self._result("completed", "model_finalized", response.content, messages, tool_results)
        self.events.emit("task_terminated", reason="finalization_empty_response")
        return self._result("terminated", "finalization_empty_response", final_text, messages, tool_results)

    @staticmethod
    def _action_signature(call: ToolCall) -> str:
        try:
            arguments = json.dumps(json.loads(call.arguments), sort_keys=True, separators=(",", ":"))
        except json.JSONDecodeError:
            arguments = call.arguments
        return f"{call.name}:{arguments}"

    def _tool_message(self, call_id: str, result: ToolResult, budget: RequestBudget) -> AgentMessage:
        payload = result.model_dump(mode="json")
        payload["_budget"] = budget.snapshot()
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        text = self._clip_feedback_text(text, budget, source="tool", label=result.tool_name)
        budget.record_feedback_size(len(text.encode("utf-8")), source="tool", label=result.tool_name)
        return AgentMessage(
            role="tool",
            tool_call_id=call_id,
            content=text,
        )

    def _clip_feedback_text(
        self,
        text: str,
        budget: RequestBudget,
        *,
        source: str,
        label: str,
    ) -> str:
        """Keep one prompt-bound payload under the per-message byte ceiling."""

        limit = budget.max_feedback_item_bytes
        size = len(text.encode("utf-8"))
        if limit is None or size <= limit:
            return text
        suffix = f"…[truncated: original {size} bytes, limit {limit}]"
        allowance = max(limit - len(suffix.encode("utf-8")), 0)
        kept = text.encode("utf-8")[:allowance].decode("utf-8", "ignore")
        clipped = kept + suffix
        self.events.emit(
            "feedback_clipped",
            source=source,
            label=label,
            original_bytes=size,
            kept_bytes=len(clipped.encode("utf-8")),
        )
        return clipped

    @staticmethod
    def _blocked_result(
        call: ToolCall,
        code: str,
        message: str,
        *,
        request_count: int = 0,
    ) -> ToolResult:
        category = (
            "budget"
            if code in {
                "task_time",
                "feedback_bytes",
                "github_requests_insufficient",
                "not_executed_before_finalization",
            }
            else "safety"
            if code in {"candidate_not_accepted", "candidate_capacity_full"}
            else "protocol"
        )
        return ToolResult(
            tool_name=call.name,
            ok=False,
            error=ToolError(code=code, category=category, message=message),
            request_count=request_count,
        )

    def _result(
        self,
        status: str,
        termination_reason: str,
        final_text: str | None,
        messages: list[AgentMessage],
        tool_results: list[ToolResult],
    ) -> AgentRunResult:
        return AgentRunResult(
            status=status,
            termination_reason=termination_reason,
            final_text=final_text,
            messages=messages,
            tool_results=tool_results,
            events=list(self.events.events),
            budget=self.budget.snapshot(),
            evidence_complete=self._evidence_complete(
                tool_results,
                getattr(self, "_executed_calls", []),
            )[0],
            evidence_gaps=self._evidence_complete(
                tool_results,
                getattr(self, "_executed_calls", []),
            )[1],
            feedback_trace=self.budget.feedback_trace(),
        )
