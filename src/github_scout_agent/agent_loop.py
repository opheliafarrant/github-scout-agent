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
    def complete(self, messages: list[AgentMessage], tools: list[dict[str, Any]]) -> AssistantResponse:
        ...


@dataclass(frozen=True)
class AgentLoopConfig:
    max_protocol_retries: int = 1
    max_repeated_actions: int = 1


class AgentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    termination_reason: str
    final_text: str | None = None
    messages: list[AgentMessage] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    events: list[AgentEvent] = Field(default_factory=list)
    budget: dict[str, object]


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
        tool_results: list[ToolResult] = []
        repeated_actions: Counter[str] = Counter()
        protocol_errors = 0
        final_text: str | None = None
        termination_reason = "unknown"

        self.events.emit("task_started", input_length=len(user_task))
        while True:
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

            if response.content:
                try:
                    self.budget.record_feedback_size(len(response.content.encode("utf-8")))
                except BudgetExceeded as exc:
                    termination_reason = exc.dimension
                    self.events.emit("task_terminated", reason=termination_reason, budget=self.budget.snapshot())
                    return self._result("terminated", termination_reason, final_text, messages, tool_results)

            messages.append(response.as_message())
            if not response.tool_calls:
                final_text = response.content
                termination_reason = "model_completed"
                self.events.emit("task_completed", reason=termination_reason)
                return self._result("completed", termination_reason, final_text, messages, tool_results)

            should_stop = False
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
                    result = self._dispatch_call(call)
                response_call_ids.add(call.id)
                tool_results.append(result)
                messages.append(self._tool_message(call.id, result))
                self.events.emit(
                    "tool_finished",
                    tool_call_id=call.id,
                    tool_name=call.name,
                    ok=result.ok,
                    error_code=result.error.code if result.error else None,
                    request_count=result.request_count,
                )
                self.events.emit("budget_updated", **self.budget.snapshot())

                if result.error and result.error.category in {"protocol", "input"}:
                    protocol_errors += 1
                    if protocol_errors > self.config.max_protocol_retries:
                        should_stop = True
                        termination_reason = "protocol_retries_exhausted"
                if result.error and result.error.category == "budget":
                    should_stop = True
                    termination_reason = result.error.code
                if self.budget.fatal_exhausted_dimensions:
                    should_stop = True
                    termination_reason = self.budget.fatal_exhausted_dimensions[-1]
                if should_stop:
                    for remaining in response.tool_calls[index + 1 :]:
                        blocked = self._blocked_result(remaining, "not_executed_after_stop", "tool was not executed after safe stop")
                        tool_results.append(blocked)
                        messages.append(self._tool_message(remaining.id, blocked))
                        self.events.emit("tool_blocked", tool_call_id=remaining.id, tool_name=remaining.name)
                    break

            if should_stop:
                if not termination_reason or termination_reason == "unknown":
                    termination_reason = "safe_stop"
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
        feedback = result.model_dump_json()
        try:
            self.budget.record_feedback_size(len(feedback.encode("utf-8")))
        except BudgetExceeded as exc:
            return self._blocked_result(call, "feedback_bytes", str(exc), request_count=result.request_count)
        return result

    @staticmethod
    def _action_signature(call: ToolCall) -> str:
        try:
            arguments = json.dumps(json.loads(call.arguments), sort_keys=True, separators=(",", ":"))
        except json.JSONDecodeError:
            arguments = call.arguments
        return f"{call.name}:{arguments}"

    @staticmethod
    def _tool_message(call_id: str, result: ToolResult) -> AgentMessage:
        return AgentMessage(
            role="tool",
            tool_call_id=call_id,
            content=result.model_dump_json(),
        )

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
            if code in {"task_time", "feedback_bytes"}
            else "safety"
            if code == "candidate_not_accepted"
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
        )
