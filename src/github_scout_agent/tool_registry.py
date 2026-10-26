"""Fixed, schema-validated read-only tool registry."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .budget import BudgetExceeded
from .github_api import GithubApiError, GitHubRestClient, MAX_SEARCH_PER_PAGE, validate_owner_repo_part
from .github_models import ToolError, ToolResult


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=256)
    sort: Literal["best_match", "stars", "forks", "help-wanted-issues", "updated"] = "best_match"
    order: Literal["asc", "desc"] = "desc"
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=MAX_SEARCH_PER_PAGE, ge=1, le=MAX_SEARCH_PER_PAGE)

    @field_validator("query")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("query cannot contain control characters")
        return value


class RepositoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str
    repo: str

    @field_validator("owner", "repo")
    @classmethod
    def validate_repository_part(cls, value: str, info: Any) -> str:
        return validate_owner_repo_part(value, info.field_name)


class ReadmeArguments(RepositoryArguments):
    ref: str | None = Field(default=None, max_length=256)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: Callable[[BaseModel], Any]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments_model.model_json_schema(),
                "strict": True,
            },
        }


class ToolRegistry:
    """A code-defined registry; model input can select, never register, tools."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"tool already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def schemas(self) -> list[dict[str, Any]]:
        return [definition.schema() for definition in self._definitions.values()]

    def estimated_request_count(self, name: str, arguments: str | dict[str, Any]) -> int:
        """Return the bounded GitHub request cost for a valid tool call.

        Invalid or unknown calls return zero so normal protocol validation still
        happens in ``dispatch`` and cannot be hidden by a budget preflight.
        """

        definition = self._definitions.get(name)
        if definition is None:
            return 0
        if isinstance(arguments, str):
            try:
                raw_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return 0
        else:
            raw_arguments = arguments
        if not isinstance(raw_arguments, dict):
            return 0
        try:
            definition.arguments_model.model_validate(raw_arguments)
        except ValidationError:
            return 0
        if name == "get_repository_languages_and_topics":
            return 2
        return 1

    def candidate_access_allowed(self, name: str, arguments: str | dict[str, Any]) -> bool:
        """When candidate capacity is full, allow evidence only for accepted IDs."""

        if name not in {
            "get_public_repository",
            "get_public_readme",
            "get_repository_license",
            "get_repository_languages_and_topics",
        }:
            return True
        definition = self._definitions.get(name)
        client = getattr(definition.handler, "client", None) if definition else None
        if client is None or not client.budget.candidates_full:
            return True
        raw_arguments = arguments
        if isinstance(arguments, str):
            try:
                raw_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return True
        if not isinstance(raw_arguments, dict):
            return True
        try:
            validated = definition.arguments_model.model_validate(raw_arguments)
        except ValidationError:
            return True
        values = validated.model_dump()
        identity = f"{values['owner']}/{values['repo']}"
        return client.budget.is_candidate_accepted(identity)

    def dispatch(self, name: str, arguments: str | dict[str, Any]) -> ToolResult:
        definition = self._definitions.get(name)
        if definition is None:
            return self._failure(name, "unknown_tool", "tool name is not registered", "protocol")
        if isinstance(arguments, str):
            try:
                raw_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return self._failure(name, "invalid_json_arguments", "tool arguments are not valid JSON", "protocol")
        else:
            raw_arguments = arguments
        if not isinstance(raw_arguments, dict):
            return self._failure(name, "invalid_arguments", "tool arguments must be a JSON object", "input")
        try:
            validated = definition.arguments_model.model_validate(raw_arguments)
        except ValidationError as exc:
            return self._failure(name, "invalid_arguments", self._safe_validation_message(exc), "input")
        client_before = getattr(definition.handler, "client", None)
        before = client_before.budget.github_requests_used if client_before else None
        try:
            data = definition.handler(validated)
        except GithubApiError as exc:
            return ToolResult(
                tool_name=name,
                ok=False,
                error=ToolError(
                    code=exc.code,
                    category=exc.category,
                    message=exc.message,
                    retryable=exc.retryable,
                    status_code=exc.status_code,
                    attempts=exc.attempts,
                ),
                request_count=self._request_delta(client_before, before),
            )
        except BudgetExceeded as exc:
            return ToolResult(
                tool_name=name,
                ok=False,
                error=ToolError(code="budget_exceeded", category="budget", message=str(exc)),
                request_count=self._request_delta(client_before, before),
            )
        return ToolResult(
            tool_name=name,
            ok=True,
            data=data,
            request_count=self._request_delta(client_before, before),
        )

    @staticmethod
    def _request_delta(client: Any, before: int | None) -> int:
        if client is None or before is None:
            return 0
        return client.budget.github_requests_used - before

    @staticmethod
    def _safe_validation_message(error: ValidationError) -> str:
        first = error.errors()[0] if error.errors() else {}
        location = ".".join(str(item) for item in first.get("loc", ()))
        return f"invalid tool argument: {location or 'arguments'}"

    @staticmethod
    def _failure(name: str, code: str, message: str, category: str) -> ToolResult:
        return ToolResult(
            tool_name=name,
            ok=False,
            error=ToolError(code=code, category=category, message=message),
        )


class _BoundHandler:
    def __init__(self, client: GitHubRestClient, method: Callable[..., Any], argument_names: tuple[str, ...]) -> None:
        self.client = client
        self._method = method
        self._argument_names = argument_names

    def __call__(self, arguments: BaseModel) -> Any:
        values = arguments.model_dump()
        return self._method(**{name: values[name] for name in self._argument_names})


def build_github_tool_registry(client: GitHubRestClient) -> ToolRegistry:
    """Build exactly the five S3-02 public read-only tools."""

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="search_public_repositories",
        description="Search public GitHub repositories with bounded filters.",
        arguments_model=SearchArguments,
        handler=_BoundHandler(client, client.search_public_repositories, ("query", "sort", "order", "page", "per_page")),
    ))
    registry.register(ToolDefinition(
        name="get_public_repository",
        description="Read public GitHub repository metadata.",
        arguments_model=RepositoryArguments,
        handler=_BoundHandler(client, client.get_public_repository, ("owner", "repo")),
    ))
    registry.register(ToolDefinition(
        name="get_public_readme",
        description="Read bounded, untrusted README content for a public repository.",
        arguments_model=ReadmeArguments,
        handler=_BoundHandler(client, client.get_public_readme, ("owner", "repo", "ref")),
    ))
    registry.register(ToolDefinition(
        name="get_repository_license",
        description="Read GitHub-identified license evidence for a public repository.",
        arguments_model=RepositoryArguments,
        handler=_BoundHandler(client, client.get_repository_license, ("owner", "repo")),
    ))
    registry.register(ToolDefinition(
        name="get_repository_languages_and_topics",
        description="Read repository language byte counts and topics.",
        arguments_model=RepositoryArguments,
        handler=_BoundHandler(client, client.get_repository_languages_and_topics, ("owner", "repo")),
    ))
    return registry
