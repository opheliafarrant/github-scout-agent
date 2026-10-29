"""A small synchronous, feedback-driven Agent Loop for S3-03."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .agent_models import AgentMessage, AssistantResponse, ModelCallError, ModelProtocolError, ToolCall
from .budget import BudgetExceeded, RequestBudget
from .events import AgentEvent, EventRecorder
from .evidence_ledger import EvidenceLedger, RepoEvidence, build_ledger
from .github_models import ToolError, ToolResult
from .report_validator import (
    Completeness,
    Finding,
    _claimed_readme_evidence,
    assess_completeness,
    excluded_repositories,
    validate_report,
)
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
    max_argument_retries: int = 3
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
    investigated_repositories: list[str] = Field(default_factory=list)
    evidence: list[RepoEvidence] = Field(default_factory=list)
    policy_notes: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    requested_options: int | None = None
    completeness: Completeness | None = None

    def flagged(self, code: str) -> tuple[str, ...]:
        """Subjects of the finding with this code, if the run produced one."""

        for finding in self.findings:
            if finding.code == code:
                return finding.subjects
        return ()


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
        self._user_task = ""
        self._verification_nudges = 0
        self._readme_claim_nudges = 0
        self._search_streak_nudges = 0
        self._empty_search_streak = 0

    def run(self, user_task: str) -> AgentRunResult:
        self.budget.start_task()
        self._user_task = user_task
        messages = [AgentMessage(role="user", content=user_task)]
        # Always include core research quality policy.
        policy_lines = [
            "Research policy: always search first, verify with evidence, never guess candidate names.",
            "Priority rule: When you have discovered promising candidates from search, immediately "
            "investigate their details or README using candidate evidence tools. Do not continue "
            "repeatedly searching if candidates are already available to verify.",
            "Search maturity & quality: When searching for production-ready libraries or frameworks, prioritize mature, "
            "community-vetted repositories. Use sort='stars' or query qualifiers (e.g. 'stars:>50') when appropriate. "
            "Avoid filling candidate spots with 0-star personal scratchpads or incomplete toy repositories if established "
            "open-source implementations exist.",
            "Prior knowledge verification: You may use your knowledge of well-known open-source projects (e.g. popular "
            "frameworks) to guide targeted searches or direct lookups, but you must NEVER recommend or declare any "
            "repository as a 'Verified Match' without actually investigating it using tools (such as get_public_repository, "
            "get_known_repository, or get_public_readme) during this session.",
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
            "Evidence-based rejection: reject a candidate when its metadata already proves it cannot meet a hard "
            "requirement (wrong language, wrong platform, a text-layer tool for scanned input). The reason must cite "
            "the concrete field that disqualifies it. This is never permission to skip investigation: if a candidate "
            "might satisfy the requirement, fetch its README before judging it."
        )
        policy_lines.append(
            "Capability queries: when the task describes a capability (a library that can do X for input type Y), "
            "search for the capability, modality and output artifact — never for the name of a library you already "
            "know. A known library is a verification target, not a query; search it by name only once you decide it "
            "is worth verifying."
        )
        policy_lines.append(
            "Never conclude 'no qualifying candidates' while the budget still allows work and no README has been "
            "fetched. An empty conclusion without one inspected candidate is a failure, not an answer: verify at "
            "least one concrete candidate before declaring the search empty."
        )
        policy_lines.append(
            "Investigation depth before an empty answer: one rejected candidate does not prove the ecosystem lacks "
            "options. When the budget still allows it, read READMEs for at least two distinct candidates and try a "
            "second query formulation (capability, platform, or artifact wording) before stating that nothing "
            "qualifies. An empty answer built on a single rejection is a weak conclusion and must be labelled as such."
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
        policy_lines.append(
            "Tables are evidence too: never place a repository you did not inspect with tools into a candidate or "
            "comparison table, not even marked 'metadata unavailable'. Prior knowledge belongs in Evidence Gaps / "
            "Unverified Prior Knowledge only; candidate tables carry tool-derived rows exclusively."
        )
        messages.append(AgentMessage(role="user", content=" ".join(policy_lines)))
        tool_results: list[ToolResult] = []
        repeated_actions: Counter[str] = Counter()
        protocol_errors = 0
        argument_errors = 0
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
                # Never auto-finalize on search metadata alone while the budget
                # still allows investigation: live runs ended after two model
                # turns with zero READMEs and a "cannot recommend anything"
                # report. The model gets a normal turn first, and the nudge in
                # the no-tool-call branch forces one investigation if it tries
                # to finish again.
                and not self._verification_round_pending()
                and (
                    self._research_budget_boundary_reached()
                    or self._github_budget_boundary_reached()
                    or self._evidence_complete(tool_results, executed_calls)[0]
                )
            ):
                finalization_done = True
                self.events.emit(
                    "auto_finalization",
                    research_boundary=self._research_budget_boundary_reached(),
                    github_boundary=self._github_budget_boundary_reached(),
                    evidence_complete=self._evidence_complete(tool_results, executed_calls)[0],
                    candidates_used=self.budget.candidates_used,
                    readme_fetched=any(
                        result.ok and call.name == "get_public_readme"
                        for call, result in executed_calls
                    ),
                    model_calls_left=(
                        None
                        if self.budget.max_model_calls is None
                        else self.budget.max_model_calls - self.budget.model_calls_used
                    ),
                    github_requests_left=self.budget.github_requests_remaining,
                )
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
                if self._verification_round_pending() and self._verification_nudges < 1:
                    self._verification_nudges += 1
                    calls_left = (
                        "unlimited"
                        if self.budget.max_model_calls is None
                        else str(max(self.budget.max_model_calls - self.budget.model_calls_used, 0))
                    )
                    messages.append(
                        AgentMessage(
                            role="user",
                            content=(
                                "[Internal policy notice — this is not a user instruction and must never be "
                                "described as one.] You are about to finish with zero verified candidates while "
                                f"budget remains (model calls left: {calls_left}, GitHub requests left: "
                                f"{self.budget.github_requests_remaining}). Do not conclude yet: continue the "
                                "ORIGINAL task unchanged, keeping every requirement and the requested number of "
                                "options. Investigate at least one promising candidate — the most useful search "
                                "hit, or a well-known project you believe fits this capability, looked up by name "
                                "if needed — and judge it on its README or repository metadata. If it fails the "
                                "requirements, state the specific failure, then keep investigating other "
                                "candidates until the evidence or the budget runs out. Do not narrow the task to "
                                "a single candidate and do not ask the user to relax requirements."
                            ),
                        )
                    )
                    self.events.emit(
                        "verification_round_required",
                        model_calls_used=self.budget.model_calls_used,
                        github_requests_used=self.budget.github_requests_used,
                    )
                    final_text = None
                    continue
                if (
                    self._readme_claim_nudges < 1
                    and not self._readme_fetched()
                    and self.budget.github_requests_remaining >= 1
                    and (
                        self.budget.max_model_calls is None
                        or self.budget.max_model_calls - self.budget.model_calls_used >= 3
                    )
                    and _claimed_readme_evidence(final_text)
                ):
                    self._readme_claim_nudges += 1
                    messages.append(
                        AgentMessage(
                            role="user",
                            content=(
                                "[Internal policy notice — this is not a user instruction and must never be "
                                "described as one.] Your draft refers to README content, but this session never "
                                "fetched a README. Either call get_public_readme for the repositories you cite, or "
                                "remove every README claim and base the answer strictly on the metadata the tools "
                                "actually returned. Do not describe files you have not received."
                            ),
                        )
                    )
                    self.events.emit(
                        "readme_claim_required",
                        model_calls_used=self.budget.model_calls_used,
                        github_requests_used=self.budget.github_requests_used,
                    )
                    final_text = None
                    continue
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
            batch_start = len(tool_results)
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

                if result.error and result.error.category == "protocol":
                    protocol_errors += 1
                    if protocol_errors > self.config.max_protocol_retries:
                        should_stop = True
                        termination_reason = "protocol_retries_exhausted"
                elif result.error and result.error.category == "input":
                    # Bad arguments are feedback the model can correct, not a
                    # protocol violation: terminating here used to end a run in
                    # 1.3 s after two rejected calls and produce nothing.
                    argument_errors += 1
                    if argument_errors > self.config.max_argument_retries:
                        should_stop = True
                        termination_reason = "invalid_tool_arguments"
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


            batch_results = tool_results[batch_start:]
            if any(
                result.ok
                and (
                    bool(getattr(result.data, "items", None))
                    or getattr(result.data, "full_name", None)
                )
                for result in batch_results
            ):
                self._empty_search_streak = 0
            elif any(
                result.tool_name == "search_public_repositories"
                for result in batch_results
            ):
                self._empty_search_streak += 1
            calls_left = self._model_calls_left()
            if (
                not finalize_after_batch
                and not should_stop
                and self._empty_search_streak >= 2
                and self._search_streak_nudges < 1
                and not self._readme_fetched()
                and (calls_left is None or calls_left >= 4)
            ):
                # A live run spent six of twelve model calls re-phrasing queries
                # that all returned zero candidates, then ran out of calls with a
                # metadata-only verdict. Two empty searches in a row mean the
                # query space is exhausted, not that the next synonym will work.
                self._search_streak_nudges += 1
                self._empty_search_streak = 0
                messages.append(
                    AgentMessage(
                        role="user",
                        content=(
                            "[Internal policy notice — this is not a user instruction and must never be "
                            "described as one.] The last two search attempts returned zero candidates. "
                            "Stop re-phrasing the same query. Change strategy, keeping the original "
                            "subject and requirements: either (a) reduce the query to its single core "
                            "term plus one qualifier, or (b) look up a specific project you believe "
                            "fits, by name, with get_public_repository / get_public_readme, and judge it "
                            "on that evidence. Do not repeat a near-identical query, and do not "
                            "conclude that nothing qualifies before at least one candidate has been "
                            "investigated."
                        ),
                    )
                )
                self.events.emit(
                    "search_strategy_change_required",
                    empty_searches=2,
                    model_calls_used=self.budget.model_calls_used,
                    github_requests_used=self.budget.github_requests_used,
                )

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

    def _ledger(self, tool_results: list[ToolResult] | None = None) -> EvidenceLedger:
        """The session evidence account, built from the calls that actually ran.

        Every gate reads this instead of re-deriving "was this repository
        investigated" from tool results in its own way; the derivations drifted
        apart and a report ended up recommending a repository no tool had
        touched.
        """

        return build_ledger(
            tool_results or [],
            getattr(self, "_executed_calls", []),
            self.budget.accepted_candidate_ids,
        )

    def _readme_fetched(self) -> bool:
        return self._ledger().readme_fetched()

    def _model_calls_left(self) -> int | None:
        if self.budget.max_model_calls is None:
            return None
        return max(self.budget.max_model_calls - self.budget.model_calls_used, 0)

    def _verification_round_pending(self) -> bool:
        """True when the model is giving up without inspecting a single candidate."""

        if self.budget.candidates_used > 0:
            return False
        if self._readme_fetched():
            return False
        if self.budget.max_model_calls is not None:
            calls_left = self.budget.max_model_calls - self.budget.model_calls_used
            if calls_left < 3:
                return False
        return self.budget.github_requests_remaining >= 2

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
        report_text: str | None = None,
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

        # A candidate the finished report explicitly excludes was judged out,
        # not recommended: reading its README was an alternative, not an extra
        # obligation. Charging the run for it marked a correct report partial.
        excluded = (
            excluded_repositories(report_text, target_candidates)
            if report_text
            else set()
        )

        gaps: list[str] = []
        for identity, record in target_candidates.items():
            if required_languages:
                language = getattr(record, "language", None)
                if not (
                    isinstance(language, str)
                    and language.casefold() in required_language_keys
                ) and identity not in language_verified:
                    gaps.append(f"language not verified: {identity}")
            if self.config.require_candidate_readme and identity not in excluded:
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
        described = self._ledger(tool_results).whitelist()
        if described:
            prompt_lines.append(
                f"INVESTIGATED CANDIDATES WHITELIST: [{'; '.join(described)}]. "
                "STRICT GROUNDING RULES: "
                "1) You may ONLY declare a repository as 'Verified Match' or 'Partial Match' if it is in the INVESTIGATED CANDIDATES WHITELIST above and supported by actual tool evidence. "
                "2) Any repository NOT in this whitelist was NEVER inspected with tools in this session. You MUST NOT mark uninspected repositories as 'Verified Match' or recommend them as primary verified choices. "
                "3) If you mention well-known projects from pre-trained memory that were not investigated via tools, you MUST explicitly categorize them under Evidence Gaps as 'Unverified Prior Knowledge' and declare that their current capabilities were not verified in this session."
            )
            prompt_lines.append(
                "Maturity grading: report Stars and creation date for every candidate. A candidate with fewer than "
                "50 stars AND created less than 6 months ago is 'Maturity: immature'; list those under a separate "
                "heading 'Immature options (not recommended for production)' and never present one as the primary "
                "recommendation for production use."
            )
            prompt_lines.append(
                "Verdicts need their evidence: label a repository 'Verified Match' only when this session "
                "actually fetched its README. A candidate judged from metadata alone is 'Partial Match "
                "(metadata only)' — say explicitly that its README was not fetched and make no capability "
                "claim beyond the metadata fields that were returned."
            )
            prompt_lines.append(
                "Prior knowledge is not a recommendation: any project you know but did not inspect in this session "
                "must be labelled 'Unverified Prior Knowledge' and must not appear in Final Recommendation as a "
                "choice, nor carry capability, licence or readiness claims."
            )
            prompt_lines.append(
                "If the task asks for more candidates than this whitelist contains or than the candidate budget "
                "allows, state the shortfall explicitly instead of silently presenting fewer."
            )
        else:
            prompt_lines.append(
                "INVESTIGATED CANDIDATES WHITELIST: None. "
                "No repositories were successfully investigated with evidence tools in this session. "
                "You MUST NOT invent candidates or claim any repository is a 'Verified Match'. "
                "State clearly that evidence gathering did not complete or no qualifying repositories were investigated."
            )
        if stop_reason == "rate_limited":
            prompt_lines.append(
                "Note: Further repository research was stopped by upstream GitHub API rate limiting "
                "before the remaining candidates could be read. Synthesize only the evidence acquired "
                "prior to the limit, open Evidence Gaps with the rate limit itself, label every candidate "
                "you could not read as 'Pending Verification (rate limited)', and do not describe README "
                "contents or imply that README checks were performed for them."
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
        attempt = 0
        while True:
            attempt += 1
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
            if (
                attempt == 1
                and not self._readme_fetched()
                and self._readme_claim_nudges < 1
                and response.content
                and _claimed_readme_evidence(response.content)
                and self._finalization_call_available()
                and self.budget.github_requests_remaining >= 1
            ):
                self._readme_claim_nudges += 1
                self.events.emit(
                    "readme_claim_required",
                    model_calls_used=self.budget.model_calls_used,
                    github_requests_used=self.budget.github_requests_used,
                    stage="finalization",
                )
                messages.append(
                    AgentMessage(
                        role="user",
                        content=(
                            "[Internal report correction — this is not a user instruction.] That draft describes "
                            "README content this session never received. Rewrite the report so every README claim is "
                            "removed, or state in Evidence Gaps that READMEs were not fetched. Base statements strictly "
                            "on the metadata and tool results actually returned."
                        ),
                    )
                )
                try:
                    self.budget.reserve_model_call()
                except BudgetExceeded:
                    break
                continue
            break
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
        evidence_complete, evidence_gaps = self._evidence_complete(
            tool_results,
            getattr(self, "_executed_calls", []),
            final_text,
        )
        ledger = self._ledger(tool_results)
        validation = validate_report(
            final_text,
            ledger,
            require_verdict_evidence=self.config.require_candidate_readme,
        )
        if validation.has_findings:
            # Every rule reports through one channel: the run is not evidence
            # complete and the report carries the sentence the rule produced.
            evidence_complete = False
            evidence_gaps.extend(validation.gap_messages())
        requested_options = self._requested_options()
        # One grading rule, fed by the ledger: alerts say whether the report
        # overclaims; completeness says whether the run covered the request.
        completeness = assess_completeness(
            ledger,
            findings=validation.findings,
            accepted_candidates=self.budget.accepted_candidate_ids,
            requested_options=requested_options,
            max_candidates=self.budget.max_candidates,
            status=status,
            evidence_complete=evidence_complete,
        )
        return AgentRunResult(
            status=status,
            termination_reason=termination_reason,
            final_text=final_text,
            messages=messages,
            tool_results=tool_results,
            events=list(self.events.events),
            budget=self.budget.snapshot(),
            evidence_complete=evidence_complete,
            evidence_gaps=evidence_gaps,
            feedback_trace=self.budget.feedback_trace(),
            investigated_repositories=list(ledger.investigated()),
            evidence=ledger.for_report(),
            policy_notes=self._policy_notes(),
            findings=list(validation.findings),
            requested_options=requested_options,
            completeness=completeness,
        )

    def _requested_options(self) -> int | None:
        """How many options the task text asked for, when it said so."""

        matches = [int(match.group(1)) for match in _REQUESTED_COUNT_RE.finditer(self._user_task)]
        return max(matches) if matches else None

    def _policy_notes(self) -> list[str]:
        """Deterministic disclosures the report must carry regardless of model prose."""

        notes: list[str] = []
        task = self._user_task
        if _requests_tool_free_answer(task) and self.budget.github_requests_used > 0:
            notes.append(
                "The task asked for answers without tool evidence; this run applied the evidence-first policy "
                f"instead and spent {self.budget.github_requests_used} GitHub request(s) verifying candidates. "
                "The findings below are tool-grounded, not recalled from memory."
            )
        requested = self._requested_options()
        if requested is not None and requested > self.budget.max_candidates:
            notes.append(
                f"The task asked for {requested} options; the candidate budget compares at most "
                f"{self.budget.max_candidates}. The shortfall is a system limit, not a search failure."
            )
        if _requests_readme_opt_out(task):
            readme_count = sum(
                1 for item in self._ledger().for_report() if item.readme_fetched
            )
            if readme_count:
                notes.append(
                    "The task asked for a metadata-only answer without reading READMEs; this run "
                    f"applied the evidence-first policy instead and fetched {readme_count} README(s) "
                    "to verify capability. Star counts and licences come from metadata; capability "
                    "claims do not."
                )
        years = sorted({int(year) for year in _YEAR_RE.findall(task)})
        stale = [year for year in years if year != datetime.now().year]
        if stale:
            notes.append(
                f"The task references {stale[-1]}; evidence is current as of "
                f"{datetime.now().astimezone().isoformat(timespec='seconds')}, so conclusions describe the "
                f"present, not {stale[-1]}."
            )
        return notes



_REFUSAL_REQUEST_RE = re.compile(
    r"(?:不要|不需要|不用|别|勿)\s*(?:调用工具|使用工具|证据|引用|来源)|凭(?:你的)?知识|无需证据|"
    r"no\s+(?:tool|evidence)|without\s+(?:tools|evidence)|(?:don't|do\s+not)\s+use\s+tools|"
    r"from\s+your\s+knowledge|use\s+your\s+(?:own\s+)?knowledge",
    re.IGNORECASE,
)
_CLAUSE_SPLIT_RE = re.compile(r"[，,。.！!？?；;\n]+")
_TOOL_AVOIDANCE_NEGATIONS = (
    "不要", "不需要", "不用", "别", "勿", "无需", "禁止", "不必",
    "never", "don't", "do not", "no need", "without", "skip",
)
_TOOL_EVIDENCE_NOUNS = (
    "工具", "检索", "搜索", "证据", "来源", "核实", "调用",
    "tool", "tools", "evidence", "search", "verify", "verification", "citation", "citations",
)


def _requests_tool_free_answer(task: str | None) -> bool:
    """Detect 'answer without calling tools / without evidence' across phrasings.

    The ordered-phrase regex missed '不需要你调用任何工具' because a pronoun and a
    verb sat between the negation and the noun; the report then silently overrode
    the request instead of disclosing it. Clause-level co-occurrence is robust to
    filler words while still requiring the negation and the tool noun to be
    adjacent thoughts.
    """

    if not task:
        return False
    if _REFUSAL_REQUEST_RE.search(task):
        return True
    for clause in _CLAUSE_SPLIT_RE.split(task.casefold()):
        if not clause:
            continue
        if not any(token in clause for token in _TOOL_AVOIDANCE_NEGATIONS):
            continue
        if any(noun in clause for noun in _TOOL_EVIDENCE_NOUNS):
            return True
    return False


_README_OPT_OUT_RE = re.compile(
    r"只看\s*元数据|只给\s*元数据|仅需\s*元数据|只要有?\s*元数据|"
    r"metadata\s+only|skip\s+(?:the\s+)?readme",
    re.IGNORECASE,
)
_README_NOUNS = ("readme", "自述文件")
_README_OPT_OUT_FILLERS = frozenset(
    "读看打开抓取获取调用分析去再自行直接你的请把我帮 "
)


def _requests_readme_opt_out(task: str | None) -> bool:
    """Detect 'metadata is enough, do not read the README'.

    A live run answered this by fetching three READMEs -- the right call, since
    metadata cannot prove capability -- and disclosed nothing about overriding
    the request, so the report read as if it had honoured it. The negation has
    to sit on the README noun, or 'do not recommend what the README omits'
    would be read as an opt-out.
    """

    if not task:
        return False
    lowered = task.casefold()
    if _README_OPT_OUT_RE.search(lowered):
        return True
    for clause in _CLAUSE_SPLIT_RE.split(lowered):
        if not clause:
            continue
        for noun in _README_NOUNS:
            index = clause.find(noun)
            if index < 0:
                continue
            head = clause[:index]
            negation: tuple[int, str] | None = None
            for token in _TOOL_AVOIDANCE_NEGATIONS:
                position = head.rfind(token)
                if position >= 0 and (negation is None or position > negation[0]):
                    negation = (position, token)
            if negation is None:
                continue
            gap = head[negation[0] + len(negation[1]) :]
            if all(char in _README_OPT_OUT_FILLERS for char in gap):
                return True
    return False


_REQUESTED_COUNT_RE = re.compile(
    r"(\d{1,3})\s*(?:个|款|条|projects?|repositories|repos?|candidates?|libraries|tools?|options?)",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
