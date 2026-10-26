"""Deterministic Mock LLMs for offline Agent Loop verification."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from .agent_models import AgentMessage, AssistantResponse, ModelCallError, ToolCall


class ScriptedMockLLM:
    """Return scripted responses; no network or model SDK is involved."""

    def __init__(self, responses: Iterable[AssistantResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages: list[AgentMessage], tools: list[dict[str, Any]]) -> AssistantResponse:
        del messages, tools
        if self.calls >= len(self.responses):
            raise ModelCallError("mock response script exhausted")
        response = self.responses[self.calls]
        self.calls += 1
        return response


class ScenarioMockLLM:
    """Rule-based mock that changes its next action from tool feedback."""

    def __init__(
        self,
        scenario: str = "empty_rewrite",
        *,
        initial_query: str | None = None,
        repository: tuple[str, str] = ("octocat", "Hello-World"),
    ) -> None:
        self.scenario = scenario
        self.initial_query = initial_query
        self.repository = repository
        self.calls = 0
        self.history: list[list[AgentMessage]] = []

    def complete(self, messages: list[AgentMessage], tools: list[dict[str, Any]]) -> AssistantResponse:
        del tools
        self.calls += 1
        self.history.append(list(messages))
        if self.scenario == "direct_complete":
            return AssistantResponse(content="Mock task complete.", finish_reason="stop")
        if self.scenario == "invalid_tool":
            return AssistantResponse(
                tool_calls=[ToolCall(id=f"invalid-{self.calls}", name="unknown_tool", arguments="{}")],
                finish_reason="tool_calls",
            )
        if self.scenario == "repeat_tool":
            return self._search_call("repeat", self.calls)

        tool_messages = [message for message in messages if message.role == "tool"]
        if not tool_messages:
            if self.scenario == "repository_followup":
                return self._repository_call(self.repository, self.calls)
            query = self.initial_query or ("empty query" if self.scenario == "empty_rewrite" else "agent")
            return self._search_call(query, self.calls)

        last = self._tool_payload(tool_messages[-1])
        if self.scenario == "empty_rewrite" and self._search_items(last) == 0:
            return self._search_call("broader open source agent", self.calls)
        if self.scenario in {"readme_followup", "malicious_readme"} and self._search_items(last) == 0:
            return AssistantResponse(content="Mock found no candidates and stopped safely.", finish_reason="stop")
        if self.scenario == "readme_followup" and last.get("tool_name") == "search_public_repositories":
            return self._readme_call(self._first_repo(last), self.calls)
        if self.scenario == "malicious_readme" and last.get("tool_name") == "search_public_repositories":
            return self._readme_call(self._first_repo(last), self.calls)
        if self.scenario == "repository_followup" and last.get("tool_name") == "get_public_repository":
            return self._readme_call({"full_name": f"{self.repository[0]}/{self.repository[1]}"}, self.calls)
        if self.scenario == "license_followup" and last.get("tool_name") == "search_public_repositories":
            return self._license_call(self._first_repo(last), self.calls)
        if self.scenario == "archive_complete":
            return AssistantResponse(content="Mock preserved the archived state and stopped.", finish_reason="stop")
        if self.scenario == "malicious_readme" and last.get("tool_name") == "get_public_readme":
            return AssistantResponse(content="Mock treated README as untrusted evidence and stopped.", finish_reason="stop")
        return AssistantResponse(content="Mock task complete after tool feedback.", finish_reason="stop")

    @staticmethod
    def _tool_payload(message: AgentMessage) -> dict[str, Any]:
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _search_items(payload: dict[str, Any]) -> int:
        data = payload.get("data")
        return len(data.get("items", [])) if isinstance(data, dict) and isinstance(data.get("items"), list) else 0

    @staticmethod
    def _first_repo(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("items"), list) and data["items"]:
            first = data["items"][0]
            return first if isinstance(first, dict) else {}
        return {}

    @staticmethod
    def _search_call(query: str, sequence: int) -> AssistantResponse:
        return AssistantResponse(
            tool_calls=[
                ToolCall(
                    id=f"search-{sequence}",
                    name="search_public_repositories",
                    arguments=json.dumps({"query": query}),
                )
            ],
            finish_reason="tool_calls",
        )

    def _repository_call(self, repository: tuple[str, str], sequence: int) -> AssistantResponse:
        owner, name = repository
        return AssistantResponse(
            tool_calls=[
                ToolCall(
                    id=f"repository-{sequence}",
                    name="get_public_repository",
                    arguments=json.dumps({"owner": owner, "repo": name}),
                )
            ],
            finish_reason="tool_calls",
        )

    @staticmethod
    def _readme_call(repo: dict[str, Any], sequence: int) -> AssistantResponse:
        owner, name = str(repo.get("full_name", "octo/demo")).split("/", 1)
        return AssistantResponse(
            tool_calls=[
                ToolCall(
                    id=f"readme-{sequence}",
                    name="get_public_readme",
                    arguments=json.dumps({"owner": owner, "repo": name}),
                )
            ],
            finish_reason="tool_calls",
        )

    @staticmethod
    def _license_call(repo: dict[str, Any], sequence: int) -> AssistantResponse:
        owner, name = str(repo.get("full_name", "octo/demo")).split("/", 1)
        return AssistantResponse(
            tool_calls=[
                ToolCall(
                    id=f"license-{sequence}",
                    name="get_repository_license",
                    arguments=json.dumps({"owner": owner, "repo": name}),
                )
            ],
            finish_reason="tool_calls",
        )
